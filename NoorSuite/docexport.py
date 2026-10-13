"""Word (.docx) / LibreOffice (.odt) export of a LaTeX document, through pandoc.

LaTeX -> Word is never lossless, so the source is prepared first to remove what pandoc cannot
know about:

* ``\\noorsheet`` / ``\\noorfile`` figures become ``\\includegraphics`` of PNGs rendered at
  300 dpi at the figure's own size;
* the numbered reference list NoorSuite writes itself (``thebibliography``) becomes plain
  numbered paragraphs (the in-text superscripts are already text);
* ``\\ref`` / ``\\autoref`` / ``\\eqref`` to figures, tables and sections are replaced by their
  numbers, and figure / table captions get their "Figure N." prefix (pandoc would leave them
  unnumbered).

Equations are written as native Word / ODF equations by pandoc; equation *numbers* are not
carried over. Pure functions here (no Qt) plus :func:`find_pandoc` and :func:`run_pandoc`.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import latex

FORMATS = {"docx": "Word (.docx)", "odt": "LibreOffice (.odt)"}
_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
INSTALL_HINT = ("Word / LibreOffice export needs pandoc. Install it with\n"
                "  conda install -n noorlatex -c conda-forge pandoc\n"
                "(or from https://pandoc.org/installing.html) and set $NOORSUITE_PANDOC / "
                "~/.noorsuite/config.json {\"pandoc\": \"<path>\"} if it is not found.")


def find_pandoc() -> str | None:
    """``$NOORSUITE_PANDOC``, ``config.json`` ``pandoc``, PATH, the ``noorlatex`` conda env,
    then the default Windows install folders."""
    explicit = os.environ.get("NOORSUITE_PANDOC", "")
    try:
        explicit = explicit or json.loads(latex._config_path().read_text(
            encoding="utf-8")).get("pandoc", "")
    except (OSError, ValueError):
        pass
    if explicit and (shutil.which(explicit) or os.path.isfile(explicit)):
        return shutil.which(explicit) or explicit
    found = shutil.which("pandoc")
    if found:
        return found
    for d in latex._candidate_dirs():
        for ext in (".exe", ""):
            p = d / ("pandoc" + ext)
            if p.is_file():
                return str(p)
    for base in (os.environ.get("LOCALAPPDATA", ""), os.environ.get("ProgramFiles", "")):
        if base:
            p = Path(base) / "Pandoc" / "pandoc.exe"
            if p.is_file():
                return str(p)
    return None


# --------------------------------------------------------------------- source rewriting
_COMMENT = re.compile(r"(?<!\\)%.*")
_FIG_CMD = re.compile(r"\\(noorsheet|noorfile)\s*(\[[^\]]*\])?\s*\{([^{}]*)\}")


def swap_figures(src: str, files: dict) -> tuple[str, list]:
    """Replace ``\\noorsheet`` / ``\\noorfile`` by ``\\includegraphics`` of the PNG listed in
    ``files`` ({(cmd, key): relative path}). Returns the text and the keys without a file."""
    missing = []

    def sub(m):
        cmd, opts, key = m.group(1), m.group(2) or "", m.group(3).strip()
        path = files.get((cmd, key))
        if not path:
            missing.append(key)
            return rf"\textbf{{[missing figure {latex_escape(key)}]}}"
        return rf"\includegraphics{opts}{{{path}}}"

    out = []
    for line in src.splitlines(keepends=True):         # never touch text inside a % comment
        c = re.search(r"(?<!\\)%", line)
        code, comment = (line[:c.start()], line[c.start():]) if c else (line, "")
        out.append(_FIG_CMD.sub(sub, code) + comment)
    return "".join(out), missing


def latex_escape(text: str) -> str:
    return re.sub(r"([#$%&_{}])", r"\\\1", text)


_BIB_ENV = re.compile(r"\\begin\{thebibliography\}\{[^}]*\}(.*?)\\end\{thebibliography\}", re.S)
_BIB_ITEM = re.compile(r"\\bibitem(?:\[([^\]]*)\])?\{[^}]*\}\s*")


def bibliography_to_paragraphs(src: str, heading: str = "References") -> str:
    """The ``thebibliography`` environment as a starred section of numbered paragraphs."""
    def sub(m):
        body = m.group(1)
        parts = _BIB_ITEM.split(body)
        # split() returns [text0, label1, text1, label2, text2, ...]
        paras, n = [], 0
        for i in range(1, len(parts) - 1, 2):
            n += 1
            label = parts[i] or f"({n})"
            text = " ".join(parts[i + 1].split())
            paras.append(rf"\noindent {label}\ {text}" "\n")
        return rf"\section*{{{heading}}}" "\n\n" + "\n".join(paras)

    return _BIB_ENV.sub(sub, src)


_SEC = re.compile(r"\\(section|subsection|subsubsection)\*?\s*\{")
_TOKEN = re.compile(
    r"\\(?P<sec>section|subsection|subsubsection)(?P<star>\*)?\s*\{"
    r"|\\begin\{(?P<eqb>equation)\}|\\end\{(?P<eqe>equation)\}"
    r"|\\begin\{(?P<env>figure|figure\*|table|table\*)\}"
    r"|\\end\{(?P<endenv>figure|figure\*|table|table\*)\}"
    r"|\\caption\s*(?:\[[^\]]*\])?\s*\{"
    r"|\\label\s*\{(?P<label>[^{}]*)\}")


_PREFIX_RE = re.compile(
    r"\\renewcommand\s*\{\\the(figure|table|equation)\}\s*\{([A-Za-z]*)\\arabic\{")


def counter_prefixes(src: str) -> dict:
    """``{"figure": "S", ...}`` from the Supporting-Information convention of redefining the
    figure / table / equation counters with a letter prefix; captions, references and equation
    numbers carry it."""
    code = _COMMENT.sub("", src)
    return {m.group(1): m.group(2) for m in _PREFIX_RE.finditer(code)}


def number_references(src: str, equation_labels: dict | None = None) -> tuple[str, dict]:
    """Number figures / tables / sections exactly as LaTeX would and resolve ``\\ref``,
    ``\\autoref``, ``\\cref`` and ``\\eqref`` to them. Returns ``(text, {label: text})``."""
    code = _COMMENT.sub("", src)
    labels: dict = {}
    pre = counter_prefixes(src)
    for k, n in (equation_labels or {}).items():          # numbered by equations_to_tables
        labels[k] = ("Equation", pre.get("equation", "") + str(n))
    sec = [0, 0, 0]
    fig = tab = eq = 0
    cur = None                                   # ("figure", n) while inside an environment
    last_sec = ""
    inserts = []                                 # (position, text): caption prefixes
    for m in _TOKEN.finditer(code):
        if m.group("sec") and not m.group("star"):
            lvl = ("section", "subsection", "subsubsection").index(m.group("sec"))
            sec[lvl] += 1
            for j in range(lvl + 1, 3):
                sec[j] = 0
            last_sec = ".".join(str(x) for x in sec[:lvl + 1])
        elif m.group("eqb"):
            eq += 1
            cur = ("Equation", eq)
        elif m.group("eqe"):
            cur = None
        elif m.group("env"):
            if m.group("env").startswith("figure"):
                fig += 1
                cur = ("Figure", fig)
            else:
                tab += 1
                cur = ("Table", tab)
        elif m.group("endenv"):
            cur = None
        elif m.group(0).startswith("\\caption"):
            if cur is not None:
                inserts.append((m.end(), f"{cur[0]} {pre.get(cur[0].lower(), '')}{cur[1]}. "))
        elif m.group("label") is not None:
            key = m.group("label").strip()
            if cur is not None:
                labels[key] = (cur[0], pre.get(cur[0].lower(), "") + str(cur[1]))
            elif last_sec:
                labels[key] = ("Section", last_sec)
    # caption prefixes (insert from the back so positions stay valid; map back via code==src
    # only when no comments were stripped -- otherwise recompute on the original)
    out = src
    if code == src:
        for pos, text in sorted(inserts, reverse=True):
            out = out[:pos] + text + out[pos:]
    else:
        out = _prefix_captions(src, pre)

    def ref(m):
        cmd, key = m.group(1), m.group(2).strip()
        kind_n = labels.get(key)
        if kind_n is None:
            return f"[{key}]"
        kind, n = kind_n
        if cmd in ("autoref", "cref", "Cref"):
            return f"{kind} {n}"
        if cmd == "eqref":
            return f"({n})"
        return n

    out = re.sub(r"\\(ref|autoref|cref|Cref|eqref)\s*\{([^{}]*)\}", ref, out)
    return out, {k: f"{v[0]} {v[1]}" for k, v in labels.items()}


def _prefix_captions(src: str, pre: dict | None = None) -> str:
    """Caption prefixes when the text has comments: redo the scan on the commented source."""
    out, fig, tab, cur, pos = [], 0, 0, None, 0
    for m in re.finditer(r"\\begin\{(figure\*?|table\*?)\}|\\end\{(figure\*?|table\*?)\}"
                         r"|\\caption\s*(?:\[[^\]]*\])?\s*\{", src):
        line_start = src.rfind("\n", 0, m.start()) + 1
        if re.search(r"(?<!\\)%", src[line_start:m.start()]):
            continue                                        # inside a comment
        if m.group(1):
            if m.group(1).startswith("figure"):
                fig += 1
                cur = ("Figure", fig)
            else:
                tab += 1
                cur = ("Table", tab)
        elif m.group(2):
            cur = None
        elif cur is not None:
            out.append(src[pos:m.end()])
            out.append(f"{cur[0]} {(pre or {}).get(cur[0].lower(), '')}{cur[1]}. ")
            pos = m.end()
    out.append(src[pos:])
    return "".join(out)


_EQ_ENV = re.compile(
    r"\\begin\{(equation|align|gather|eqnarray)\}(.*?)\\end\{\1\}", re.S)
_MARK = "\u2060"                                   # invisible marker in the empty left cell


def _split_top(text: str, sep: str) -> list:
    r"""Split ``text`` on ``sep`` (``\\`` or ``&``) where it is not inside braces or a nested
    ``\begin{..}...\end{..}`` (e.g. ``aligned``)."""
    parts, buf, depth, env, i = [], [], 0, 0, 0
    while i < len(text):
        if text.startswith("\\begin{", i):
            env += 1
        elif text.startswith("\\end{", i):
            env = max(0, env - 1)
        c = text[i]
        if c == "\\" and sep == "\\\\" and text.startswith("\\\\", i) and not depth and not env:
            parts.append("".join(buf))
            buf = []
            i += 2
            m = re.match(r"\s*\[[^\]]*\]", text[i:])      # \\[3pt] spacing option
            if m:
                i += m.end()
            continue
        if c == "\\":
            buf.append(text[i:i + 2])
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth = max(0, depth - 1)
        elif c == "&" and sep == "&" and not depth and not env:
            parts.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    parts.append("".join(buf))
    return parts


def equations_to_tables(src: str, prefix: str = "") -> tuple[str, int, dict]:
    r"""Numbered ``equation`` / ``align`` / ``gather`` / ``eqnarray`` environments as borderless
    tables: an empty marker cell on the left, the math in the middle, ``(n)`` on the right.
    ``equation`` / ``gather`` rows have one middle cell; ``align`` / ``eqnarray`` rows are split at
    the first ``&`` into two cells (right- / left-aligned, so the relation signs line up).
    Starred environments, ``\nonumber`` rows and ``\[ \]`` stay plain display math / unnumbered.
    Returns ``(text, how_many_numbers, {label: number})``."""
    masked = _COMMENT.sub(lambda m: " " * len(m.group(0)), src)
    out, pos, n, labels = [], 0, 0, {}
    for m in _EQ_ENV.finditer(masked):
        kind = m.group(1)
        inner = _COMMENT.sub("", src[m.start(2):m.end(2)])
        rows = [r for r in _split_top(inner, "\\\\")]
        if kind != "equation":
            rows = [r for r in rows if r.strip()]       # trailing \\ -> empty last row
        lines = []
        for row in rows:
            numbered = not re.search(r"\\(?:nonumber|notag)\b", row)
            keys = re.findall(r"\\label\s*\{([^}]*)\}", row)
            row = re.sub(r"\\(?:label\s*\{[^}]*\}|nonumber|notag)", "", row)
            number = ""
            if numbered:
                n += 1
                number = f"({prefix}{n})"
                for k in keys:
                    labels[k.strip()] = n
            cell = lambda t: f"$\\displaystyle {' '.join(t.split())}$" if t.strip() else ""  # noqa
            if kind in ("align", "eqnarray"):
                bits = _split_top(row, "&")
                left, right = (bits[0], " ".join(bits[1:])) if len(bits) > 1 else ("", row)
                lines.append(f"{_MARK} & {cell(left)} & {cell(right)} & {number} \\\\")
            else:
                lines.append(f"{_MARK} & {cell(row)} & {number} \\\\")
        if kind in ("align", "eqnarray"):
            spec = "p{0.08\\linewidth}p{0.38\\linewidth}p{0.42\\linewidth}p{0.08\\linewidth}"
        else:
            spec = "p{0.08\\linewidth}p{0.80\\linewidth}p{0.08\\linewidth}"
        out.append(src[pos:m.start()])
        out.append(f"\\begin{{longtable}}{{{spec}}}\n" + "\n".join(lines) +
                   "\n\\end{longtable}")
        pos = m.end()
    out.append(src[pos:])
    return "".join(out), n, labels


_TBL = re.compile(r"<w:tbl>.*?</w:tbl>", re.S)


def _set_jc(cell: str, value: str) -> str:
    if "<w:jc " in cell:
        return re.sub(r'<w:jc w:val="[a-z]+"\s*/>', f'<w:jc w:val="{value}" />', cell)
    if "<w:pPr>" in cell:
        return cell.replace("<w:pPr>", f'<w:pPr><w:jc w:val="{value}" />', 1)
    return cell.replace("<w:p>", f'<w:p><w:pPr><w:jc w:val="{value}" /></w:pPr>', 1)


def style_equation_tables(docx_path) -> int:
    """Post-process a pandoc .docx: the tables made by :func:`equations_to_tables` (3 or 4
    columns, marker in every first cell) get centred / aligned cells, right-aligned numbers,
    vertically centred rows and no borders. Returns how many tables."""
    import zipfile
    count = 0

    def fix(m):
        nonlocal count
        t = m.group(0)
        ncol = t.count("<w:gridCol")
        rows = re.findall(r"<w:tr>.*?</w:tr>", t, re.S)
        if ncol not in (3, 4) or not rows:
            return t
        txt = lambda c: "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", c)).strip()  # noqa: E731
        new_rows = []
        for r in rows:
            cells = re.findall(r"<w:tc>.*?</w:tc>", r, re.S)
            if len(cells) != ncol or txt(cells[0]) != _MARK \
                    or not re.fullmatch(r"(\(\d+\))?", txt(cells[-1])):
                return t
            aligns = ["left"] + (["center"] if ncol == 3 else ["right", "left"]) + ["right"]
            rr = r
            for c, a in zip(cells, aligns):
                rr = rr.replace(c, _set_jc(c, a), 1)
            new_rows.append((r, rr))
        for old, new in new_rows:
            t = t.replace(old, new, 1)
        count += 1
        t = re.sub(r"<w:tcPr\s*/>", '<w:tcPr><w:vAlign w:val="center" /></w:tcPr>', t)
        none = "".join(f'<w:{e} w:val="nil" />'
                       for e in ("top", "left", "bottom", "right", "insideH", "insideV"))
        return re.sub(r"(<w:tblLayout[^>]*/>)", r"\1<w:tblBorders>" + none + "</w:tblBorders>",
                      t, count=1)

    try:
        zin = zipfile.ZipFile(docx_path)
        items = [(i, zin.read(i.filename)) for i in zin.infolist()]
        zin.close()
    except (OSError, zipfile.BadZipFile):
        return 0
    with zipfile.ZipFile(docx_path, "w", zipfile.ZIP_DEFLATED) as zout:
        for info, data in items:
            if info.filename == "word/document.xml":
                data = _TBL.sub(fix, data.decode("utf-8")).encode("utf-8")
            zout.writestr(info, data)
    return count


# ------------------------------------------------------------------------ orchestration
def prepare_office_source(source: str, records: list, cite_mode: str, sheets, assets,
                          fig_dir, sheet_png, asset_png) -> tuple[str, list, list]:
    """The text pandoc reads, plus ``notes`` and ``missing`` figure keys.

    ``sheets`` / ``assets``: the project's sheet / asset models; ``sheet_png(sm, path)`` and
    ``asset_png(asset, path)`` write a PNG (callers differ: window vs. headless)."""
    fig_dir = Path(fig_dir)
    fig_dir.mkdir(parents=True, exist_ok=True)
    # the project's native numbered ACS list is what Word gets (BibTeX styles cannot be
    # rendered without a CSL pipeline)
    text, notes, info = latex.prepare_build_source(source, records, "acs-guide", cite_mode)
    for k in info["undefined"]:
        notes.append(f"citation key {k!r} is not in the project's references")
    files: dict = {}
    for key in latex.figure_refs(text):
        sm = latex.resolve_ref(key, sheets)
        if sm is None or not re.fullmatch(r"[A-Za-z0-9._-]+", key):
            continue
        dest = fig_dir / f"sheet-{key}.png"
        try:
            sheet_png(sm, str(dest))
            files[("noorsheet", key)] = f"{fig_dir.name}/{dest.name}"
        except Exception as exc:                    # keep exporting the rest
            notes.append(f"figure {key}: {exc}")
    for key in latex.asset_refs(text):
        a = latex.resolve_asset(key, assets)
        if a is None or not re.fullmatch(r"[A-Za-z0-9._-]+", key):
            continue
        dest = fig_dir / f"asset-{key}.png"
        try:
            asset_png(a, str(dest))
            files[("noorfile", key)] = f"{fig_dir.name}/{dest.name}"
        except Exception as exc:
            notes.append(f"figure file {key}: {exc}")
    text, missing = swap_figures(text, files)
    text = bibliography_to_paragraphs(text)
    text, _n_eq, eq_labels = equations_to_tables(
        text, counter_prefixes(text).get("equation", ""))
    text, _labels = number_references(text, eq_labels)
    text = re.sub(r"\\usepackage(\[[^\]]*\])?\{noorsuite\}", "", text)
    return text, notes, missing


def make_reference_docx(template_id: str, workdir, pandoc: str) -> str | None:
    """pandoc's default reference.docx restyled to the journal layout (font, size, line
    spacing, margins); the path, or ``None`` if it could not be made."""
    from . import templates
    workdir = Path(workdir)
    default = workdir / "reference-default.docx"
    try:
        res = subprocess.run([pandoc, "--print-default-data-file", "reference.docx"],
                             capture_output=True, timeout=60,
                             creationflags=_CREATE_NO_WINDOW)
        if res.returncode != 0 or not res.stdout:
            return None
        default.write_bytes(res.stdout)
    except (OSError, subprocess.SubprocessError):
        return None
    out = workdir / "reference.docx"
    return str(out) if templates.write_reference_docx(template_id, out, default) else None


def run_pandoc(workdir, tex_name: str, out_path, fmt: str, pandoc: str | None = None,
               timeout: int = 180, reference_doc: str | None = None) -> tuple[bool, str]:
    """``pandoc <workdir>/<tex_name> -> out_path`` (``fmt``: ``docx`` | ``odt``)."""
    pandoc = pandoc or find_pandoc()
    if not pandoc:
        return False, INSTALL_HINT
    if fmt not in FORMATS:
        return False, f"unknown format {fmt!r}"
    cmd = [pandoc, tex_name, "-f", "latex", "-t", fmt, "-o", str(out_path), "--standalone",
           "--number-sections", f"--resource-path={workdir}"]
    if reference_doc and fmt == "docx":
        cmd.append(f"--reference-doc={reference_doc}")
    try:
        res = subprocess.run(cmd, cwd=str(workdir), capture_output=True, text=True,
                             timeout=timeout, creationflags=_CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"could not run pandoc: {exc}"
    log = (res.stdout or "") + (res.stderr or "")
    ok = res.returncode == 0 and Path(out_path).is_file()
    return ok, log.strip()


def export_office(source: str, records: list, cite_mode: str, sheets, assets, out_path,
                  fmt: str, workdir, sheet_png, asset_png, pandoc=None) -> tuple[bool, str]:
    """Prepare + run pandoc. ``workdir`` is a scratch folder (figures are written there)."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    text, notes, missing = prepare_office_source(
        source, records, cite_mode, sheets, assets, workdir / "office-figures",
        sheet_png, asset_png)
    (workdir / "main.tex").write_text(text, encoding="utf-8")
    ref = None
    if fmt == "docx":
        from . import templates
        ref = make_reference_docx(templates.detect(source), workdir,
                                  pandoc or find_pandoc() or "pandoc")
    ok, log = run_pandoc(workdir, "main.tex", out_path, fmt, pandoc, reference_doc=ref)
    if ok and fmt == "docx":
        style_equation_tables(out_path)
    extra = [f"note: {n}" for n in notes] + [f"missing figure: {k}" for k in missing]
    return ok, "\n".join(extra + ([log] if log else []))
