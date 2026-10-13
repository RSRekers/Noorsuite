"""LaTeX support: ``\\noorsheet`` figures, engine discovery, building a PDF. Qt-free, stdlib only.

A NoorSuite *document* is a ``.tex`` source stored in the project. Figures are referenced
by sheet::

    \\usepackage{noorsuite}
    ...
    \\begin{figure}[htbp]
      \\centering
      \\noorsheet{a1b2c3d4}%  IV curve, sample A3
      \\caption{Current-voltage curve of sample A3 at 4 K.}
    \\end{figure}

``\\noorsheet[<includegraphics options>]{<sheet id or slug>}`` includes that sheet as a
vector PDF at its own figure size (so text in the figure has the printed size) and wraps it in
a ``noorsuite://sheet/<key>`` link, which is how the in-app preview knows what was clicked.

Build layout (a work dir): ``main.tex``, ``noorsuite.sty``, ``noorsuite-figures/<key>.pdf``.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

FIGURES_DIR = "noorsuite-figures"
DEFAULT_FIG_CM = (12.0, 8.0)      # figure size for sheets that have no explicit cm size
LINK_SCHEME = "noorsuite://sheet/"

STY = r"""\NeedsTeXFormat{LaTeX2e}
\ProvidesPackage{noorsuite}[2026/10/12 NoorSuite sheets as figures]
\RequirePackage{graphicx}
\makeatletter
\@ifpackageloaded{hyperref}{}{\RequirePackage{hyperref}}
\makeatother
% \noorsheet[includegraphics options]{sheet-id-or-slug}
\newcommand{\noorsheet}[2][]{%
  \begingroup
  \hypersetup{pdfborder={0 0 0},colorlinks=false}%
  \IfFileExists{noorsuite-figures/#2.pdf}%
    {\href{noorsuite://sheet/#2}{\includegraphics[#1]{noorsuite-figures/#2.pdf}}}%
    {\fbox{\texttt{missing NoorSuite sheet \detokenize{#2}}}}%
  \endgroup}
% \noorfile[includegraphics options]{asset-id-or-name}: an imported SVG / PDF / PNG / JPG
\newcommand{\noorfile}[2][]{%
  \begingroup
  \hypersetup{pdfborder={0 0 0},colorlinks=false}%
  \IfFileExists{noorsuite-figures/asset-#2.pdf}%
    {\href{noorsuite://sheet/asset:#2}{\includegraphics[#1]{noorsuite-figures/asset-#2.pdf}}}%
    {\IfFileExists{noorsuite-figures/asset-#2.png}%
      {\href{noorsuite://sheet/asset:#2}{\includegraphics[#1]{noorsuite-figures/asset-#2.png}}}%
      {\IfFileExists{noorsuite-figures/asset-#2.jpg}%
        {\href{noorsuite://sheet/asset:#2}{\includegraphics[#1]{noorsuite-figures/asset-#2.jpg}}}%
        {\fbox{\texttt{missing NoorSuite file \detokenize{#2}}}}}}%
  \endgroup}
\endinput
"""

ARTICLE_TEMPLATE = r"""\documentclass[11pt,a4paper]{article}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage{amsmath,amssymb}
\usepackage[margin=2.5cm]{geometry}
\usepackage{hyperref}
\usepackage{noorsuite}   % \noorsheet{...}: NoorSuite sheets as figures

\title{@TITLE@}
\author{}
\date{\today}

\begin{document}
\maketitle

\begin{abstract}
Abstract.
\end{abstract}

\section{Introduction}
Write your text here. Insert a figure with \emph{Insert figure...} in the toolbar, or by hand:
\noorsheet{sheet-id}

\section{Results}

\end{document}
"""

# Reference-list formats. ``packages`` are the preamble lines the format needs (natbib for
# author-year / superscript citations, achemso for the ACS journal look).
BIB_STYLES = [
    {"id": "acs-guide",
     "label": "ACS Guide (2025 rev.) - numbers by occurrence, DOI links (default)",
     "bst": "", "packages": [], "native": "acs"},
    {"id": "acs", "label": "ACS - achemso.bst, superscript numbers (older layout)",
     "bst": "achemso", "packages": [r"\usepackage[super,sort&compress]{natbib}"]},
    {"id": "acs-notitle", "label": "ACS journal - achemso package, no article titles",
     "bst": "achemso", "packages": [r"\usepackage{achemso}"]},
    {"id": "plain", "label": "plain - numbered, sorted by author", "bst": "plain", "packages": []},
    {"id": "unsrt", "label": "unsrt - numbered, in order of citation", "bst": "unsrt",
     "packages": []},
    {"id": "ieeetr", "label": "ieeetr - IEEE-like numbered", "bst": "ieeetr", "packages": []},
    {"id": "abbrv", "label": "abbrv - numbered, abbreviated names", "bst": "abbrv",
     "packages": []},
    {"id": "alpha", "label": "alpha - [Smi20] labels", "bst": "alpha", "packages": []},
    {"id": "plainnat", "label": "plainnat - author-year (natbib)", "bst": "plainnat",
     "packages": [r"\usepackage{natbib}"]},
    {"id": "abbrvnat", "label": "abbrvnat - author-year, abbreviated (natbib)",
     "bst": "abbrvnat", "packages": [r"\usepackage{natbib}"]},
    {"id": "unsrtnat", "label": "unsrtnat - author-year, citation order (natbib)",
     "bst": "unsrtnat", "packages": [r"\usepackage{natbib}"]},
    {"id": "apalike", "label": "apalike - author-year (APA-like, natbib)", "bst": "apalike",
     "packages": [r"\usepackage{natbib}"]},
]
DEFAULT_BIB_STYLE = "acs-guide"


def bib_style(style_id: str | None = None) -> dict:
    wanted = style_id or get_bib_style()
    return next((s for s in BIB_STYLES if s["id"] == wanted), BIB_STYLES[0])


def get_bib_style() -> str:
    """The default reference-list format: ``$NOORSUITE_BIB_STYLE``, else the user's choice in
    ``~/.noorsuite/config.json``, else ACS."""
    if os.environ.get("NOORSUITE_BIB_STYLE"):
        return os.environ["NOORSUITE_BIB_STYLE"]
    try:
        return json.loads(_config_path().read_text(encoding="utf-8")).get(
            "bib_style", DEFAULT_BIB_STYLE)
    except (OSError, ValueError):
        return DEFAULT_BIB_STYLE


def set_bib_style(style_id: str) -> None:
    path = _config_path()
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    cfg["bib_style"] = style_id
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except OSError:
        pass


def package_loaded(source: str, name: str) -> bool:
    r"""Is ``name`` loaded via ``\usepackage`` (or is it the document class)?"""
    pat = r"\\(?:usepackage|RequirePackage)(?:\[[^\]]*\])?\{[^}]*\b" + re.escape(name) + r"\b"
    cls = r"\\documentclass(?:\[[^\]]*\])?\{" + re.escape(name) + r"\}"
    return bool(re.search(pat, source) or re.search(cls, source))


def own_citations(source: str) -> bool:
    """The class / package (achemso, elsarticle, rsc.sty, or the achemso package) loads natbib
    and sets the reference look itself: never add or switch citation packages there."""
    from . import templates
    return templates.has_own_citations(source) or package_loaded(source, "achemso")


def ensure_bibliography(source: str, style_id: str | None = None):
    r"""Make citations resolvable in the *built* copy of a document: if it cites (``\cite*``) but
    has no ``\bibliography{...}``, add the reference list in the default format before the final
    ``\end{document}``; add natbib when ``\citep`` / ``\citet`` need it. Returns
    ``(source, notes)`` -- the user's own text is never changed, ``notes`` say what was added."""
    from . import zotero
    code = "\n".join(_COMMENT_RE.sub("", ln) for ln in source.splitlines())
    if not zotero.cite_keys(source):
        return source, []
    notes, preamble = [], []
    style = bib_style(style_id)
    has_list = re.search(r"\\bibliography\s*\{|\\begin\{thebibliography\}|\\printbibliography",
                         code)
    if re.search(r"\\bibliography\s*\{", code) and not _BST_RE.search(code):
        m = re.search(r"^[ \t]*\\bibliography\s*\{", source, re.M)    # marker without a style
        if m:
            source = (source[:m.start()] + f"\\bibliographystyle{{{style['bst']}}}\n"
                      + source[m.start():])
            notes.append(f"the reference list had no \\bibliographystyle: added "
                         f"'{style['bst']}' to the built copy")
            code = "\n".join(_COMMENT_RE.sub("", ln) for ln in source.splitlines())
    if not has_list:
        for line in style["packages"]:
            name = re.search(r"\{([^}]*)\}\s*$", line).group(1)
            if not package_loaded(code, name) and not own_citations(code):
                preamble.append(line)
        end = list(re.finditer(r"\\end\{document\}", source))
        if end:
            at = end[-1].start()
            source = (source[:at] + f"\\bibliographystyle{{{style['bst']}}}\n"
                      f"\\bibliography{{references}}\n" + source[at:])
            notes.append(f"no reference list in the text: added \\bibliography with the "
                         f"'{style['id']}' format ({style['bst']}.bst) to the built copy - "
                         "use References -> Insert reference list to make it permanent")
    if re.search(r"\\cite[pt]\*?\b|\\cite(?:author|year)\b", code) \
            and not package_loaded(code, "natbib") and not own_citations(code) \
            and not any("natbib" in p for p in preamble):
        preamble.append(r"\usepackage{natbib}")
        notes.append(r"\citep/\citet need natbib: added \usepackage{natbib} to the built copy")
    if preamble:
        m = re.search(r"\\begin\{document\}", source)
        if m:
            source = source[:m.start()] + "\n".join(preamble) + "\n" + source[m.start():]
    return source, notes


# Packages that belong to a citation format. They change what \cite and the reference list
# look like and several clash with each other (apalike + natbib: "\bibhang already defined"),
# so switching formats must remove the previous format's packages -- not just natbib.
MANAGED_PACKAGES = ("natbib", "achemso", "apalike", "apacite")
_BST_RE = re.compile(r"\\bibliographystyle\s*\{([^}]*)\}")
_USEPKG_RE = re.compile(
    r"^(?P<ind>[ \t]*)\\usepackage(?P<opt>\[[^\]]*\])?\{(?P<names>[^}]*)\}(?P<tail>[ \t]*(?:%.*)?)\n?",
    re.M)


def strip_packages(source: str, names) -> str:
    r"""``source`` without ``\usepackage`` entries for ``names`` (options and comma lists
    handled: ``\usepackage{amsmath,natbib}`` keeps amsmath). Commented lines are left."""
    names = set(names)

    def fix(m):
        pkgs = [p.strip() for p in m.group("names").split(",") if p.strip()]
        keep = [p for p in pkgs if p not in names]
        if len(keep) == len(pkgs):
            return m.group(0)
        if not keep:
            return ""
        opt = m.group("opt") or ""
        return f"{m.group('ind')}\\usepackage{opt}{{{','.join(keep)}}}{m.group('tail')}\n"
    return _USEPKG_RE.sub(fix, source)


def _insert_before_begin_document(source: str, lines) -> str:
    m = re.search(r"\\begin\{document\}", source)
    if not m or not lines:
        return source
    return source[:m.start()] + "\n".join(lines) + "\n" + source[m.start():]


def normalize_for_style(source: str, style_id: str | None = None):
    r"""The build copy's packages made consistent with the document's format: packages of
    *other* formats are dropped, the format's own are added when missing. A natbib the
    document loads itself is kept (it may carry the user's options). Returns
    ``(source, notes)``; the user's text is never modified, only the compiled copy."""
    st = bib_style(style_id)
    needed = {re.search(r"\{([^}]*)\}\s*$", p).group(1) for p in st["packages"]}
    if own_citations(source):
        return source, []                         # the class brings its own citations
    present = {n for n in MANAGED_PACKAGES if package_loaded(source, n)}
    drop = present - needed
    notes = []
    out = source
    if drop:
        out = strip_packages(out, drop)
        notes.append("packages of another citation format were left in the text "
                     f"({', '.join(sorted(drop))}) - removed from the built copy; "
                     "choose the format in References to clean the document itself")
    add = [p for p in st["packages"]
           if re.search(r"\{([^}]*)\}\s*$", p).group(1) not in present]
    if add:
        out = _insert_before_begin_document(out, add)
    return out, notes


def detect_bib_style(source: str, prefer: str = "") -> str:
    r"""Style id of the document's own ``\bibliographystyle{...}`` (``""`` if it has none).
    Several presets share ``achemso.bst``; ``prefer`` (the stored choice) breaks the tie, else the
    ``achemso`` package in the preamble does."""
    code = "\n".join(_COMMENT_RE.sub("", ln) for ln in source.splitlines())
    m = _BST_RE.search(code)
    if m is None:
        return ""
    hits = [s["id"] for s in BIB_STYLES if s["bst"] == m.group(1).strip()]
    if not hits:
        return ""
    if prefer in hits:
        return prefer
    if "acs-notitle" in hits and package_loaded(code, "achemso") \
            and not re.search(r"\\usepackage(?:\[[^\]]*\])?\{natbib\}", code):
        return "acs-notitle"
    return hits[0]


def apply_bib_style(source: str, style_id: str) -> str:
    r"""``source`` rewritten for ``style_id``: the natbib / achemso package lines are replaced by
    the format's own, and an existing ``\bibliographystyle{...}`` is switched. Nothing else
    is touched; a document without a reference list just gets the preamble lines."""
    st = bib_style(style_id)
    if own_citations(source):
        return _BST_RE.sub(lambda m: f"\\bibliographystyle{{{st['bst']}}}", source) \
            if not st.get("native") else source
    out = strip_packages(source, MANAGED_PACKAGES)
    out = _insert_before_begin_document(out, st["packages"])
    if st.get("native"):                   # NoorSuite writes the list: no BibTeX style line
        return re.sub(r"^[ \t]*\\bibliographystyle\s*\{[^}]*\}[ \t]*\n?", "", out,
                      flags=re.M)
    line = f"\\bibliographystyle{{{st['bst']}}}"
    if _BST_RE.search(out):
        return _BST_RE.sub(lambda m: line, out)
    m = re.search(r"^[ \t]*\\bibliography\s*\{", out, re.M)      # keep marker + add the style
    return out[:m.start()] + line + "\n" + out[m.start():] if m else out


NUMERIC_BST_ONLY = {"plain", "unsrt", "ieeetr", "abbrv", "alpha", "acs", "acs-notitle"}


def prepare_build_source(source: str, records, style_id: str | None = None,
                         cite_mode: str = "superscript"):
    r"""The text that is actually compiled, for the document's reference format:
    ``(build_source, notes, info)`` with ``info = {"cited": [keys in order], "undefined":
    [keys], "style": style}``. Native formats number the citations and write the list
    themselves (:mod:`NoorSuite.refstyle`); BibTeX formats get ``ensure_bibliography``."""
    from . import zotero
    st = bib_style(style_id)
    source, pk_notes = normalize_for_style(source, style_id)
    if st.get("native"):
        from . import refstyle
        src, cited, undefined, notes = refstyle.number_citations(
            source, records, cite_mode=cite_mode)
        return src, pk_notes + notes, {"cited": cited, "undefined": undefined, "style": st}
    conv_notes = []
    if st["id"] in NUMERIC_BST_ONLY and re.search(r"\\cite[pt]\*?\b", source):
        # numbered BibTeX formats cannot print author names, and natbib (which would define
        # \citep / \citet) clashes with several of their .bst files: plain \cite instead
        source = re.sub(r"\\cite[pt]\*?(?=\s*(?:\[|\{))", r"\\cite", source)
        conv_notes.append(r"\citep / \citet need an author-year format; this numbered format "
                          r"prints them as \cite in the built copy")
    src, notes = ensure_bibliography(source, style_id)
    notes = conv_notes + notes
    notes = pk_notes + notes
    known = {r["key"] for r in records}
    cited = zotero.cite_keys(source)
    return src, notes, {"cited": cited, "undefined": [k for k in cited if k not in known],
                        "style": st}


def article_template(title: str = "Title", style_id: str | None = None,
                     template_id: str | None = None) -> str:
    if template_id and template_id != "article":
        from . import templates
        return templates.build_document(template_id, title)
    text = ARTICLE_TEMPLATE.replace("@TITLE@", title)
    pk = "\n".join(bib_style(style_id)["packages"])
    return text.replace(r"\usepackage{noorsuite}   % \noorsheet{...}: NoorSuite sheets as figures",
                        r"\usepackage{noorsuite}   % \noorsheet{...}: NoorSuite sheets as figures"
                        + ("\n" + pk if pk else ""))


_REF_RE = re.compile(r"\\noorsheet\s*(?:\[[^\]]*\])?\s*\{([^{}]*)\}")
_COMMENT_RE = re.compile(r"(?<!\\)%.*")
_SAFE_KEY = re.compile(r"^[A-Za-z0-9._-]+$")


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")


def figure_refs(source: str) -> list:
    """Every distinct ``\\noorsheet`` key in ``source`` (comments ignored), in order."""
    seen, out = set(), []
    for line in source.splitlines():
        for m in _REF_RE.finditer(_COMMENT_RE.sub("", line)):
            key = m.group(1).strip()
            if key and key not in seen:
                seen.add(key)
                out.append(key)
    return out


_ASSET_RE = re.compile(r"\\noorfile\s*(?:\[[^\]]*\])?\s*\{([^{}]*)\}")


def asset_refs(source: str) -> list:
    """Every distinct ``\\noorfile`` key in ``source`` (comments ignored), in order."""
    seen, out = set(), []
    for line in source.splitlines():
        for m in _ASSET_RE.finditer(_COMMENT_RE.sub("", line)):
            key = m.group(1).strip()
            if key and key not in seen:
                seen.add(key)
                out.append(key)
    return out


def resolve_asset(key: str, assets):
    """The asset (``asset_id`` / ``name``) a ``\\noorfile`` key refers to: id, else name
    slug, else exact name; ``None`` if there is none."""
    assets = list(assets)
    for a in assets:
        if a.asset_id == key:
            return a
    for a in assets:
        if slug(a.name) == slug(key) and slug(key):
            return a
    return next((a for a in assets if a.name == key), None)


def asset_snippet(asset_id: str, name: str = "", caption: str = "", width: str = "") -> str:
    """A ``figure`` environment for an imported figure file."""
    opts = "[" + (width or "width=0.8" + chr(92) + "linewidth") + "]"
    note = f"%  {name}" if name else ""
    return (f"\\begin{{figure}}[htbp]\n  \\centering\n  \\noorfile{opts}{{{asset_id}}}{note}\n"
            f"  \\caption{{{caption or name or 'Caption.'}}}\n"
            f"  \\label{{fig:{slug(name) or asset_id}}}\n\\end{{figure}}\n")


def resolve_ref(key: str, sheets):
    """The sheet (an object with ``sheet_id`` / ``name``) a ``\\noorsheet`` key refers to:
    by id, else by name slug, else by exact name; ``None`` if there is none."""
    sheets = list(sheets)
    for sm in sheets:
        if sm.sheet_id == key:
            return sm
    for sm in sheets:
        if slug(sm.name) == slug(key) and slug(key):
            return sm
    return next((sm for sm in sheets if sm.name == key), None)


def figure_snippet(sheet_id: str, name: str = "", caption: str = "", label: str = "",
                   width: str = "", star: bool = False) -> str:
    """A ``figure`` environment for one sheet (what *Insert figure...* writes); ``star``
    -> ``figure*`` (spanning both columns of a two-column layout)."""
    opts = f"[{width}]" if width else ""
    label = label or f"fig:{slug(name) or sheet_id}"
    note = f"%  {name}" if name else ""
    env = "figure*" if star else "figure"
    return (f"\\begin{{{env}}}[htbp]\n  \\centering\n  \\noorsheet{opts}{{{sheet_id}}}{note}\n"
            f"  \\caption{{{caption or name or 'Caption.'}}}\n  \\label{{{label}}}\n"
            f"\\end{{{env}}}\n")


# --------------------------------------------------------------------------- engine
_ENGINE_NAMES = ("tectonic", "latexmk", "xelatex", "lualatex", "pdflatex")


def _config_path() -> Path:
    return Path(os.path.expanduser("~")) / ".noorsuite" / "config.json"


def _candidate_dirs() -> list:
    home = Path(os.path.expanduser("~"))
    dirs = []
    for root in ("anaconda3", "miniconda3", "miniforge3", "mambaforge", ".conda"):
        env = home / root / "envs" / "noorlatex"
        dirs += [env / "Library" / "bin", env / "Scripts", env / "bin"]
    dirs.append(home / ".noorsuite" / "latex")
    return dirs


def find_engine():
    """Path of a usable LaTeX engine, or ``None``. Order: ``$NOORSUITE_LATEX``, the
    ``latex_engine`` entry of ``~/.noorsuite/config.json``, ``tectonic`` / ``latexmk`` /
    ``xelatex`` / ``lualatex`` / ``pdflatex`` on PATH, then a ``noorlatex`` conda env."""
    explicit = os.environ.get("NOORSUITE_LATEX")
    try:
        explicit = explicit or json.loads(_config_path().read_text(encoding="utf-8")).get(
            "latex_engine")
    except (OSError, ValueError):
        pass
    if explicit and (shutil.which(explicit) or os.path.isfile(explicit)):
        return shutil.which(explicit) or explicit
    for name in _ENGINE_NAMES:
        found = shutil.which(name)
        if found:
            return found
    for d in _candidate_dirs():
        for name in _ENGINE_NAMES:
            for ext in ("", ".exe"):
                p = d / (name + ext)
                if p.is_file():
                    return str(p)
    return None


def engine_commands(engine: str, tex: str, outdir: str) -> list:
    """The command line(s) that turn ``tex`` into ``<outdir>/main.pdf`` with ``engine``."""
    base = Path(engine).stem.lower()
    if base == "tectonic":
        # --only-cached: no network round trips for the package bundle (build_pdf retries
        # without it when a package is not cached yet)
        return [[engine, "-X", "compile", "--only-cached", "--outdir", outdir, tex]]
    if base == "latexmk":
        return [[engine, "-pdf", "-interaction=nonstopmode", "-halt-on-error",
                 f"-outdir={outdir}", tex]]
    one = [engine, "-interaction=nonstopmode", "-halt-on-error",
           f"-output-directory={outdir}", tex]
    bibtex = shutil.which("bibtex")
    if bibtex:                   # latex, bibtex, latex, latex: citations + cross-references
        return [one, [bibtex, f"{outdir}/{Path(tex).stem}"], one, one]
    return [one, one]


# --------------------------------------------------------------------------- build
@dataclass
class BuildResult:
    ok: bool
    pdf: str = ""
    log: str = ""
    engine: str = ""
    seconds: float = 0.0
    missing: list = field(default_factory=list)      # \noorsheet keys with no sheet


def prepare_workdir(workdir, source: str) -> Path:
    """Write ``main.tex`` + ``noorsuite.sty`` (and make the figure folder) into ``workdir``."""
    wd = Path(workdir)
    (wd / FIGURES_DIR).mkdir(parents=True, exist_ok=True)
    (wd / "main.tex").write_text(source, encoding="utf-8")
    (wd / "noorsuite.sty").write_text(STY, encoding="utf-8")
    return wd


def build_pdf(workdir, engine=None, timeout=300) -> BuildResult:
    """Compile ``<workdir>/main.tex``. Figures must already be in ``noorsuite-figures/``."""
    engine = engine or find_engine()
    if not engine:
        return BuildResult(False, log=(
            "No LaTeX engine found. Install tectonic (conda install -c conda-forge tectonic), "
            "MiKTeX or TeX Live, or point $NOORSUITE_LATEX / ~/.noorsuite/config.json "
            '{"latex_engine": "<path>"} at one.'))
    wd = Path(workdir)
    out = wd / "out"
    out.mkdir(exist_ok=True)
    t0, log = time.time(), []
    flags = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
    commands = engine_commands(engine, "main.tex", "out")
    for cmd in commands:
        is_bibtex = Path(cmd[0]).stem.lower() == "bibtex"
        try:
            res = subprocess.run(cmd, cwd=wd, capture_output=True, text=True, timeout=timeout,
                                 encoding="utf-8", errors="replace", **flags)
            text = res.stdout + res.stderr
            # --only-cached never downloads: a package -- or a BibTeX style file such as
            # ieeetr.bst / plainnat.bst -- that is not cached yet makes the run fail or, worse,
            # "succeed" with every citation printed as "?". Retry online in both cases.
            if "--only-cached" in cmd and (res.returncode != 0 or "issued by BibTeX" in text):
                cmd = [c for c in cmd if c != "--only-cached"]
                res = subprocess.run(cmd, cwd=wd, capture_output=True, text=True,
                                     timeout=timeout, encoding="utf-8", errors="replace",
                                     **flags)
        except subprocess.TimeoutExpired:
            return BuildResult(False, log="\n".join(log) + "\nLaTeX run timed out.",
                               engine=engine, seconds=time.time() - t0)
        except OSError as exc:
            return BuildResult(False, log=f"Could not run {engine}: {exc}", engine=engine)
        log.append(f"$ {' '.join(cmd)}\n{res.stdout}{res.stderr}")
        if res.returncode != 0 and not is_bibtex:      # bibtex warns (exit 1) on unknown keys
            return BuildResult(False, log="\n".join(log), engine=engine,
                               seconds=time.time() - t0)
    pdf = out / "main.pdf"
    return BuildResult(pdf.is_file(), str(pdf) if pdf.is_file() else "", "\n".join(log),
                       engine, time.time() - t0)


def write_bundle(dest, source: str, figures_dir) -> Path:
    """Copy ``main.tex``, ``noorsuite.sty`` and the figure PDFs to ``dest`` -- a folder
    that compiles anywhere (journal submission, arXiv, Overleaf upload)."""
    dest = Path(dest)
    prepare_workdir(dest, source)
    for pdf in Path(figures_dir).glob("*.pdf"):
        shutil.copy2(pdf, dest / FIGURES_DIR / pdf.name)
    return dest


def default_workdir(doc_id: str) -> Path:
    return Path(os.path.expanduser("~")) / ".noorsuite" / "build" / doc_id
