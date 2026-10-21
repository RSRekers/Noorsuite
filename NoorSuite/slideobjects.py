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
_OBJ = re.compile(r"\\noor(?:master)?obj\s*")
MASTER = -1                                          # "frame" index of the master layout


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
    if m.group(0).startswith("\\noormasterobj"):
        obj["master"] = True
    return obj, content[1]


def _isnum(v) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def _preamble_span(source: str) -> tuple:
    b = re.search(r"\\begin\{document\}", source)
    return (0, b.start() if b else len(source))


def objects_in(source: str, frame: int) -> list:
    """Objects of one frame in drawing order: dicts with ``span`` (absolute text offsets).
    ``frame == MASTER`` -> the ``\\noormasterobj`` lines of the preamble (drawn on every slide)."""
    if frame == MASTER:
        s, e = _preamble_span(source)
    else:
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
    macro = "noormasterobj" if obj.get("master") else "noorobj"
    return f"\\{macro}[{','.join(parts)}]{{{kind}}}{{{obj.get('arg', '')}}}"


def replace_object(source: str, frame: int, index: int, obj: dict) -> str:
    items = objects_in(source, frame)
    s, e = items[index]["span"]
    return source[:s] + format_object(obj) + source[e:]


def add_object(source: str, frame: int, obj: dict) -> str:
    if frame == MASTER:
        obj = dict(obj, master=True)
        b = re.search(r"\\begin\{document\}", source)
        pos = b.start() if b else len(source)
        return source[:pos] + format_object(obj) + "\n" + source[pos:]
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
SETUP_KEYS = ("title-dx", "title-dy", "title-size", "body-size", "body-top", "margin-left", "margin-right",
              "number-on-title", "number-x", "number-y", "number-size",
              "title-x", "title-y", "title-w", "title-color", "body-x", "body-y", "body-w",
              "num-x", "num-y", "num-size", "num-color", "src-x", "src-y", "src-w", "src-size")
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


# ---------------------------------------------------------------------- master placeholders
PLACEHOLDERS = ("title", "body", "number", "source")
_PH_KEYS = {
    "title": {"x": "title-x", "y": "title-y", "w": "title-w", "size": "title-size",
              "color": "title-color"},
    "body": {"x": "body-x", "y": "body-y", "w": "body-w"},
    "number": {"x": "num-x", "y": "num-y", "size": "num-size", "color": "num-color"},
    "source": {"x": "src-x", "y": "src-y", "w": "src-w", "size": "src-size"},
}
PH_LABELS = {"title": "Frame title", "body": "Body text area", "number": "Slide number",
             "source": "Source line"}


def placeholder_defaults(page_cm) -> dict:
    pw, ph = page_cm
    return {
        "title": {"x": 0.9, "y": 0.35, "w": round(pw - 1.8 - 1.6, 2), "h": 1.2, "size": 20.0,
                  "color": ""},
        "body": {"x": 0.9, "y": 1.9, "w": round(pw - 1.8, 2), "h": round(ph - 1.9 - 1.1, 2),
                 "size": 0.0, "color": ""},
        "number": {"x": round(pw - 1.5, 2), "y": round(ph - 0.9, 2), "w": 1.0, "h": 0.5,
                   "size": 10.0, "color": "000000"},
        "source": {"x": 0.9, "y": round(ph - 0.85, 2), "w": round(pw - 3.2, 2), "h": 0.5,
                   "size": 8.0, "color": ""},
    }


def has_placeholders(source: str) -> bool:
    setup = read_setup(source)
    return any(k in setup for k in ("title-x", "body-x", "num-x", "src-x"))


def placeholders(source: str, page_cm, detected: dict | None = None) -> list:
    """The four layout placeholders as pseudo objects (kind ``ph-title`` ...): values from the
    ``\\\\noorslide`` line, else ``detected`` (measured in the compiled PDF), else defaults."""
    setup = read_setup(source)
    base = placeholder_defaults(page_cm)
    out = []
    for name in PLACEHOLDERS:
        g = dict(base[name])
        if detected and name in detected:
            g.update(detected[name])
        for field, key in _PH_KEYS[name].items():
            if key in setup and setup[key] != "":
                g[field] = setup[key] if field == "color" else float(setup[key])
        out.append({"kind": f"ph-{name}", "x": float(g["x"]), "y": float(g["y"]),
                    "w": float(g["w"]), "h": float(g["h"]), "angle": 0.0,
                    "size": float(g.get("size", 0) or 0), "color": g.get("color", "") or "",
                    "arg": PH_LABELS[name], "fill": "none", "line": "000000", "lw": 1.0,
                    "opacity": 1.0, "align": "left", "dash": "dashed", "x2": 0.0, "y2": 0.0,
                    "fill2": "", "grad": "vertical"})
    return out


def write_placeholders(source: str, objs: list) -> str:
    """Store all placeholder geometries in the ``\\\\noorslide`` line (the first edit takes the whole
    layout over, so what the editor shows is what LaTeX produces)."""
    setup = read_setup(source)
    for o in objs:
        name = o["kind"][3:]
        if name not in _PH_KEYS:
            continue
        for field, key in _PH_KEYS[name].items():
            v = o.get(field)
            if v in (None, ""):
                setup.pop(key, None)
                continue
            setup[key] = v if field == "color" else _num(float(v))
    if "body-y" in setup:                               # title box height = where the body starts
        pass
    return write_setup(source, setup)
