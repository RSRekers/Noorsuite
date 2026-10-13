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
    frames, notes = [], []
    for m in _FRAME.finditer(code):
        head, body = _frame_head(m.group(1))
        is_title = bool(re.search(r"\\titlepage\b", body))
        if is_title:
            frames.append({"kind": "title", "title": meta["title"]})
            continue
        parsed = _parse_with_items(body)
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
    out = {"blocks": [], "source": "", "note": "", "title": None, "subtitle": None}
    text = _COMMENT.sub("", body)
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
    out["blocks"] = _blocks(text)
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


def _blocks(text: str) -> list:
    blocks = []

    def add_text(chunk: str):
        pos = 0
        for m in _FIG_CMD.finditer(chunk):
            _text_blocks(chunk[pos:m.start()])
            blocks.append(("picture", m.group(1), m.group(3).strip(), m.group(2) or ""))
            pos = m.end()
        _text_blocks(chunk[pos:])

    def _text_blocks(chunk):
        t = plain(chunk)
        for para in re.split(r"\n\s*\n|\n", t):
            if para.strip():
                blocks.append(("text", para.strip()))

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
            blocks.append(("list", _list_items(inner, name == "enumerate", 0)))
        elif name == "columns":
            cols = []
            for cm in re.finditer(r"\\begin\{column\}\s*\{[^{}]*\}(.*?)\\end\{column\}", inner, re.S):
                cols.append(_blocks(cm.group(1)))
            blocks.append(("columns", cols))
        elif name == "tabular":
            g = _group(inner, 0)
            rows = _parse_tabular(inner[g[1]:] if g else inner)
            if rows:
                blocks.append(("table", rows))
        elif name in ("block", "alertblock", "exampleblock"):
            g = _group(inner, 0)
            if g:
                blocks.append(("text", "**" + plain(g[0])))
                inner = inner[g[1]:]
            blocks.extend(_blocks(inner))
        else:                                # figure / center: transparent
            blocks.extend(_blocks(_drop_caption(inner, blocks)))
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


def _list_items(inner: str, ordered: bool, level: int) -> list:
    """Items of one list level (nested lists become deeper items)."""
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
    for part in parts[1:] if len(parts) > 1 else parts:
        m = re.search(r"\\begin\{(itemize|enumerate|description)\}", part)
        if m:
            head = part[:m.start()]
            env = _find_env(part, m.start())
            nested = _list_items(part[env[2]:env[3]], env[0] == "enumerate", level + 1) if env else []
            tail = part[env[4]:] if env else ""
            t = plain(head + " " + tail)
            if t:
                items.append((level, t, ordered))
            items.extend(nested)
        else:
            t = plain(part)
            if t:
                items.append((level, t, ordered))
    return items


# --------------------------------------------------------------------------- PPTX writer
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
                reference: str | None = None, mapping: dict | None = None):
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
            _title_slide(prs, title_layout, P["meta"], title_idx, notes)
            continue
        cols = next((b for b in fr["blocks"] if b[0] == "columns"), None)
        layout = two_layout if cols and len(cols[1]) >= 2 else (
            only_layout if all(b[0] in ("picture", "table") for b in fr["blocks"])
            and fr["blocks"] else content_layout)
        slide = prs.slides.add_slide(layout)
        _fill_content(slide, fr, picture_file, notes, cols)
        _add_slide_number(slide, layout)
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
    slide.shapes.add_picture(str(path), left + (bw - w) // 2, top + (bh - h) // 2, w, h)


def _fill_content(slide, fr, picture_file, notes, cols):
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

    def area_of(ph):
        return (ph.left, ph.top, ph.width, ph.height)

    def render(blocks_, ph, box):
        """Blocks into one placeholder (text) and free shapes (pictures / tables)."""
        lines = []
        pics = []
        tables = []
        for b in blocks_:
            if b[0] == "list":
                lines.extend((lvl, txt) for lvl, txt, _o in b[1])
            elif b[0] == "text":
                lines.append(b[1])
            elif b[0] == "picture":
                f = picture_file(b[1], b[2])
                if f is not None:
                    pics.append((f, _width_fraction(b[3])))
            elif b[0] == "table":
                tables.append(b[1])
        left, top, width, height = box
        text_w = width
        if lines and (pics or tables):
            text_w = int(width * 0.46)
        if ph is not None:
            if lines:
                ph.left, ph.top, ph.width, ph.height = left, top, text_w, height
                _set_text(ph, lines)
            else:
                _remove(ph)
        shapes_left = left + (text_w + int(width * 0.02) if lines and (pics or tables) else 0)
        shapes_w = width - (shapes_left - left)
        items = [("pic", p) for p in pics] + [("tab", t) for t in tables]
        for i, (kind, obj) in enumerate(items):
            cell_w = shapes_w // len(items)
            cell = (shapes_left + i * cell_w, top, cell_w, height)
            if kind == "pic":
                _place_picture(slide, obj[0], cell, obj[1])
            else:
                _place_table(slide, obj, cell)

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
