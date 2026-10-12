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

def article_template(title: str = "Title") -> str:
    return ARTICLE_TEMPLATE.replace("@TITLE@", title)


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
                   width: str = "") -> str:
    """A ``figure`` environment for one sheet (what *Insert figure...* writes)."""
    opts = f"[{width}]" if width else ""
    label = label or f"fig:{slug(name) or sheet_id}"
    note = f"%  {name}" if name else ""
    return (f"\\begin{{figure}}[htbp]\n  \\centering\n  \\noorsheet{opts}{{{sheet_id}}}{note}\n"
            f"  \\caption{{{caption or name or 'Caption.'}}}\n  \\label{{{label}}}\n"
            f"\\end{{figure}}\n")


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
        return [[engine, "-X", "compile", "--outdir", outdir, tex]]
    if base == "latexmk":
        return [[engine, "-pdf", "-interaction=nonstopmode", "-halt-on-error",
                 f"-outdir={outdir}", tex]]
    one = [engine, "-interaction=nonstopmode", "-halt-on-error",
           f"-output-directory={outdir}", tex]
    return [one, one]            # twice: cross-references / table of contents


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
    for cmd in engine_commands(engine, "main.tex", "out"):
        try:
            res = subprocess.run(cmd, cwd=wd, capture_output=True, text=True, timeout=timeout,
                                 encoding="utf-8", errors="replace", **flags)
        except subprocess.TimeoutExpired:
            return BuildResult(False, log="\n".join(log) + "\nLaTeX run timed out.",
                               engine=engine, seconds=time.time() - t0)
        except OSError as exc:
            return BuildResult(False, log=f"Could not run {engine}: {exc}", engine=engine)
        log.append(f"$ {' '.join(cmd)}\n{res.stdout}{res.stderr}")
        if res.returncode != 0:
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
