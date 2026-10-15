"""Free-positioned objects on Beamer slides: the ``\\noorobj`` lines in a frame.

Qt-free helpers shared by the slide editor, the PowerPoint export and the tests.

An object is one line inside a frame::

    \\noorobj[x=2,y=3,w=4,h=2,angle=15,fill=ff8800]{rect}{Label}

kinds: ``rect ellipse line arrow text sheet file``; positions / sizes in cm from the slide's top-left
corner; colours are hex without ``#``; ``line`` / ``arrow`` use ``x,y`` -> ``x2,y2``; ``sheet`` / ``file``
carry the sheet or asset key as content.
"""

from __future__ import annotations

import re

KINDS = ("rect", "ellipse", "line", "arrow", "text", "sheet", "file")

# defaults of the LaTeX macro (keys equal to these are not written)
DEFAULTS = {"x": 0.0, "y": 0.0, "w": 4.0, "h": 2.0, "x2": 4.0, "y2": 2.0, "angle": 0.0,
            "fill": "none", "fill2": "", "grad": "vertical", "line": "000000", "lw": 1.0,
            "opacity": 1.0, "size": 18.0, "color": "000000", "align": "center", "dash": "solid"}
NUMERIC = ("x", "y", "w", "h", "x2", "y2", "angle", "lw", "opacity", "size")
ALWAYS = ("x", "y")                                  # written even when 0

_BEGIN = re.compile(r"\\begin\{frame\}")
_END = re.compile(r"\\end\{frame\}")
_OBJ = re.compile(r"\\noorobj\s*")


def frames(source: str) -> list:
    """``[(start, end)]`` of every frame (``\\begin{frame}`` .. ``\\end{frame}``)."""
    out = []
    for m in _BEGIN.finditer(source):
        e = _END.search(source, m.end())
        if e:
            out.append((m.start(), e.end()))
    return out


def frame_at(source: str, pos: int) -> int:
    """Index of the frame containing ``pos`` (the next one when between frames), else -1."""
    fr = frames(source)
    for i, (s, e) in enumerate(fr):
        if s <= pos <= e:
            return i
    for i, (s, _e) in enumerate(fr):
        if s > pos:
            return i
    return len(fr) - 1


def _group(text: str, i: int, open_c="{", close_c="}"):
    if i >= len(text) or text[i] != open_c:
        return None
    depth = 0
    j = i
    while j < len(text):
        c = text[j]
        if c == "\\":
            j += 2
            continue
        if c == open_c:
            depth += 1
        elif c == close_c:
            depth -= 1
            if depth == 0:
                return text[i + 1:j], j + 1
        j += 1
    return None


def parse_options(opts: str) -> dict:
    out = {}
    for part in re.split(r",\s*(?![^{]*\})", opts):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def parse_object(text: str, start: int):
    """The ``\\noorobj`` call beginning at ``start`` -> (object dict, end) or None."""
    m = _OBJ.match(text, start)
    if not m:
        return None
    pos = m.end()
    opts = ""
    if pos < len(text) and text[pos] == "[":
        g = _group(text, pos, "[", "]")
        if not g:
            return None
        opts, pos = g
    while pos < len(text) and text[pos] in " \t":
        pos += 1
    kind = _group(text, pos)
    if not kind:
        return None
    pos = kind[1]
    while pos < len(text) and text[pos] in " \t":
        pos += 1
    content = _group(text, pos)
    if not content:
        return None
    obj = dict(DEFAULTS)
    if kind[0].strip() in ("text",):
        obj.update({"line": "none", "align": "left"})
    for k, v in parse_options(opts).items():
        obj[k] = float(v) if k in NUMERIC and _isnum(v) else v
    obj["kind"] = kind[0].strip()
    obj["arg"] = content[0]
    return obj, content[1]


def _isnum(v) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def objects_in(source: str, frame: int) -> list:
    """Objects of one frame in drawing order: dicts with ``span`` (absolute text offsets)."""
    fr = frames(source)
    if not 0 <= frame < len(fr):
        return []
    s, e = fr[frame]
    out = []
    pos = s
    while True:
        m = _OBJ.search(source, pos, e)
        if not m:
            break
        parsed = parse_object(source, m.start())
        if parsed is None:
            pos = m.end()
            continue
        obj, end = parsed
        obj["span"] = (m.start(), end)
        out.append(obj)
        pos = end
    return out


def _num(v: float) -> str:
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def format_object(obj: dict) -> str:
    kind = obj["kind"]
    base = dict(DEFAULTS)
    if kind == "text":
        base.update({"line": "none", "align": "left"})
    geometry = ("x", "y", "x2", "y2") if kind in ("line", "arrow") else ("x", "y", "w", "h")
    parts = []
    for k in ("x", "y", "w", "h", "x2", "y2", "angle", "fill", "fill2", "grad", "line", "lw",
              "opacity", "size", "color", "align", "dash"):
        if k not in obj:
            continue
        if k in ("x", "y", "w", "h", "x2", "y2") and k not in geometry:
            continue
        if k in ("w", "h", "x2", "y2") and k not in geometry:
            continue
        v = obj[k]
        if k in ALWAYS or k in geometry:
            pass
        elif v == base.get(k) or (k in NUMERIC and _isnum(v) and _isnum(base.get(k))
                                  and abs(float(v) - float(base[k])) < 1e-9):
            continue
        if k == "fill2" and not v:
            continue
        parts.append(f"{k}={_num(float(v)) if k in NUMERIC and _isnum(v) else v}")
    return f"\\noorobj[{','.join(parts)}]{{{kind}}}{{{obj.get('arg', '')}}}"


def replace_object(source: str, frame: int, index: int, obj: dict) -> str:
    items = objects_in(source, frame)
    s, e = items[index]["span"]
    return source[:s] + format_object(obj) + source[e:]


def add_object(source: str, frame: int, obj: dict) -> str:
    fr = frames(source)
    s, e = fr[frame]
    close = source.rfind("\\end{frame}", s, e)
    line = "  " + format_object(obj) + "\n"
    head = "" if source[:close].endswith("\n") else "\n"
    return source[:close] + head + line + source[close:]


def delete_object(source: str, frame: int, index: int) -> str:
    items = objects_in(source, frame)
    s, e = items[index]["span"]
    while s > 0 and source[s - 1] in " \t":
        s -= 1
    if e < len(source) and source[e] == "\n":
        e += 1
    return source[:s] + source[e:]


def move_object(source: str, frame: int, index: int, delta: int) -> str:
    """Swap an object with its neighbour (later = drawn on top)."""
    items = objects_in(source, frame)
    j = index + delta
    if not 0 <= j < len(items):
        return source
    a, b = sorted((index, j))
    (as_, ae), (bs, be) = items[a]["span"], items[b]["span"]
    return source[:as_] + source[bs:be] + source[ae:bs] + source[as_:ae] + source[be:]


def slide_size_mm(source: str) -> tuple:
    """Page size in mm from the class option ``aspectratio`` (beamer default 4:3 = 128 x 96)."""
    m = re.search(r"\\documentclass\[([^\]]*)\]\{beamer\}", source)
    ratio = ""
    if m:
        a = re.search(r"aspectratio\s*=\s*(\d+)", m.group(1))
        ratio = a.group(1) if a else ""
    return {"1610": (160.0, 100.0), "169": (160.0, 90.0), "149": (140.0, 90.0),
            "141": (140.0, 99.0), "54": (125.0, 100.0), "43": (128.0, 96.0),
            "32": (135.0, 90.0)}.get(ratio, (128.0, 96.0))


# ---------------------------------------------------------------------- slide setup line
SETUP_KEYS = ("title-dx", "title-dy", "title-size", "body-top", "margin-left", "margin-right",
              "number-on-title", "number-x", "number-y", "number-size")
_SETUP = re.compile(r"\\noorslide\s*(?=\{)")


def read_setup(source: str) -> dict:
    m = _SETUP.search(source)
    if not m:
        return {}
    g = _group(source, m.end())
    return parse_options(g[0]) if g else {}


def write_setup(source: str, values: dict) -> str:
    """Put / replace the ``\\noorslide{...}`` line in the preamble (empty values are dropped;
    no values at all removes the line)."""
    parts = [f"{k}={values[k]}" for k in SETUP_KEYS if str(values.get(k, "")).strip() != ""]
    line = f"\\noorslide{{{','.join(parts)}}}" if parts else ""
    m = _SETUP.search(source)
    if m:
        g = _group(source, m.end())
        s, e = m.start(), (g[1] if g else m.end())
        if line:
            return source[:s] + line + source[e:]
        end = e + 1 if source[e:e + 1] == "\n" else e
        return source[:s] + source[end:]
    if not line:
        return source
    # after \usepackage{noorsuite} / the theme, before \begin{document}
    anchor = re.search(r"^\\title\b|^\\begin\{document\}", source, re.M)
    pos = anchor.start() if anchor else len(source)
    return source[:pos] + line + "\n" + source[pos:]
