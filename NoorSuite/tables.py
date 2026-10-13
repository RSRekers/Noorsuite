"""Tables from data objects, as LaTeX (``\\noortable{<id>}`` in a document).

A table is a :class:`~NoorSuite.model.TableModel` in the project: which data object, which
columns (with header, number format, alignment), which rows. The document only holds
``\\noortable{id-or-name}``; at build time it is replaced by the rendered ``tabular``, so the
table follows the data like a figure follows its sheet. Word / LibreOffice export gets the same
expanded text (pandoc turns the ``tabular`` into a real table).

Pure functions, no Qt.
"""
from __future__ import annotations

import math
import re

import numpy as np

FORMATS = ["auto", "fixed", "sig", "sci", "int"]
FORMAT_LABELS = ["Automatic (up to 6 digits)", "Fixed decimals", "Significant digits",
                 "Scientific (a x 10^b)", "Integer"]
ALIGNS = ["l", "c", "r"]
ALIGN_LABELS = ["Left", "Centre", "Right"]

_REF = re.compile(r"\\noortable\s*(?:\[[^\]]*\])?\s*\{([^{}]*)\}")
_COMMENT = re.compile(r"(?<!\\)%.*")
_ESC = {"&": r"\&", "%": r"\%", "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}",
        "~": r"\textasciitilde{}", "^": r"\textasciicircum{}", "\\": r"\textbackslash{}"}


def esc(text: str) -> str:
    """Plain text for a LaTeX cell; text containing ``$...$`` is passed through unchanged
    (so a header like ``$\\sigma$ (S cm$^{-1}$)`` works)."""
    text = str(text)
    if "$" in text:
        return text
    return "".join(_ESC.get(c, c) for c in text)


def normalize_columns(data, specs=None, digits=None, fmt=None) -> list:
    """Column spec dicts from the friendly forms: ``"I"``, ``("I", "Current (mA)")`` or a dict.
    ``None`` -> every column of ``data``. ``digits`` / ``fmt`` are defaults for plain names."""
    names = list(data.column_order) if data is not None else []
    out = []
    for s in (specs if specs else names):
        d = {"col": s} if isinstance(s, str) else (
            {"col": s[0], "header": s[1] if len(s) > 1 else ""} if isinstance(s, (tuple, list))
            else dict(s))
        d.setdefault("header", "")
        d.setdefault("fmt", fmt or "auto")
        d.setdefault("digits", 3 if digits is None else int(digits))
        d.setdefault("align", "r")
        d.setdefault("scale", 1.0)
        if d["fmt"] not in FORMATS:
            raise ValueError(f"unknown number format {d['fmt']!r}; use one of {FORMATS}")
        if data is not None and d["col"] not in data.columns:
            raise ValueError(f"data object {data.name!r} has no column {d['col']!r}")
        out.append(d)
    return out


def table_refs(source: str) -> list:
    """Distinct ``\\noortable`` keys of ``source`` in order (comments ignored)."""
    seen, out = set(), []
    for line in source.splitlines():
        for m in _REF.finditer(_COMMENT.sub("", line)):
            k = m.group(1).strip()
            if k and k not in seen:
                seen.add(k)
                out.append(k)
    return out


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")


def resolve_table(key: str, tables):
    """The table a key refers to: id, else name slug, else exact name."""
    tables = list(tables)
    for t in tables:
        if t.table_id == key:
            return t
    for t in tables:
        if _slug(t.name) == _slug(key) and _slug(key):
            return t
    return next((t for t in tables if t.name == key), None)


# ------------------------------------------------------------------ number formatting
def _minus(s: str) -> str:
    return "$-$" + s[1:] if s.startswith("-") else s


def format_number(v: float, fmt: str = "auto", digits: int = 3, missing: str = "--") -> str:
    """One cell: ``fmt`` in :data:`FORMATS`; NaN / inf -> ``missing``; negatives use a math
    minus; scientific values become ``1.23\\,$\\times$\\,10$^{-4}$``."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return missing
    if not math.isfinite(v):
        return missing
    digits = max(0, int(digits))
    if fmt == "int":
        s = str(int(round(v)))
    elif fmt == "fixed":
        s = f"{v:.{digits}f}"
    elif fmt == "sig":
        s = f"{v:.{max(1, digits)}g}"
    elif fmt == "sci":
        s = f"{v:.{digits}e}"
    else:
        s = f"{v:.6g}"
    if "e" in s.lower():
        mant, _, exp = s.lower().partition("e")
        return _minus(mant) + r"\,$\times$\,10$^{" + str(int(exp)) + "}$"
    return _minus(s)


# ------------------------------------------------------------------ rendering
def _row_indices(tm, n: int, sort_values=None) -> list:
    """Row numbers to show: optional sort, then the 1-based range ``row_start..row_end`` of
    that order, every ``step``-th, at most ``max_rows``."""
    idx = list(range(n))
    if sort_values is not None and len(sort_values) == n:
        key = np.where(np.isfinite(sort_values), sort_values, np.inf)
        idx = [int(i) for i in np.argsort(key, kind="stable")]
        if tm.descending:
            idx = idx[::-1]
    lo = max(1, int(tm.row_start or 1)) - 1
    hi = int(tm.row_end) if tm.row_end else n
    idx = idx[lo:hi][::max(1, int(tm.step or 1))]
    if tm.max_rows and int(tm.max_rows) > 0:
        idx = idx[:int(tm.max_rows)]
    return idx


def header_text(tm_col: dict, data) -> str:
    if tm_col.get("header"):
        return esc(tm_col["header"])
    name = tm_col.get("col", "")
    unit = (data.units.get(name, "") if data is not None else "") or ""
    return esc(name) + (f" ({esc(unit)})" if unit else "")


def render_table(tm, data) -> str:
    r"""LaTeX ``tabular`` (booktabs rules unless ``tm.booktabs`` is off) for ``tm`` over
    ``data``. Unknown columns become a visible placeholder cell."""
    cols = [c for c in tm.columns if c.get("col")]
    if data is None or not cols:
        return "\\fbox{\\texttt{table: no data}}"
    n = data.nrows
    sort_vals = data.columns.get(tm.sort_by) if tm.sort_by else None
    rows = _row_indices(tm, n, sort_vals)
    spec = "".join(c.get("align", "r") if c.get("align") in ALIGNS else "r" for c in cols)
    top, mid, bot = ("\\toprule", "\\midrule", "\\bottomrule") if tm.booktabs \
        else ("\\hline", "\\hline", "\\hline")
    lines = [f"\\begin{{tabular}}{{{spec}}}", top,
             " & ".join(header_text(c, data) for c in cols) + " \\\\", mid]
    for i in rows:
        cells = []
        for c in cols:
            arr = data.columns.get(c["col"])
            if arr is None or i >= len(arr):
                cells.append("--")
                continue
            v = float(arr[i]) * float(c.get("scale", 1.0) or 1.0)
            cells.append(format_number(v, c.get("fmt", "auto"), c.get("digits", 3)))
        lines.append(" & ".join(cells) + " \\\\")
    lines += [bot, "\\end{tabular}"]
    return "\n".join(lines)


def expand_tables(source: str, tables, repository: dict):
    r"""``source`` with every ``\noortable[opts]{key}`` replaced by the rendered tabular
    (wrapped in ``\resizebox``-free ``\centering`` when ``opts`` is ``center``). Adds
    ``\usepackage{booktabs}`` before ``\begin{document}`` when a table needs it.
    Returns ``(text, missing_keys)``."""
    tables = list(tables)
    missing = []
    used_booktabs = False

    def sub(m):
        nonlocal used_booktabs
        key = m.group(1).strip()
        tm = resolve_table(key, tables)
        if tm is None:
            missing.append(key)
            return f"\\fbox{{\\texttt{{missing table {esc(key)}}}}}"
        data = repository.get(tm.data_id) or next(
            (o for o in repository.values() if o.name == tm.data_id), None)
        used_booktabs = used_booktabs or bool(tm.booktabs)
        return render_table(tm, data)

    # keep comment lines untouched
    out = []
    for line in source.splitlines(keepends=True):
        code = _COMMENT.sub("", line)
        out.append(_REF.sub(sub, line) if _REF.search(code) and line.lstrip()[:1] != "%"
                   else line)
    text = "".join(out)
    if used_booktabs and not re.search(r"\\usepackage(?:\[[^\]]*\])?\{[^}]*booktabs", text):
        b = re.search(r"\\begin\{document\}", text)
        if b:
            text = text[:b.start()] + "\\usepackage{booktabs}\n" + text[b.start():]
    return text, missing


def table_snippet(table_id: str, name: str = "", caption: str = "", label: str = "") -> str:
    """A ``table`` environment around ``\\noortable`` (what *Insert table* writes)."""
    label = label or f"tab:{_slug(name) or table_id}"
    return (f"\\begin{{table}}[htbp]\n  \\centering\n  \\caption{{{caption or name or 'Caption.'}}}\n"
            f"  \\label{{{label}}}\n  \\noortable{{{table_id}}}\n\\end{{table}}\n")


def fingerprint(tm, data) -> str:
    """Changes when the table spec or the numbers it shows change."""
    import hashlib
    import json
    h = hashlib.md5(json.dumps(tm.to_dict(), sort_keys=True, default=str).encode())
    if data is not None:
        for c in tm.columns:
            arr = data.columns.get(c.get("col", ""))
            if arr is not None:
                h.update(np.ascontiguousarray(arr).tobytes())
    return h.hexdigest()
