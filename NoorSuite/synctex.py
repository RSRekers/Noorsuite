"""Minimal SyncTeX reader: PDF position -> line of the TeX source ("inverse search").

``parse(path)`` reads ``main.synctex.gz`` (or plain ``.synctex``); ``locate(data, page, x, y)``
returns the line number in the compiled file for a point given in PDF points (bp, origin top-left).
``match_line`` maps a line of the *build copy* to the line of the user's editor text (the build copy
has a few edits -- figures, references, tables -- so line numbers can drift).
"""

from __future__ import annotations

import difflib
import gzip
import re
from pathlib import Path

_REC = re.compile(r"^([\[\(hvkgx\$])(\d+),(\d+)(?:,-?\d+)?:(-?\d+),(-?\d+)(?::(-?\d+)(?:,(-?\d+),(-?\d+))?)?")


def parse(path) -> dict | None:
    """``{"inputs": {tag: name}, "pages": {n: [(kind, tag, line, x, y, w, h, d)]}}`` in bp."""
    p = Path(path)
    if not p.is_file():
        return None
    try:
        raw = gzip.open(p, "rt", encoding="utf-8", errors="replace").read() \
            if p.suffix == ".gz" else p.read_text(encoding="utf-8", errors="replace")
    except (OSError, EOFError):
        return None
    unit, mag, xoff, yoff = 1.0, 1000.0, 0.0, 0.0
    inputs, pages, page = {}, {}, None
    to_bp = lambda v: v * unit * (mag / 1000.0) / 65536.0 * 72.0 / 72.27   # noqa: E731
    in_content = False
    for line in raw.splitlines():
        if line.startswith("Input:"):
            parts = line.split(":", 2)
            if len(parts) == 3 and parts[1].isdigit():
                inputs[int(parts[1])] = parts[2].replace("\\", "/")
        elif line.startswith("Unit:"):
            unit = float(line[5:] or 1)
        elif line.startswith("Magnification:"):
            mag = float(line[14:] or 1000)
        elif line.startswith("X Offset:"):
            xoff = float(line[9:] or 0)
        elif line.startswith("Y Offset:"):
            yoff = float(line[9:] or 0)
        elif line.startswith("Content:"):
            in_content = True
        elif line.startswith("Postamble:"):
            break
        elif in_content:
            if line.startswith("{"):
                page = int(line[1:])
                pages[page] = []
            elif page is not None:
                m = _REC.match(line)
                if m:
                    kind, tag, ln = m.group(1), int(m.group(2)), int(m.group(3))
                    x, y = to_bp(int(m.group(4)) + xoff), to_bp(int(m.group(5)) + yoff)
                    w = to_bp(int(m.group(6))) if m.group(6) else 0.0
                    h = to_bp(int(m.group(7))) if m.group(7) else 0.0
                    d = to_bp(int(m.group(8))) if m.group(8) else 0.0
                    pages[page].append((kind, tag, ln, x, y, w, h, d))
    return {"inputs": inputs, "pages": pages} if pages else None


def main_tags(data: dict, names=("main.tex",)) -> set:
    return {t for t, n in data["inputs"].items() if Path(n).name in names}


def locate(data: dict, page: int, x: float, y: float, names=("main.tex",)):
    """Line (1-based) of the main file closest to the point, or None. ``page`` is 1-based."""
    tags = main_tags(data, names)
    best, best_d = None, None
    for kind, tag, ln, rx, ry, w, h, d in data["pages"].get(page, []):
        if tag not in tags or ln <= 0 or kind in "[(":    # boxes carry the paragraph's end line
            continue
        pts = [(rx, ry)]
        if kind in ("h", "k") and w:
            pts.append((rx + w, ry))
            pts.append((rx + w / 2, ry))
        for px, py in pts:
            dist = (px - x) ** 2 + 4.0 * (py - 0.35 * h - y) ** 2
            if best_d is None or dist < best_d:
                best, best_d = ln, dist
    return best


def match_line(build_lines: list, source_lines: list, line: int) -> int:
    """0-based editor line that corresponds to 1-based ``line`` of the build copy.

    The two texts are aligned as a whole (difflib over the stripped lines), so repeated lines
    (item, centering ...) and blocks the build copy inserted (preamble additions, expanded
    tables, the numbered reference list) do not throw the position off."""
    if not source_lines:
        return 0
    if not build_lines:
        return min(max(line - 1, 0), len(source_lines) - 1)
    idx = min(max(line - 1, 0), len(build_lines) - 1)
    b = [t.strip() for t in build_lines]
    s = [t.strip() for t in source_lines]
    last = len(s) - 1
    sm = difflib.SequenceMatcher(None, b, s, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if not (i1 <= idx < i2):
            continue
        if tag == "equal":
            return min(j1 + (idx - i1), last)
        if j2 <= j1:                        # the build copy added this line: stay next to it
            return min(max(j1 - 1, 0), last)
        best, score = None, -1.0
        for j in range(j1, j2):             # replaced block: the most similar line in it
            r = difflib.SequenceMatcher(None, b[idx], s[j]).ratio()
            if r > score:
                best, score = j, r
        if score < 0.3:                     # nothing alike: keep the relative position
            best = j1 + int((idx - i1) * (j2 - j1) / max(1, i2 - i1))
        return min(best, last)
    return min(idx, last)
