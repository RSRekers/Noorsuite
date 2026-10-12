"""Headless PDF build: ``python -m NoorSuite latex build [--project FILE] [--doc NAME] [--out FILE]``.

Renders every sheet a document references (offscreen Qt), compiles the document with the
detected LaTeX engine and writes the PDF -- no GUI window needed. Also ``latex engine``
(show which engine is used) and ``latex export --folder DIR`` (main.tex + noorsuite.sty +
figure PDFs, for journal submission / arXiv / Overleaf).
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

from . import latex


_APP = None


def _load(project):
    from .model import ProjectModel
    from .offline import SESSION_FILE
    path = project or SESSION_FILE
    if not os.path.exists(path):
        raise SystemExit(f"project file not found: {path}")
    return ProjectModel.load(path)


def _pick_document(pm, key):
    if not pm.documents:
        raise SystemExit("the project has no LaTeX documents (create one in the GUI or with "
                         "suite.push_document(...))")
    if key is None:
        return pm.documents[0]
    for d in pm.documents:
        if key in (d.doc_id, d.name):
            return d
    raise SystemExit(f"no document {key!r}; available: {[d.name for d in pm.documents]}")


def render_figures(pm, dm, workdir) -> list:
    """Export every referenced sheet into ``<workdir>/noorsuite-figures``; returns the keys
    that could not be resolved."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    from .sheet import export_sheet_pdf
    global _APP
    _APP = QApplication.instance() or QApplication([])   # keep it alive
    repo = {o.id: o for o in pm.data_objects}
    imgs = {i.id: i for i in pm.images}
    figures = Path(workdir) / latex.FIGURES_DIR
    figures.mkdir(parents=True, exist_ok=True)
    missing = []
    for key in latex.figure_refs(dm.source):
        sm = latex.resolve_ref(key, pm.sheets)
        if sm is None or not re.fullmatch(r"[A-Za-z0-9._-]+", key):
            missing.append(key)
            continue
        export_sheet_pdf(sm, repo, imgs, str(figures / f"{key}.pdf"))
    return missing


def main(argv) -> int:
    ap = argparse.ArgumentParser(prog="noorsuite latex", description=__doc__)
    ap.add_argument("command", choices=["build", "export", "engine"])
    ap.add_argument("--project", help=".sciproj file (default: the GUI session file)")
    ap.add_argument("--doc", help="document name or id (default: the first one)")
    ap.add_argument("--out", help="build: output PDF path (default: <document name>.pdf)")
    ap.add_argument("--folder", help="export: destination folder")
    a = ap.parse_args(argv)

    if a.command == "engine":
        engine = latex.find_engine()
        print(engine or "no LaTeX engine found (install tectonic / MiKTeX / TeX Live)")
        return 0 if engine else 1

    pm = _load(a.project)
    dm = _pick_document(pm, a.doc)
    workdir = Path(tempfile.mkdtemp(prefix="noorsuite_tex_"))
    try:
        latex.prepare_workdir(workdir, dm.source)
        from . import zotero
        (workdir / "references.bib").write_text(
            zotero.bibtex_file(pm.references, used=zotero.cite_keys(dm.source)),
            encoding="utf-8")
        missing = render_figures(pm, dm, workdir)
        for key in missing:
            print(f"warning: no sheet matches \noorsheet{{{key}}}", file=sys.stderr)
        if a.command == "export":
            if not a.folder:
                raise SystemExit("export needs --folder")
            dest = latex.write_bundle(a.folder, dm.source, workdir / latex.FIGURES_DIR)
            shutil.copy2(workdir / "references.bib", dest / "references.bib")
            print(f"exported {dm.name!r} -> {dest}")
            return 0
        res = latex.build_pdf(workdir)
        if not res.ok:
            print(res.log, file=sys.stderr)
            return 1
        default_name = re.sub(r"[^\w.-]+", "_", dm.name) + ".pdf"
        out = Path(a.out or default_name)
        shutil.copyfile(res.pdf, out)
        print(f"built {dm.name!r} -> {out}  ({res.seconds:.1f} s, {Path(res.engine).stem})")
        return 0
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
