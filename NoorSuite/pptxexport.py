"""PowerPoint (.pptx) export of a Beamer presentation.

The frames of the LaTeX source are read into simple blocks (bullet lists with levels, paragraphs,
pictures from sheets / figure files / ``\\includegraphics``, tables, two columns, the footer source
line, speaker notes) and written as *native, editable* PowerPoint slides with ``python-pptx``:

* with an imported corporate template (``~/.noorsuite/presentations/<theme>/reference.pptx``) the
  original master and layouts are reused, so the talk carries the real design -- title slide in the
  title layout, content slides in "Title and Content" / "Two Content";
* without one, PowerPoint's default design (16:10) is used.

Math is approximated as plain Unicode text (Greek letters, super- / subscripts, fractions as a/b);
anything that cannot be mapped is reported in the notes. Pure text processing + python-pptx; the
caller supplies PNG renderers for sheets and figure files.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from . import latex, pptximport

_COMMENT = re.compile(r"(?<!\\)%.*")

# --------------------------------------------------------------------------- text -> Unicode
_GREEK = {n: c for n, c in zip(
    "alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa lambda mu nu xi "
    "pi rho sigma tau upsilon phi varphi chi psi omega Gamma Delta Theta Lambda Xi Pi Sigma Phi Psi "
    "Omega".split(),
    "α β γ δ ε ε ζ η θ ϑ ι κ λ μ ν ξ π ρ σ τ υ φ φ χ ψ ω Γ Δ Θ Λ Ξ Π Σ Φ Ψ Ω".split())}
_SYMBOLS = {"times": "×", "cdot": "·", "pm": "±", "mp": "∓", "approx": "≈", "leq": "≤", "le": "≤",
            "geq": "≥", "ge": "≥", "neq": "≠", "rightarrow": "→", "to": "→", "leftarrow": "←",
            "Rightarrow": "⇒", "infty": "∞", "degree": "°", "circ": "°", "partial": "∂", "nabla": "∇",
            "sim": "∼", "propto": "∝", "ll": "≪", "gg": "≫", "langle": "⟨", "rangle": "⟩",
            "ldots": "…", "dots": "…", "cdots": "⋯", "sum": "Σ", "int": "∫", "AA": "Å",
            "textdegree": "°", "S": "§", "ell": "ℓ", "hbar": "ħ", "sqrt": "√", "in": "∈",
            "perp": "⊥", "parallel": "∥", "equiv": "≡", "forall": "∀", "exists": "∃"}
_SUP = str.maketrans("0123456789+-=()ni", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱ")
_SUB = str.maketrans("0123456789+-=()aehijklmnoprstuvx", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓ")


def _group(text: str, i: int):
    """The ``{...}`` group at ``text[i]`` -> (content, end) or None."""
    if i >= len(text) or text[i] != "{":
        return None
    depth = 0
    for j in range(i, len(text)):
        c = text[j]
        if c == "\\":
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[i + 1:j], j + 1
    return None

# --------------------------------------------------------------------------- overlays (effects)
# Beamer overlay commands are turned into private marker characters first; the block parser then
# reads them in document order and gives every list item / paragraph / picture / table a *step*
# (0 = there from the start, k = appears with the k-th click).
_MARK = re.compile("\x02|\x05|\x07|\x03\\d+\x04|\x06\\d+\x04")
_TRANS = {"fade": "fade", "dissolve": "dissolve", "wipe": "wipe", "split": "split",
          "blinds": "blinds", "uncover": "pull", "cover": "cover", "glitter": "fade",
          "box": "wipe", "replace": "fade"}
_TRANS_RE = re.compile(r"\\trans(fade|dissolve|wipe|split[a-z]*|blinds[a-z]*|uncover|glitter|box|"
                       r"replace)\b\s*(?:\[[^\]]*\])?")
_ENV_NAMES = r"(?:itemize|enumerate|description|block|alertblock|exampleblock|column|columns|" \
             r"center|figure|tabular)"


def _spec_step(spec: str):
    """Overlay specification -> ``"inc"`` (``+``), a step number, or None."""
    spec = spec.strip()
    if spec.startswith("+"):
        return "inc"
    m = re.match(r"(\d+)", spec)
    return int(m.group(1)) if m else None


def _match_end(text: str, begin: int, name: str):
    """(start, end) of the ``\\end{name}`` that closes the ``\\begin{name}`` at ``begin``."""
    depth = 0
    for t in re.finditer(r"\\begin\{" + re.escape(name) + r"\}|\\end\{" + re.escape(name) + r"\}",
                         text[begin:]):
        depth += 1 if t.group(0).startswith("\\begin") else -1
        if depth == 0:
            return begin + t.start(), begin + t.end()
    return None


def _incremental_items(inner: str) -> str:
    """``\\x02`` in front of every top-level ``\\item`` (the ``[<+->]`` list option)."""
    out, depth = [], 0
    for tok in re.split(r"(\\begin\{(?:itemize|enumerate|description)\}|"
                        r"\\end\{(?:itemize|enumerate|description)\}|\\item\b)", inner):
        if tok.startswith("\\begin"):
            depth += 1
        elif tok.startswith("\\end"):
            depth -= 1
        out.append(tok)
        if tok == "\\item" and depth == 0:
            out.append("\x07")
    return "".join(out)


def _overlays(text: str) -> str:
    """Overlay commands -> marker characters (see :data:`_MARK`)."""
    # \begin{itemize}[<+->] : every top-level item is one click
    pat = re.compile(r"\\begin\{(itemize|enumerate|description)\}\s*\[\s*<\s*\+[^\]]*\]")
    while True:
        m = pat.search(text)
        if not m:
            break
        head = "\\begin{" + m.group(1) + "}"
        end = _match_end(text, m.start(), m.group(1))
        if end is None:
            text = text[:m.start()] + head + text[m.end():]
            continue
        text = (text[:m.start()] + head + _incremental_items(text[m.end():end[0]])
                + text[end[0]:])
    text = re.sub(r"(\\begin\{(?:itemize|enumerate|description)\})\s*\[\s*<[^\]]*>\s*\]", r"\1", text)
    # \item<2-> / \item<+->
    def item(m):
        k = _spec_step(m.group(1))
        if k == "inc":
            return "\\item\x07"
        return "\\item" + (f"\x03{k}\x04" if k else "")
    text = re.sub(r"\\item\s*<([^<>]*)>", item, text)
    # \uncover<2->{...} \only<2>{...} \visible<2->{...} \onslide<2->
    out, pos = [], 0
    for m in re.finditer(r"\\(?:uncover|only|visible|onslide|temporal)\s*<([^<>]*)>", text):
        if m.start() < pos:
            continue
        out.append(text[pos:m.start()])
        k = _spec_step(m.group(1))
        g = _group(text, m.end()) if m.end() < len(text) and text[m.end()] == "{" else None
        scoped = bool(k)
        if k == "inc":
            out.append("\x07")
            k, scoped = None, True
        if g is not None:
            out.append((f"\x03{k}\x04" if k else "") + g[0] + ("\x05" if scoped else ""))
            pos = g[1]
        else:
            out.append(f"\x03{k}\x04" if k else "")
            pos = m.end()
    out.append(text[pos:])
    text = "".join(out)
    # \begin{block}<2-> ... \end{block}: the whole environment appears with that step
    pat = re.compile(r"\\begin\{(" + _ENV_NAMES + r")\}\s*<([^<>]*)>")
    while True:
        m = pat.search(text)
        if not m:
            break
        k = _spec_step(m.group(2))
        end = _match_end(text, m.start(), m.group(1))
        body = "\\begin{" + m.group(1) + "}"
        if end is None or k is None:
            text = text[:m.start()] + body + text[m.end():]
            continue
        lead = "\x07" if k == "inc" else f"\x03{k}\x04"
        text = (text[:m.start()] + lead + body + text[m.end():end[1]] + "\x05"
                + text[end[1]:])
    # \pause, \pause[3]
    text = re.sub(r"\\pause\b\s*\[(\d+)\]", lambda m: f"\x06{m.group(1)}\x04", text)
    return re.sub(r"\\pause\b", "\x02", text)


class _Steps:
    """Running appearance step while the blocks of a frame are read in order."""

    def __init__(self):
        self.n = 1                       # Beamer's overlay counter (slide number; the + in <+->)
        self.hide = 0                    # click index from which everything after a pause shows
        self.floors: list = []           # scoped steps: item<2->, uncover<3->{..}

    def current(self) -> int:
        return self.floors[-1] if self.floors else self.hide

    def feed(self, text: str):
        """Consume the markers of ``text`` -> (clean text, step of its first visible content)."""
        out, first, pos = [], None, 0

        def seg(piece):
            nonlocal first
            if piece.strip() and first is None:
                first = self.current()
            out.append(piece)

        for m in _MARK.finditer(text):
            seg(text[pos:m.start()])
            tok = m.group(0)
            if tok == "\x02":
                self.n += 1
                self.hide = self.n - 1
            elif tok == "\x07":
                self.floors.append(self.n - 1)
                self.n += 1
            elif tok == "\x05":
                if self.floors:
                    self.floors.pop()
            elif tok[0] == "\x03":
                self.floors.append(max(0, int(tok[1:-1]) - 1))
            else:
                self.n = int(tok[1:-1])
                self.hide = self.n - 1
            pos = m.end()
        seg(text[pos:])
        return "".join(out), (first if first is not None else self.current())


def _max_step(blocks) -> int:
    top = 0
    for b in blocks:
        if b[0] == "list":
            top = max([top] + list(b[2]))
        elif b[0] == "columns":
            for col in b[1]:
                top = max(top, _max_step(col))
        elif b[0] == "text" and len(b) > 2:
            top = max(top, b[2])
        elif b[0] == "picture" and len(b) > 4:
            top = max(top, b[4])
        elif b[0] == "table" and len(b) > 2:
            top = max(top, b[2])
    return top


def page_map(parsed: dict, page_count: int) -> list:
    """Per PDF page (0-based) ``(frame index, step, steps of that frame)``; falls back to one page
    per frame when the counts do not add up (``[plain]`` frames, ``allowframebreaks`` ...)."""
    frames = parsed["frames"]
    out = []
    for i, fr in enumerate(frames):
        n = int(fr.get("steps", 1)) if fr.get("kind") != "title" else 1
        out.extend((i, k, n) for k in range(n))
    if len(out) == page_count:
        return out
    return [(min(p, max(0, len(frames) - 1)), 0, 1) for p in range(page_count)]


def _tail(step: int) -> tuple:
    return (step,) if step > 0 else ()


def transition_of(text: str):
    """The first ``\\trans...`` command of ``text`` -> kind ("fade", "wipe", ...) or None."""
    m = _TRANS_RE.search(text)
    if not m:
        return None
    word = m.group(1)
    for k in ("split", "blinds"):
        if word.startswith(k):
            return k
    return word



def _math(m: str) -> str:
    m = re.sub(r"\\(?:mathrm|mathbf|mathit|text|textrm|mathsf|boldsymbol)\s*\{([^{}]*)\}", r"\1", m)
    m = re.sub(r"\\frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}", r"(\1)/(\2)", m)
    m = re.sub(r"\\sqrt\s*\{([^{}]*)\}", r"√(\1)", m)
    m = re.sub(r"\\([A-Za-z]+)", lambda x: _GREEK.get(x.group(1), _SYMBOLS.get(x.group(1), "")), m)
    m = re.sub(r"\^\{([^{}]*)\}", lambda x: x.group(1).translate(_SUP), m)
    m = re.sub(r"\^([A-Za-z0-9+\-])", lambda x: x.group(1).translate(_SUP), m)
    m = re.sub(r"_\{([^{}]*)\}", lambda x: x.group(1).translate(_SUB), m)
    m = re.sub(r"_([A-Za-z0-9])", lambda x: x.group(1).translate(_SUB), m)
    return re.sub(r"[{}\\]", "", m).strip()


def plain(text: str) -> str:
    """LaTeX running text -> readable Unicode text for a slide."""
    t = re.sub(r"\$\$(.*?)\$\$|\$(.*?)\$|\\\((.*?)\\\)", lambda m: _math(
        next(g for g in m.groups() if g is not None)), text, flags=re.S)
    for _ in range(3):                     # \textbf{\emph{x}} etc.
        t = re.sub(r"\\(?:textbf|textit|emph|textsf|texttt|textrm|textsc|underline|alert|"
                   r"textcolor\{[^}]*\}|href\{[^}]*\})\s*\{([^{}]*)\}", r"\1", t)
    t = re.sub(r"\\textsuperscript\{([^{}]*)\}", lambda m: m.group(1).translate(_SUP), t)
    t = t.replace("---", "—").replace("--", "–").replace("~", "\u00a0")
    t = re.sub(r"\\[,;:! ]", " ", t)
    t = re.sub(r"\\(&|%|#|_|\$|\{|\})", r"\1", t)
    t = re.sub(r"\\\\(\[[^\]]*\])?", "\n", t)
    t = re.sub(r"\\(?:centering|raggedright|pause|vfill|hfill|noindent|small|footnotesize|"
               r"scriptsize|large|Large|LARGE|bfseries|itshape|normalsize)\b", "", t)
    t = re.sub(r"\\(?:vspace|hspace)\*?\{[^}]*\}", "", t)
    t = re.sub(r"\\(?:label|cite[a-z]*|ref)\*?(?:\[[^\]]*\])?\{[^}]*\}", "", t)
    t = re.sub(r"\\[A-Za-z]+\*?(?:\[[^\]]*\])?\{([^{}]*)\}", r"\1", t)
    t = re.sub(r"\\[A-Za-z]+\*?", "", t)
    t = re.sub(r"[{}]", "", t)
    return re.sub(r"[ \t]+", " ", t).strip()


# --------------------------------------------------------------------------- frame parsing
_FRAME = re.compile(r"\\begin\{frame\}(.*?)\\end\{frame\}", re.S)
_FIG_CMD = re.compile(r"\\(noorsheet|noorfile|includegraphics)\s*(\[[^\]]*\])?\s*\{([^{}]*)\}")
def _width_fraction(opts: str | None):
    """``width=0.8\\textwidth`` -> ("frac", 0.8); ``width=9cm`` -> ("cm", 9.0); else None."""
    if not opts:
        return None
    m = re.search(r"width\s*=\s*([0-9.]+)\s*\\(?:textwidth|linewidth|columnwidth)", opts)
    if m:
        return ("frac", float(m.group(1)))
    if re.search(r"width\s*=\s*\\(?:textwidth|linewidth|columnwidth)", opts):
        return ("frac", 1.0)
    m = re.search(r"width\s*=\s*([0-9.]+)\s*cm", opts)
    return ("cm", float(m.group(1))) if m else None


def _parse_tabular(body: str) -> list:
    rows = []
    for line in re.split(r"\\\\", body):
        line = re.sub(r"\\(?:toprule|midrule|bottomrule|hline)", "", line).strip()
        if line:
            rows.append([plain(c) for c in line.split("&")])
    return rows


def parse_presentation(source: str) -> dict:
    """``{"meta": {title, author, date, institute}, "frames": [...]}`` from a beamer source."""
    code = _COMMENT.sub("", source)

    def cmd(name):
        m = re.search(r"\\" + name + r"\s*(?:\[[^\]]*\])?\s*(?=\{)", code)
        g = _group(code, m.end()) if m else None
        return plain(g[0]) if g else ""

    meta = {"title": cmd("title"), "author": cmd("author"), "date": cmd("date"),
            "institute": cmd("institute")}
    from . import slideobjects as _so
    meta["setup"] = _so.read_setup(code)
    meta["page_cm"] = tuple(v / 10 for v in _so.slide_size_mm(code))
    frames, notes = [], []
    for m in _FRAME.finditer(code):
        head, body = _frame_head(m.group(1))
        is_title = bool(re.search(r"\\titlepage\b", body))
        if is_title:
            tf = _parse_with_items(body)
            frames.append({"kind": "title", "title": meta["title"],
                           "objects": tf.get("objects", [])})
            continue
        parsed = _parse_with_items(body)
        if not parsed.get("transition"):                 # \transfade before the frame
            before = _TRANS_RE.findall(code[:m.start()])
            parsed["transition"] = transition_of("\\trans" + before[-1]) if before else None
        if head["title"] and not parsed["title"]:
            parsed["title"] = head["title"]
        parsed["subtitle"] = parsed["subtitle"] or head["subtitle"]
        parsed["kind"] = "content"
        frames.append(parsed)
    return {"meta": meta, "frames": frames}


def _frame_head(raw: str):
    """``<overlay>[options]{Title}{Subtitle}`` right after ``\begin{frame}`` -> (head, body)."""
    pos = 0
    title = subtitle = None
    s = raw
    while True:
        k = pos
        while k < len(s) and s[k] in " 	":
            k += 1
        if k < len(s) and s[k] in "<[" and title is None:
            e = s.find(">" if s[k] == "<" else "]", k)
            if e < 0:
                break
            pos = e + 1
            continue
        if k < len(s) and s[k] == "{" and (title is None or subtitle is None):
            g = _group(s, k)
            if not g:
                break
            if title is None:
                title = plain(g[0])
            else:
                subtitle = plain(g[0])
            pos = g[1]
            continue
        break
    return {"title": title, "subtitle": subtitle}, s[pos:]


def _parse_with_items(body: str) -> dict:
    # replace each \item by a marker so the generic splitter can attach text to the right item
    out = {"blocks": [], "source": "", "note": "", "title": None, "subtitle": None,
           "objects": []}
    text = _COMMENT.sub("", body)
    from . import slideobjects as _so                 # free objects: not part of the text flow
    pos = 0
    while True:
        m = re.compile(r"\\noorobj\b").search(text, pos)
        if not m:
            break
        got = _so.parse_object(text, m.start())
        if got is None:
            pos = m.end()
            continue
        out["objects"].append(got[0])
        text = text[:m.start()] + text[got[1]:]
        pos = m.start()
    # pull out the simple single-purpose commands first
    for name, key in (("framesource", "source"), ("note", "note")):
        for m in list(re.finditer(r"\\" + name + r"\s*(?:\[[^\]]*\])?\s*(?=\{)", text)):
            g = _group(text, m.end())
            if g:
                out[key] = (out[key] + "\n" + plain(g[0])).strip()
        text = _strip_cmd(text, name)
    for name, key in (("frametitle", "title"), ("framesubtitle", "subtitle")):
        m = re.search(r"\\" + name + r"\s*(?=\{)", text)
        if m:
            g = _group(text, m.end())
            if g:
                out[key] = plain(g[0])
        text = _strip_cmd(text, name)
    out["transition"] = transition_of(text)
    text = _TRANS_RE.sub("", text)
    out["blocks"] = _blocks(_overlays(text))
    top = max([_max_step(out["blocks"])] + [
        int(n) - 1 for spec in re.findall(r"<([\d,\-|+ @a-z]*)>", text)
        for n in re.findall(r"\d+", spec)])
    out["steps"] = max(0, top) + 1                       # PDF pages this frame produces
    return out


def _strip_cmd(text: str, name: str) -> str:
    pat = re.compile(r"\\" + name + r"\s*(?:\[[^\]]*\])?\s*(?=\{)")
    while True:
        m = pat.search(text)
        if not m:
            return text
        g = _group(text, m.end())
        text = text[:m.start()] + text[g[1] if g else m.end() + 1:]


_ENVS = ("itemize", "enumerate", "description", "columns", "tabular", "figure", "center",
         "block", "alertblock", "exampleblock")


def _find_env(text: str, start: int = 0):
    """First ``\\begin{env}`` (of :data:`_ENVS`) at/after ``start`` with its matching end."""
    m = re.compile(r"\\begin\{(" + "|".join(_ENVS) + r")\}").search(text, start)
    if not m:
        return None
    name = m.group(1)
    depth, pos = 1, m.end()
    tok = re.compile(r"\\begin\{" + name + r"\}|\\end\{" + name + r"\}")
    for t in tok.finditer(text, pos):
        depth += 1 if t.group(0).startswith("\\begin") else -1
        if depth == 0:
            return name, m.start(), m.end(), t.start(), t.end()
    return None


def _blocks(text: str, st: "_Steps | None" = None) -> list:
    st = st or _Steps()
    blocks = []

    def add_text(chunk: str):
        pos = 0
        for m in _FIG_CMD.finditer(chunk):
            _text_blocks(chunk[pos:m.start()])
            blocks.append(("picture", m.group(1), m.group(3).strip(), m.group(2) or "")
                          + _tail(st.current()))
            pos = m.end()
        _text_blocks(chunk[pos:])

    def _text_blocks(chunk):
        if not _MARK.search(chunk):
            for para in re.split(r"\n\s*\n|\n", plain(chunk)):
                if para.strip():
                    blocks.append(("text", para.strip()) + _tail(st.current()))
            return
        for line in re.split(r"\n", chunk):          # overlays: one step per line
            clean, step = st.feed(line)
            for para in re.split(r"\n", plain(clean)):
                if para.strip():
                    blocks.append(("text", para.strip()) + _tail(step))

    pos = 0
    while True:
        env = _find_env(text, pos)
        if env is None:
            add_text(text[pos:])
            break
        name, bs, be, es, ee = env
        add_text(text[pos:bs])
        inner = text[be:es]
        if name in ("itemize", "enumerate", "description"):
            steps: list = []
            items = _list_items(inner, name == "enumerate", 0, st, steps)
            blocks.append(("list", items, steps))
        elif name == "columns":
            cols, p = [], 0
            for cm in re.finditer(r"\\begin\{column\}\s*\{[^{}]*\}(.*?)\\end\{column\}", inner,
                                  re.S):
                st.feed(inner[p:cm.start()])
                p = cm.end()
                cols.append(_blocks(cm.group(1), st))
            st.feed(inner[p:])
            blocks.append(("columns", cols))
        elif name == "tabular":
            g = _group(inner, 0)
            rows = _parse_tabular(inner[g[1]:] if g else inner)
            if rows:
                blocks.append(("table", rows) + _tail(st.current()))
        elif name in ("block", "alertblock", "exampleblock"):
            g = _group(inner, 0)
            if g:
                blocks.append(("text", "**" + plain(g[0])) + _tail(st.current()))
                inner = inner[g[1]:]
            blocks.extend(_blocks(inner, st))
        else:                                # figure / center: transparent
            blocks.extend(_blocks(_drop_caption(inner, blocks), st))
        pos = ee
    return blocks


def _drop_caption(inner: str, blocks: list) -> str:
    m = re.search(r"\\caption\s*(?:\[[^\]]*\])?\s*(?=\{)", inner)
    if not m:
        return inner
    g = _group(inner, m.end())
    if not g:
        return inner
    return inner[:m.start()] + inner[m.end() + len(g[0]) + 2:] + "\n\n" + g[0]


def _list_items(inner: str, ordered: bool, level: int, st: "_Steps | None" = None,
                steps: "list | None" = None) -> list:
    """Items of one list level (nested lists become deeper items); ``steps`` receives the
    appearance step of every item, in the same order."""
    st = st or _Steps()
    steps = steps if steps is not None else []
    items = []
    # split on \item at depth 0 (nested lists are kept whole inside their item)
    parts, depth, cur = [], 0, ""
    for tok in re.split(r"(\\begin\{(?:itemize|enumerate|description)\}|"
                        r"\\end\{(?:itemize|enumerate|description)\}|\\item(?:\[[^\]]*\])?)", inner):
        if tok.startswith("\\begin"):
            depth += 1
            cur += tok
        elif tok.startswith("\\end"):
            depth -= 1
            cur += tok
        elif tok.startswith("\\item") and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += tok
    parts.append(cur)
    for idx, part in enumerate(parts):
        if idx == 0 and len(parts) > 1:
            st.feed(part)                    # text (markers) in front of the first \item
            continue
        floors = len(st.floors)
        m = re.search(r"\\begin\{(itemize|enumerate|description)\}", part)
        if m:
            head = part[:m.start()]
            env = _find_env(part, m.start())
            clean_h, step_h = st.feed(head)
            nested_steps: list = []
            nested = _list_items(part[env[2]:env[3]], env[0] == "enumerate", level + 1, st,
                                 nested_steps) if env else []
            clean_t, step_t = st.feed(part[env[4]:] if env else "")
            t = plain(clean_h + " " + clean_t)
            if t:
                items.append((level, t, ordered))
                steps.append(step_h if clean_h.strip() else step_t)
            items.extend(nested)
            steps.extend(nested_steps)
        else:
            clean, step = st.feed(part)
            t = plain(clean)
            if t:
                items.append((level, t, ordered))
                steps.append(step)
        del st.floors[floors:]
    return items


def reference_for(source: str):
    """``(reference.pptx path | None, mapping dict)`` of the imported theme the document uses."""
    code = _COMMENT.sub("", source)
    for m in re.finditer(r"\\usetheme(?:\[[^\]]*\])?\{([^}]*)\}", code):
        d = pptximport.presentations_dir() / m.group(1).strip()
        ref = d / "reference.pptx"
        if ref.is_file():
            meta = {}
            try:
                meta = json.loads((d / "template.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
            return str(ref), meta.get("pptx", {})
    return None, {}


def _need_pptx():
    try:
        import pptx  # noqa: F401
    except ImportError as exc:
        raise RuntimeError("PowerPoint export needs python-pptx: pip install python-pptx") from exc


def export_pptx(source: str, out_path, sheets, assets, sheet_png, asset_png, workdir,
                reference: str | None = None, mapping: dict | None = None,
                effect: str = "appear"):
    """Write ``source`` (a beamer document, tables already expanded) as ``out_path``.
    Returns ``(ok, notes)``."""
    _need_pptx()
    from pptx import Presentation
    from pptx.util import Emu

    P = parse_presentation(source)
    notes: list = []
    if reference is None:
        reference, mapping = reference_for(source)
    mapping = mapping or {}
    work = Path(workdir)
    work.mkdir(parents=True, exist_ok=True)

    prs = Presentation(reference) if reference else Presentation()
    if not reference:
        _to_16_10(prs)
    _drop_slides(prs)
    layouts = {lay.name: lay for lay in prs.slide_layouts}

    def pick(name, fallbacks):
        for n in (name, *fallbacks):
            if n and n in layouts:
                return layouts[n]
        return list(prs.slide_layouts)[0]

    title_layout = pick(mapping.get("title_layout"), ["Title Slide"])
    content_layout = pick(mapping.get("content_layout"), ["Title and Content"])
    two_layout = pick(mapping.get("two_layout"), ["Two Content", "Title and Content"])
    only_layout = pick(mapping.get("only_layout"), ["Title Only", "Title and Content"])
    title_idx = [i for i in mapping.get("title_idx", [])]

    counter = [0]

    def picture_file(cmd, key):
        counter[0] += 1
        dest = work / f"pic{counter[0]}.png"
        if cmd == "noorsheet":
            sm = latex.resolve_ref(key, sheets)
            if sm is None:
                notes.append(f"figure {key!r}: no such sheet")
                return None
            sheet_png(sm, str(dest))
            return dest
        if cmd == "noorfile":
            a = latex.resolve_asset(key, assets)
            if a is None:
                notes.append(f"figure file {key!r}: not in the project")
                return None
            asset_png(a, str(dest))
            return dest
        # \includegraphics{path}: relative files next to the document are not known here
        notes.append(f"\\includegraphics{{{key}}}: only sheets and figure files are exported")
        return None

    for fr in P["frames"]:
        if fr["kind"] == "title":
            tslide = _title_slide(prs, title_layout, P["meta"], title_idx, notes)
            _title_number(tslide, title_layout, P["meta"])
            _add_objects(tslide, fr.get("objects", []), prs, P["meta"], picture_file)
            continue
        cols = next((b for b in fr["blocks"] if b[0] == "columns"), None)
        layout = two_layout if cols and len(cols[1]) >= 2 else (
            only_layout if all(b[0] in ("picture", "table") for b in fr["blocks"])
            and fr["blocks"] else content_layout)
        slide = prs.slides.add_slide(layout)
        anims: list = []
        _fill_content(slide, fr, picture_file, notes, cols, anims)
        _add_slide_number(slide, layout)
        _add_objects(slide, fr.get("objects", []), prs, P["meta"], picture_file)
        _transition(slide, fr.get("transition"))
        _animate(slide, anims, effect)
        if fr.get("note"):
            slide.notes_slide.notes_text_frame.text = fr["note"]
    prs.save(str(out_path))
    return True, notes


def _to_16_10(prs):
    """PowerPoint's default template is 4:3 -- make it 16:10 and stretch its layouts."""
    from pptx.util import Emu
    old_w, old_h = prs.slide_width, prs.slide_height
    new_w, new_h = Emu(12192000), Emu(7620000)
    fx, fy = new_w / old_w, new_h / old_h
    prs.slide_width, prs.slide_height = new_w, new_h
    seen = []
    for owner in [prs.slide_master, *prs.slide_layouts]:
        for sh in owner.shapes:
            if not sh._element.xpath("./p:spPr/a:xfrm"):
                continue                      # inherits its (already scaled) geometry
            try:
                sh.left, sh.top = int(sh.left * fx), int(sh.top * fy)
                sh.width, sh.height = int(sh.width * fx), int(sh.height * fy)
            except (TypeError, AttributeError):
                pass
        seen.append(owner)


def _drop_slides(prs):
    """Remove the sample slides of a reference template (their layouts stay)."""
    lst = prs.slides._sldIdLst
    for sld in list(lst):
        prs.part.drop_rel(sld.rId)
        lst.remove(sld)


def _placeholders(slide):
    return list(slide.placeholders)


def _is_title(ph) -> bool:
    from pptx.enum.shapes import PP_PLACEHOLDER as T
    return ph.placeholder_format.type in (T.TITLE, T.CENTER_TITLE)


def _set_text(shape, lines, bullets=False):
    tf = shape.text_frame
    tf.clear()
    first = True
    for item in lines:
        level, text = (item[0], item[1]) if isinstance(item, tuple) else (0, item)
        p = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        bold = text.startswith("**")
        p.text = text[2:] if bold else text
        if bold:
            for r in p.runs:
                r.font.bold = True
        p.level = min(max(0, level), 4)


def _remove(shape):
    el = shape._element
    el.getparent().remove(el)


def _title_slide(prs, layout, meta, title_idx, notes):
    slide = prs.slides.add_slide(layout)
    phs = _placeholders(slide)
    by_idx = {str(p.placeholder_format.idx): p for p in phs}
    values = [meta["title"], meta["author"], meta["date"]]
    if title_idx and all(i in by_idx for i in title_idx):
        for i, v in zip(title_idx, values):
            _set_text(by_idx[i], [v] if v else [""])
        for p in phs:
            if str(p.placeholder_format.idx) not in title_idx and not p.has_text_frame or (
                    str(p.placeholder_format.idx) not in title_idx and not p.text_frame.text):
                _remove(p)
    else:                                       # default template: title + subtitle
        title = next((p for p in phs if _is_title(p)), None)
        if title is not None:
            _set_text(title, [meta["title"]])
        sub = next((p for p in phs if p is not title), None)
        if sub is not None:
            _set_text(sub, [x for x in (meta["author"], meta["date"]) if x])
    _add_slide_number(slide, layout, even_title=False)
    return slide


def _fit(path, box_w, box_h):
    from PIL import Image
    with Image.open(path) as im:
        w, h = im.size
    scale = min(box_w / w, box_h / h)
    return int(w * scale), int(h * scale)


def _place_picture(slide, path, box, frac=None):
    """Add ``path`` centred in ``box`` = (left, top, width, height) in EMU."""
    left, top, bw, bh = box
    w, h = _fit(path, bw, bh)
    if frac and frac[0] == "frac":
        w2, h2 = _fit(path, int(bw * min(1.0, frac[1])), bh)
        w, h = w2, h2
    return slide.shapes.add_picture(str(path), left + (bw - w) // 2, top + (bh - h) // 2,
                                    w, h)


def _fill_content(slide, fr, picture_file, notes, cols, anims=None):
    from pptx.util import Emu
    phs = _placeholders(slide)
    title = next((p for p in phs if _is_title(p)), None)
    bodies = [p for p in phs if p is not title]
    sheight = slide.part.package.presentation_part.presentation.slide_height
    main = [b for b in bodies if b.top is not None and b.top < sheight * 0.5]
    foot = [b for b in bodies if b not in main]
    main.sort(key=lambda b: (b.left or 0))
    if title is not None:
        _set_text(title, [fr.get("title") or ""])
    if fr.get("source") and foot:
        _set_text(foot[0], [fr["source"]])
        foot = foot[1:]
    elif fr.get("source"):                      # layout without a footer placeholder
        from pptx.util import Pt
        pw = slide.part.package.presentation_part.presentation.slide_width
        tb = slide.shapes.add_textbox(int(pw * 0.04), int(sheight * 0.92), int(pw * 0.92),
                                      int(sheight * 0.05))
        tb.text_frame.text = fr["source"]
        for r in tb.text_frame.paragraphs[0].runs:
            r.font.size = Pt(12)
    for f in foot:
        if not f.has_text_frame or not f.text_frame.text:
            _remove(f)
    blocks = fr["blocks"]
    anims = anims if anims is not None else []

    def area_of(ph):
        return (ph.left, ph.top, ph.width, ph.height)

    def render(blocks_, ph, box):
        """Blocks into one placeholder (text) and free shapes (pictures / tables)."""
        lines, line_steps = [], []
        pics = []
        tables = []
        for b in blocks_:
            if b[0] == "list":
                lines.extend((lvl, txt) for lvl, txt, _o in b[1])
                line_steps.extend(b[2] if len(b) > 2 else [0] * len(b[1]))
            elif b[0] == "text":
                lines.append(b[1])
                line_steps.append(b[2] if len(b) > 2 else 0)
            elif b[0] == "picture":
                f = picture_file(b[1], b[2])
                if f is not None:
                    pics.append((f, _width_fraction(b[3]), b[4] if len(b) > 4 else 0))
            elif b[0] == "table":
                tables.append((b[1], b[2] if len(b) > 2 else 0))
        left, top, width, height = box
        text_w = width
        if lines and (pics or tables):
            text_w = int(width * 0.46)
        if ph is not None:
            if lines:
                ph.left, ph.top, ph.width, ph.height = left, top, text_w, height
                _set_text(ph, lines)
                for i, k in enumerate(line_steps):
                    if k > 0:
                        anims.append((ph, i, k))
            else:
                _remove(ph)
        shapes_left = left + (text_w + int(width * 0.02) if lines and (pics or tables) else 0)
        shapes_w = width - (shapes_left - left)
        items = [("pic", p) for p in pics] + [("tab", t) for t in tables]
        for i, (kind, obj) in enumerate(items):
            cell_w = shapes_w // len(items)
            cell = (shapes_left + i * cell_w, top, cell_w, height)
            if kind == "pic":
                shp, k = _place_picture(slide, obj[0], cell, obj[1]), obj[2]
            else:
                shp, k = _place_table(slide, obj[0], cell), obj[1]
            if k > 0:
                anims.append((shp, None, k))

    if cols and len(cols[1]) >= 2 and len(main) >= 2:
        for ph, cb in zip(main[:2], cols[1][:2]):
            render(cb, ph, area_of(ph))
        rest = [b for b in blocks if b is not cols]
        if rest:
            notes.append("content outside the columns of a frame was not exported")
        for ph in main[2:]:
            _remove(ph)
        return
    if cols:                                    # fewer placeholders than columns: halves
        flat = []
        for cb in cols[1]:
            flat.extend(cb)
        blocks = [b for b in blocks if b is not cols] + flat
    if main:
        ph = main[0]
        render(blocks, ph, area_of(ph))
        for extra in main[1:]:
            _remove(extra)
    else:
        w, h = slide.part.package.presentation_part.presentation.slide_width, sheight
        render(blocks, None, (int(w * 0.04), int(h * 0.2), int(w * 0.92), int(h * 0.65)))


def _place_table(slide, rows, box):
    from pptx.util import Pt
    left, top, width, height = box
    nr, nc = len(rows), max(len(r) for r in rows)
    shape = slide.shapes.add_table(nr, nc, left, top, width, min(height, int(width * 0.06 * nr)))
    tbl = shape.table
    for i, row in enumerate(rows):
        for j in range(nc):
            cell = tbl.cell(i, j)
            cell.text = row[j] if j < len(row) else ""
            for p in cell.text_frame.paragraphs:
                for r in p.runs:
                    r.font.size = Pt(14)
    return shape


def _add_slide_number(slide, layout, even_title=True):
    """The layout's slide-number placeholder as a field on the slide (PowerPoint shows numbers
    only for slides that carry the placeholder)."""
    from pptx.enum.shapes import PP_PLACEHOLDER as T
    src = next((p for p in layout.placeholders
                if p.placeholder_format.type == T.SLIDE_NUMBER), None)
    if src is None:
        return
    ns = ('xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
          'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"')
    from lxml import etree
    sp = etree.fromstring(
        f'<p:sp {ns}><p:nvSpPr><p:cNvPr id="900" name="Slide Number Placeholder"/>'
        f'<p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr><p:nvPr><p:ph type="sldNum" sz="quarter" '
        f'idx="{src.placeholder_format.idx}"/></p:nvPr></p:nvSpPr><p:spPr/>'
        f'<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:fld id="{{B6F15528-21DE-4FAA-801E-634DDDAF4B2B}}" '
        f'type="slidenum"><a:rPr lang="en-US"/><a:t>&#8249;#&#8250;</a:t></a:fld>'
        f'<a:endParaRPr lang="en-US"/></a:p></p:txBody></p:sp>')
    slide.shapes._spTree.append(sp)


# --------------------------------------------------------------------------- effects
_P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
_EFFECTS = {                                   # name -> (presetID, subtype, animEffect filter)
    "appear": (1, 0, None),
    "fade": (10, 0, "fade"),
    "wipe": (22, 8, "wipe(left)"),
}
_TRANSITIONS = {
    "fade": "<p:fade/>", "dissolve": "<p:dissolve/>", "wipe": '<p:wipe dir="l"/>',
    "split": '<p:split orient="vert" dir="out"/>', "blinds": '<p:blinds dir="horz"/>',
    "pull": '<p:pull dir="l"/>', "cover": '<p:cover dir="l"/>',
}


def _transition(slide, kind):
    """Slide transition from a Beamer ``\\trans...`` command."""
    if not kind or kind not in _TRANSITIONS:
        return
    from lxml import etree
    el = etree.fromstring(f'<p:transition xmlns:p="{_P_NS}" spd="med">{_TRANSITIONS[kind]}'
                          f'</p:transition>')
    slide._element.append(el)


def _animate(slide, anims, effect="appear"):
    """Click animations: ``anims`` = [(shape, paragraph index | None, step)]; every distinct
    step is one click, all items of the same step appear together."""
    if not anims:
        return
    from lxml import etree
    pid, sub, filt = _EFFECTS.get(effect, _EFFECTS["appear"])
    counter = [2]

    def nid():
        counter[0] += 1
        return counter[0]

    def target(shape, para):
        if para is None:
            return f'<p:spTgt spid="{shape.shape_id}"/>'
        return (f'<p:spTgt spid="{shape.shape_id}"><p:txEl><p:pRg st="{para}" end="{para}"/>'
                f'</p:txEl></p:spTgt>')

    def one(shape, para, first):
        t = target(shape, para)
        node = "clickEffect" if first else "withEffect"
        x = (f'<p:par><p:cTn id="{nid()}" presetID="{pid}" presetClass="entr" '
             f'presetSubtype="{sub}" fill="hold" grpId="0" nodeType="{node}"><p:stCondLst>'
             f'<p:cond delay="0"/></p:stCondLst><p:childTnLst><p:set><p:cBhvr>'
             f'<p:cTn id="{nid()}" dur="1" fill="hold"><p:stCondLst><p:cond delay="0"/>'
             f'</p:stCondLst></p:cTn><p:tgtEl>{t}</p:tgtEl><p:attrNameLst>'
             f'<p:attrName>style.visibility</p:attrName></p:attrNameLst></p:cBhvr><p:to>'
             f'<p:strVal val="visible"/></p:to></p:set>')
        if filt:
            x += (f'<p:animEffect transition="in" filter="{filt}"><p:cBhvr><p:cTn id="{nid()}" '
                  f'dur="500"/><p:tgtEl>{t}</p:tgtEl></p:cBhvr></p:animEffect>')
        return x + "</p:childTnLst></p:cTn></p:par>"

    clicks = []
    for step in sorted({k for _, _, k in anims}):
        outer, inner = nid(), nid()
        effs = "".join(one(shp, para, i == 0) for i, (shp, para, k)
                       in enumerate([a for a in anims if a[2] == step]))
        clicks.append(
            f'<p:par><p:cTn id="{outer}" fill="hold"><p:stCondLst><p:cond delay="indefinite"/>'
            f'</p:stCondLst><p:childTnLst><p:par><p:cTn id="{inner}" fill="hold"><p:stCondLst>'
            f'<p:cond delay="0"/></p:stCondLst><p:childTnLst>{effs}</p:childTnLst></p:cTn>'
            f'</p:par></p:childTnLst></p:cTn></p:par>')
    builds, seen = [], set()
    for shp, para, _k in anims:
        if shp.shape_id in seen or not getattr(shp, "has_text_frame", False):
            continue
        seen.add(shp.shape_id)
        whole = para is None
        builds.append(f'<p:bldP spid="{shp.shape_id}" grpId="0"'
                      + ("" if whole else ' build="p"') + "/>")
    xml = (f'<p:timing xmlns:p="{_P_NS}"><p:tnLst><p:par><p:cTn id="1" dur="indefinite" '
           f'restart="never" nodeType="tmRoot"><p:childTnLst><p:seq concurrent="1" '
           f'nextAc="seek"><p:cTn id="2" dur="indefinite" nodeType="mainSeq"><p:childTnLst>'
           + "".join(clicks) +
           '</p:childTnLst></p:cTn><p:prevCondLst><p:cond evt="onPrev" delay="0"><p:tgtEl>'
           '<p:sldTgt/></p:tgtEl></p:cond></p:prevCondLst><p:nextCondLst><p:cond evt="onNext" '
           'delay="0"><p:tgtEl><p:sldTgt/></p:tgtEl></p:cond></p:nextCondLst></p:seq>'
           '</p:childTnLst></p:cTn></p:par></p:tnLst>'
           + (f'<p:bldLst>{"".join(builds)}</p:bldLst>' if builds else "") + '</p:timing>')
    slide._element.append(etree.fromstring(xml))


# --------------------------------------------------------------------------- free objects
def _hex(value, default="000000") -> str:
    v = str(value or "").lstrip("#")
    return v if re.fullmatch(r"[0-9a-fA-F]{6}", v) else default


def _set_alpha(fill_or_line_fore_color_parent, opacity: float):
    """Opacity on the solid fill (DrawingML ``alpha``)."""
    from lxml import etree
    try:
        clr = fill_or_line_fore_color_parent._xPr.find(
            ".//{http://schemas.openxmlformats.org/drawingml/2006/main}srgbClr")
    except Exception:
        clr = None
    if clr is not None:
        a = etree.SubElement(clr, "{http://schemas.openxmlformats.org/drawingml/2006/main}alpha")
        a.set("val", str(int(max(0.0, min(1.0, opacity)) * 100000)))


def _title_number(slide, layout, meta):
    """``number-on-title``: make sure the title slide shows its number (field box when the
    title layout has no number placeholder)."""
    if str(meta.get("setup", {}).get("number-on-title", "")).lower() != "true":
        return
    from pptx.enum.shapes import PP_PLACEHOLDER as T
    if any(p.placeholder_format.type == T.SLIDE_NUMBER for p in layout.placeholders):
        return
    from lxml import etree
    from pptx.util import Emu, Pt
    prs = slide.part.package.presentation_part.presentation
    setup = meta.get("setup", {})
    pw_cm = meta.get("page_cm", (16.0, 10.0))[0]
    k = prs.slide_width / (pw_cm * 360000)
    size = float(setup.get("number-size", 10) or 10)
    x = float(setup["number-x"]) * 360000 * k if setup.get("number-x") else prs.slide_width * 0.9
    y = float(setup["number-y"]) * 360000 * k if setup.get("number-y") else prs.slide_height * 0.93
    tb = slide.shapes.add_textbox(Emu(int(x)), Emu(int(y)), Emu(int(prs.slide_width * 0.08)),
                                  Emu(int(prs.slide_height * 0.05)))
    p = tb.text_frame.paragraphs[0]._p
    for r in list(p):
        p.remove(r)
    ns = "http://schemas.openxmlformats.org/drawingml/2006/main"
    fld = etree.SubElement(p, f"{{{ns}}}fld")
    fld.set("id", "{B6F15528-21DE-4FAA-801E-634DDDAF4B2B}")
    fld.set("type", "slidenum")
    rpr = etree.SubElement(fld, f"{{{ns}}}rPr")
    rpr.set("lang", "en-US")
    rpr.set("sz", str(int(size * 100)))
    t = etree.SubElement(fld, f"{{{ns}}}t")
    t.text = "1"


def _add_objects(slide, objs, prs, meta, picture_file):
    """The ``\\noorobj`` objects of a frame as native PowerPoint shapes / pictures / lines."""
    if not objs:
        return
    from pptx.dml.color import RGBColor
    from pptx.enum.dml import MSO_LINE
    from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
    from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
    from pptx.util import Emu, Pt
    from lxml import etree
    pw_cm, ph_cm = meta.get("page_cm", (16.0, 10.0))
    kx = prs.slide_width / (pw_cm * 360000)
    ky = prs.slide_height / (ph_cm * 360000)

    def emu(v, k):
        return Emu(int(float(v) * 360000 * k))

    def style_line(shape, o):
        ln = shape.line
        if o.get("line") == "none":
            ln.fill.background()
        else:
            ln.color.rgb = RGBColor.from_string(_hex(o.get("line")).upper())
            ln.width = Pt(float(o.get("lw", 1)))
            dash = {"dashed": MSO_LINE.DASH, "dotted": MSO_LINE.ROUND_DOT}.get(o.get("dash"))
            if dash is not None:
                ln.dash_style = dash

    def style_text(shape, o):
        tf = shape.text_frame
        tf.word_wrap = True
        tf.vertical_anchor = MSO_ANCHOR.MIDDLE
        text = plain(o.get("arg", ""))
        if not text:
            return
        lines = text.split("\n")
        for i, line in enumerate(lines):
            para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            para.text = line
            para.alignment = {"left": PP_ALIGN.LEFT, "right": PP_ALIGN.RIGHT}.get(
                o.get("align"), PP_ALIGN.CENTER)
            for r in para.runs:
                r.font.size = Pt(float(o.get("size", 18)))
                r.font.color.rgb = RGBColor.from_string(_hex(o.get("color")).upper())

    for o in objs:
        kind = o["kind"]
        try:
            if kind in ("line", "arrow"):
                c = slide.shapes.add_connector(
                    MSO_CONNECTOR.STRAIGHT, emu(o["x"], kx), emu(o["y"], ky),
                    emu(o["x2"], kx), emu(o["y2"], ky))
                style_line(c, o)
                if kind == "arrow":
                    ln = c.line._get_or_add_ln()
                    tail = etree.SubElement(
                        ln, "{http://schemas.openxmlformats.org/drawingml/2006/main}tailEnd")
                    tail.set("type", "triangle")
                continue
            l, t = emu(o["x"], kx), emu(o["y"], ky)
            w, h = emu(o["w"], kx), emu(o["h"], ky)
            if kind in ("sheet", "file"):
                f = picture_file("noorsheet" if kind == "sheet" else "noorfile", o.get("arg", ""))
                if f is None:
                    continue
                pic = slide.shapes.add_picture(str(f), l, t, w, h)
                pic.rotation = -float(o.get("angle", 0))
                continue
            if kind == "text":
                shp = slide.shapes.add_textbox(l, t, w, h)
            else:
                shp = slide.shapes.add_shape(
                    MSO_SHAPE.OVAL if kind == "ellipse" else MSO_SHAPE.RECTANGLE, l, t, w, h)
                fill = o.get("fill", "none")
                if fill == "none":
                    shp.fill.background()
                elif o.get("fill2"):
                    shp.fill.gradient()
                    stops = shp.fill.gradient_stops
                    stops[0].color.rgb = RGBColor.from_string(_hex(fill).upper())
                    stops[1].color.rgb = RGBColor.from_string(_hex(o["fill2"]).upper())
                    shp.fill.gradient_angle = {"horizontal": 0, "vertical": 270}.get(
                        o.get("grad"), 0)
                else:
                    shp.fill.solid()
                    shp.fill.fore_color.rgb = RGBColor.from_string(_hex(fill).upper())
                    if float(o.get("opacity", 1)) < 1:
                        _set_alpha(shp.fill, float(o["opacity"]))
                style_line(shp, o)
                shp.shadow.inherit = False
            style_text(shp, o)
            shp.rotation = -float(o.get("angle", 0))
        except Exception:                      # one odd object must not break the export
            continue
