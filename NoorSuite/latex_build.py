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
    assets = {a.asset_id: a for a in pm.assets}
    figures = Path(workdir) / latex.FIGURES_DIR
    figures.mkdir(parents=True, exist_ok=True)
    missing = []
    for key in latex.figure_refs(dm.source):
        sm = latex.resolve_ref(key, pm.sheets)
        if sm is None or not re.fullmatch(r"[A-Za-z0-9._-]+", key):
            missing.append(key)
            continue
        export_sheet_pdf(sm, repo, imgs, str(figures / f"{key}.pdf"), assets=assets)
    from . import assets as assets_mod
    for key in latex.asset_refs(dm.source):                # imported figure files
        a = latex.resolve_asset(key, pm.assets)
        if a is None or not re.fullmatch(r"[A-Za-z0-9._-]+", key) or \
                assets_mod.export_for_latex(a, figures, f"asset-{key}") is None:
            missing.append(key)
    return missing


def _office(pm, dm, a) -> int:
    """``latex docx|odt``: Word / LibreOffice export through pandoc."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    from . import assets as assets_mod
    from . import docexport
    from .sheet import export_sheet_raster
    global _APP
    _APP = QApplication.instance() or QApplication([])
    repo = {o.id: o for o in pm.data_objects}
    imgs = {i.id: i for i in pm.images}
    assets = {x.asset_id: x for x in pm.assets}

    def sheet_png(sm, path):
        export_sheet_raster(sm, repo, imgs, path, 300, assets=assets)

    def asset_png(asset, path):
        if not assets_mod.export_png(asset, path):
            raise RuntimeError("figure file unreadable")

    out = Path(a.out or (re.sub(r"[^\w.-]+", "_", dm.name) + "." + a.command))
    work = Path(tempfile.mkdtemp(prefix="noorsuite_office_"))
    try:
        from . import tables as _tables
        expanded, _missing = _tables.expand_tables(dm.source, pm.tables, repo)
        ok, log = docexport.export_office(
            expanded, pm.references, getattr(dm, "cite_mode", "superscript"), pm.sheets,
            pm.assets, out.resolve(), a.command, work, sheet_png, asset_png)
        if log:
            print(log, file=sys.stderr)
        if ok:
            print(f"exported {dm.name!r} -> {out}")
        return 0 if ok else 1
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv) -> int:
    ap = argparse.ArgumentParser(prog="noorsuite latex", description=__doc__)
    ap.add_argument("command", choices=["build", "export", "engine", "docx", "odt"])
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
    if a.command in ("docx", "odt"):
        return _office(pm, dm, a)
    workdir = Path(tempfile.mkdtemp(prefix="noorsuite_tex_"))
    try:
        style_id = latex.detect_bib_style(dm.source, getattr(dm, "bib_style", "")) \
            or getattr(dm, "bib_style", "") or None
        from . import tables as _tables
        repo = {o.id: o for o in pm.data_objects}
        expanded, tab_missing = _tables.expand_tables(dm.source, pm.tables, repo)
        for k in tab_missing:
            print(f"warning: no table matches {{{k}}}", file=sys.stderr)
        source, notes, info = latex.prepare_build_source(
            expanded, pm.references, style_id, getattr(dm, "cite_mode", "superscript"))
        for n in notes:
            print("note:", n, file=sys.stderr)
        for k in info["undefined"]:
            print(f"warning: citation key {k!r} is not in the project's references",
                  file=sys.stderr)
        latex.prepare_workdir(workdir, source)
        from . import zotero
        (workdir / "references.bib").write_text(
            zotero.bibtex_file(pm.references, used=info["cited"]), encoding="utf-8")
        missing = render_figures(pm, dm, workdir)
        for key in missing:
            print(f"warning: no figure matches {{{key}}}", file=sys.stderr)
        if a.command == "export":
            if not a.folder:
                raise SystemExit("export needs --folder")
            dest = latex.write_bundle(a.folder, expanded, workdir / latex.FIGURES_DIR)
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
