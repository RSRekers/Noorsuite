"""Font size / bold / italic / colour of ordinary slide text (Qt-free).

The text of a slide is plain LaTeX, so a change is written into the source: the text of one line is
wrapped as ``{\\fontsize{N}{M}\\selectfont\\bfseries\\itshape\\color[HTML]{RRGGBB} text}`` (the wrapper is
recognised again, so repeated changes never nest), and the sizes of a whole frame are one
``\\noorfont{body=M}`` line right after ``\\begin{frame}`` (see ``noorsuite.sty``).
"""

from __future__ import annotations

import re

from .slideobjects import _group, frames

KEEP = object()                      # "leave this property as it is"

_ITEM = re.compile(r"^(\s*\\item(?:<[^>]*>)?(?:\[[^\]]*\])?(?:<[^>]*>)?[ \t]*)(.*)$")
_TITLE_CMD = re.compile(r"\\(?:frametitle|framesubtitle)(?:<[^>]*>)?\s*(?=\{)")
_BLOCK = re.compile(r"^\s*\\begin\{(?:block|alertblock|exampleblock)\}(?:<[^>]*>)?\s*(?=\{)")
_FRAME = re.compile(r"^\s*\\begin\{frame\}(?:<[^>]*>)?\s*(?:\[[^\]]*\])?\s*(?=\{)")
_SKIP = re.compile(r"^\s*(?:\\begin|\\end|%|\\noorobj|\\noormasterobj|\\usepackage|\\note|\\framesource|"
                   r"\\pause|\\titlepage|\\includegraphics|\\noorsheet|\\noorfile|\\noortable|"
                   r"\\label|\\centering|\\vspace|\\hspace|\\noorfont|\\\[|\\\])")
_WRAP = re.compile(r"^\{(\\fontsize\{([\d.]+)\}\{[\d.]+\}\\selectfont)?(\\bfseries)?(\\itshape)?"
                   r"(\\color\[HTML\]\{([0-9A-Fa-f]{6})\})?[ ]?(.*)\}$", re.S)


def _split(line: str):
    """(head, content, tail) of the styleable part of one source line, or None."""
    m = _ITEM.match(line)
    if m:
        return m.group(1), m.group(2), ""
    for rx in (_FRAME, _BLOCK):
        m = rx.match(line)
        if m:
            last = None
            i = m.end()
            while True:                    # the title is the last { } group of the line
                g = _group(line, i)
                if g is None:
                    break
                last = (i, g)
                i = g[1]
                while i < len(line) and line[i] in " \t":
                    i += 1
            if last is not None:
                i0, (inner, end) = last
                return line[:i0 + 1], inner, line[end - 1:]
            return None
    for m in _TITLE_CMD.finditer(line):
        g = _group(line, m.end())
        if g:
            return line[:m.end() + 1], g[0], line[g[1] - 1:]
    if _SKIP.match(line) or not line.strip():
        return None
    lead = line[:len(line) - len(line.lstrip())]
    return lead, line.strip(), ""


def _unwrap(content: str):
    """(props, plain text) when ``content`` is our wrapper, else (defaults, content)."""
    props = {"size": None, "bold": False, "italic": False, "color": ""}
    c = content.strip()
    m = _WRAP.match(c)
    if m and any(m.group(i) for i in (1, 3, 4, 5)):
        g = _group(c, 0)                                   # the closing brace must match the first
        if g and g[1] == len(c):
            props.update(size=float(m.group(2)) if m.group(2) else None, bold=bool(m.group(3)),
                         italic=bool(m.group(4)), color=(m.group(6) or "").upper())
            return props, m.group(7)
    return props, content


def _wrap(props: dict, text: str) -> str:
    pre = ""
    if props["size"]:
        n = props["size"]
        pre += f"\\fontsize{{{n:g}}}{{{round(n * 1.2, 2):g}}}\\selectfont"
    if props["bold"]:
        pre += "\\bfseries"
    if props["italic"]:
        pre += "\\itshape"
    if props["color"]:
        pre += f"\\color[HTML]{{{props['color'].lstrip('#').upper()}}}"
    return "{" + pre + " " + text + "}" if pre else text


def line_style(source: str, line: int):
    """``{"text", "size", "bold", "italic", "color", "kind"}`` of source line ``line`` (0-based) or None."""
    lines = source.split("\n")
    if not 0 <= line < len(lines):
        return None
    parts = _split(lines[line])
    if parts is None:
        return None
    _head, content, _tail = parts
    props, text = _unwrap(content)
    if _ITEM.match(lines[line]):
        kind = "item"
    elif _FRAME.match(lines[line]) or _TITLE_CMD.search(lines[line]):
        kind = "title"
    elif _BLOCK.match(lines[line]):
        kind = "block"
    else:
        kind = "text"
    return {**props, "text": text, "kind": kind}


def restyle_line(source: str, line: int, size=KEEP, bold=KEEP, italic=KEEP, color=KEEP) -> str:
    """Apply the given properties to the text of source line ``line`` (others stay)."""
    lines = source.split("\n")
    if not 0 <= line < len(lines):
        return source
    parts = _split(lines[line])
    if parts is None:
        return source
    head, content, tail = parts
    props, text = _unwrap(content)
    for key, val in (("size", size), ("bold", bold), ("italic", italic), ("color", color)):
        if val is not KEEP:
            props[key] = val
    if props["size"] is not None and props["size"] <= 0:
        props["size"] = None
    lines[line] = head + _wrap(props, text) + tail
    return "\n".join(lines)


def frame_title_line(source: str, frame: int):
    r"""0-based source line that carries the title of frame ``frame`` (``\begin{frame}{T}`` or
    ``\frametitle{T}``), or None. The title is styled through its text (a size set after the
    title was typeset would not reach it)."""
    span = _frame_span(source, frame)
    if span is None:
        return None
    first = source.count("\n", 0, span[0])
    for k, line in enumerate(source[span[0]:span[1]].split("\n")):
        if (_FRAME.match(line) or _TITLE_CMD.search(line)) and _split(line) is not None:
            return first + k
    return None


# ---------------------------------------------------------------------- whole-frame sizes
_NOORFONT = re.compile(r"^[ \t]*\\noorfont\{([^{}]*)\}[ \t]*\n", re.M)


def _frame_span(source: str, frame: int):
    fr = frames(source)
    return fr[frame] if 0 <= frame < len(fr) else None


def read_frame_fonts(source: str, frame: int) -> dict:
    """``{"title": N, "body": M}`` of ``\\noorfont`` in frame ``frame`` (missing keys absent)."""
    span = _frame_span(source, frame)
    if span is None:
        return {}
    m = _NOORFONT.search(source, span[0], span[1])
    out = {}
    if m:
        for part in m.group(1).split(","):
            k, _, v = part.partition("=")
            try:
                out[k.strip()] = float(v)
            except ValueError:
                pass
    return out


def set_frame_fonts(source: str, frame: int, body=KEEP) -> str:
    """Write / update / remove the frame's ``\\noorfont{body=}`` line (``None`` or 0 clears it).
    (A title size is set on the title text itself, see :func:`frame_title_line`.)"""
    span = _frame_span(source, frame)
    if span is None:
        return source
    cur = read_frame_fonts(source, frame)
    if body is not KEEP:
        if body:
            cur["body"] = float(body)
        else:
            cur.pop("body", None)
    m = _NOORFONT.search(source, span[0], span[1])
    line = ("\\noorfont{" + ",".join(f"{k}={cur[k]:g}" for k in ("body",) if k in cur)
            + "}\n") if cur else ""
    if m:
        return source[:m.start()] + line + source[m.end():]
    if not line:
        return source
    eol = source.find("\n", span[0])                       # right after the \begin{frame} line
    if eol < 0 or eol >= span[1]:
        return source
    return source[:eol + 1] + line + source[eol + 1:]


# ---------------------------------------------------------------------- text under the mouse
import unicodedata  # noqa: E402

_CMD = re.compile(r"\\[A-Za-z]+\*?(?:\[[^\]]*\])?|<[^>]*>|%.*$|\$")


def _alnum(text: str) -> str:
    """Letters and digits only, case-folded and without ligatures / accents: the common ground of
    LaTeX source text and the text layer of the compiled PDF."""
    t = unicodedata.normalize("NFKD", text).casefold()
    return "".join(ch for ch in t if ch.isalnum())


def plain_text(line: str) -> str:
    return _alnum(_CMD.sub(" ", line))


def match_pdf_text(source: str, first: int, last: int, pdf_text: str):
    r"""The source line in ``first..last`` (0-based, inclusive) whose text contains -- or, for a
    long paragraph line, is contained in -- the line of text read from the PDF, else None.
    (SyncTeX is no help inside a Beamer frame: the whole body is typeset at ``\end{frame}``.)"""
    want = _alnum(pdf_text)
    if len(want) < 3:
        return None
    lines = source.split("\n")
    best, score = None, 0
    for i in range(max(0, first), min(last, len(lines) - 1) + 1):
        if _split(lines[i]) is None:
            continue
        have = plain_text(lines[i])
        if len(have) < 3:
            continue
        if want in have:
            s = len(want) + 1000
        elif have in want:
            s = len(have)
        else:
            s = 0
        if s > score:
            best, score = i, s
    return best
