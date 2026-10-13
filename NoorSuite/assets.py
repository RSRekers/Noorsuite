"""Imported figure files (SVG / PDF / PNG / JPG) -- "assets" of a project.

An asset is stored *inside* the project (``ProjectModel.assets``, base64 in the JSON), so a
document or a plot picture never breaks because a file moved. Editing happens in an external
editor (Inkscape for SVG): the bytes are written to a working file under
``~/.noorsuite/assets/``, the editor opens it, and the window watches that file and reads it
back when it is saved.

Pure helpers here (file names, finding Inkscape, SVG -> PDF / PNG); the window owns the
watching. Rasterising needs ``PyQt6.QtGui`` (always there) and uses ``QtSvg`` when Inkscape
is not installed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ASSET_EXTS = ("svg", "pdf", "png", "jpg", "jpeg")
_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def home_dir() -> Path:
    d = Path(os.path.expanduser("~")) / ".noorsuite"
    d.mkdir(parents=True, exist_ok=True)
    return d


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", str(text)).strip("-").lower() or "figure"


def work_dir() -> Path:
    d = home_dir() / "assets"
    d.mkdir(parents=True, exist_ok=True)
    return d


def work_path(asset) -> Path:
    """The editable working copy of an asset (``<slug>-<id>.<ext>``)."""
    return work_dir() / f"{slug(asset.name)}-{asset.asset_id}.{asset.ext}"


def materialize(asset) -> Path:
    """Write the asset's bytes to its working file unless it already holds exactly them."""
    p = work_path(asset)
    try:
        if p.is_file() and p.read_bytes() == asset.data:
            return p
    except OSError:
        pass
    p.write_bytes(asset.data)
    return p


def fingerprint(asset) -> str:
    return f"{asset.asset_id}:{asset.version}:{hashlib.md5(asset.data).hexdigest()[:12]}"


# ------------------------------------------------------------------ Inkscape
def _config_inkscape() -> str:
    try:
        cfg = json.loads((home_dir() / "config.json").read_text(encoding="utf-8"))
        return str(cfg.get("inkscape", "") or "")
    except (OSError, ValueError):
        return ""


def find_inkscape() -> str | None:
    """``$NOORSUITE_INKSCAPE``, ``config.json`` ``{"inkscape": path}``, PATH, then the
    usual install folders."""
    for cand in (os.environ.get("NOORSUITE_INKSCAPE", ""), _config_inkscape()):
        if cand and Path(cand).is_file():
            return cand
    exe = shutil.which("inkscape")
    if exe:
        return exe
    probes = []
    for base in (os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", ""),
                 os.environ.get("LOCALAPPDATA", "") + r"\Programs"):
        if base:
            probes += [Path(base) / "Inkscape" / "bin" / "inkscape.exe",
                       Path(base) / "Inkscape" / "inkscape.exe"]
    probes += [Path("/usr/bin/inkscape"), Path("/usr/local/bin/inkscape"),
               Path("/Applications/Inkscape.app/Contents/MacOS/inkscape")]
    return next((str(p) for p in probes if p.is_file()), None)


def open_in_editor(path, editor: str | None = None) -> bool:
    """Open ``path`` in Inkscape (SVG) or the system's default program; True if started."""
    path = str(path)
    ink = editor or (find_inkscape() if path.lower().endswith(".svg") else None)
    try:
        if ink:
            subprocess.Popen([ink, path], creationflags=_CREATE_NO_WINDOW)
            return True
        if sys.platform == "win32":
            os.startfile(path)                                  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        return True
    except OSError:
        return False


def _run_inkscape(args, timeout=120) -> bool:
    ink = find_inkscape()
    if not ink:
        return False
    try:
        res = subprocess.run([ink, *args], capture_output=True, timeout=timeout,
                             creationflags=_CREATE_NO_WINDOW)
        return res.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# ------------------------------------------------------------------ conversions
def export_for_latex(asset, out_dir, stem=None) -> Path | None:
    """Put the asset where LaTeX can include it: ``<out_dir>/<stem>.<ext>`` (``stem``
    defaults to ``asset-<id>``; SVG becomes a vector PDF -- Inkscape if installed, else
    QtSvg). Returns the file, or ``None``."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = out_dir / (stem or f"asset-{asset.asset_id}")
    ext = "jpg" if asset.ext == "jpeg" else asset.ext
    if ext != "svg":
        target = Path(str(base) + "." + ext)
        if not target.is_file() or target.read_bytes() != asset.data:
            target.write_bytes(asset.data)
        return target
    target = Path(str(base) + ".pdf")
    src = materialize(asset)
    if _run_inkscape([str(src), "--export-type=pdf", f"--export-filename={target}",
                      "--export-area-page"]) and target.is_file():
        return target
    return target if _svg_to_pdf_qt(asset.data, target) else None


def _svg_to_pdf_qt(data: bytes, target: Path) -> bool:
    try:
        from PyQt6.QtCore import QMarginsF, QSizeF
        from PyQt6.QtGui import QPageLayout, QPageSize, QPainter, QPdfWriter
        from PyQt6.QtSvg import QSvgRenderer
    except ImportError:
        return False
    r = QSvgRenderer(data)
    if not r.isValid():
        return False
    size = r.defaultSize()
    w_pt, h_pt = max(1.0, size.width() * 0.75), max(1.0, size.height() * 0.75)   # px -> pt
    w = QPdfWriter(str(target))
    w.setResolution(72)
    w.setPageSize(QPageSize(QSizeF(w_pt, h_pt), QPageSize.Unit.Point))
    w.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Point)
    p = QPainter(w)
    r.render(p)
    p.end()
    return target.is_file()


def raster(asset, max_px=1400):
    """The asset as an RGBA ``uint8`` array ``(h, w, 4)``, longest side <= ``max_px``;
    cached on disk by fingerprint. ``None`` if it cannot be read."""
    import numpy as np
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QImage

    cache = home_dir() / "asset-cache"
    cache.mkdir(parents=True, exist_ok=True)
    fp = hashlib.md5(f"{fingerprint(asset)}:{max_px}".encode()).hexdigest()[:20]
    png = cache / f"{asset.asset_id}-{fp}.png"
    if not png.is_file():
        for old in cache.glob(f"{asset.asset_id}-*.png"):
            try:
                old.unlink()
            except OSError:
                pass
        ok = False
        if asset.ext == "svg":
            src = materialize(asset)
            ok = _run_inkscape([str(src), "--export-type=png", f"--export-filename={png}",
                                f"--export-width={int(max_px)}", "--export-area-page",
                                "--export-background-opacity=0"])
            if not (ok and png.is_file()):
                ok = _svg_to_png_qt(asset.data, png, max_px)
        elif asset.ext == "pdf":
            ok = _pdf_to_png_qt(materialize(asset), png, max_px)
        else:
            img = QImage.fromData(asset.data)
            if not img.isNull():
                if max(img.width(), img.height()) > max_px:
                    img = img.scaled(max_px, max_px, Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation)
                ok = img.save(str(png), "PNG")
        if not ok:
            return None
    img = QImage(str(png))
    if img.isNull():
        return None
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    ptr = img.constBits()
    ptr.setsize(img.sizeInBytes())
    arr = np.frombuffer(ptr, np.uint8).reshape(img.height(), img.bytesPerLine() // 4, 4)
    return arr[:, :img.width(), :].copy()


def _svg_to_png_qt(data: bytes, png: Path, max_px: int) -> bool:
    try:
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QImage, QPainter
        from PyQt6.QtSvg import QSvgRenderer
    except ImportError:
        return False
    r = QSvgRenderer(data)
    if not r.isValid():
        return False
    s = r.defaultSize()
    scale = max_px / max(1, max(s.width(), s.height()))
    img = QImage(max(1, round(s.width() * scale)), max(1, round(s.height() * scale)),
                 QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    r.render(p)
    p.end()
    return img.save(str(png), "PNG")


def _pdf_to_png_qt(pdf: Path, png: Path, max_px: int) -> bool:
    try:
        from PyQt6.QtCore import QSize
        from PyQt6.QtPdf import QPdfDocument
    except ImportError:
        return False
    doc = QPdfDocument()
    doc.load(str(pdf))
    if doc.status() != QPdfDocument.Status.Ready or doc.pageCount() < 1:
        return False
    pts = doc.pagePointSize(0)
    scale = max_px / max(1.0, max(pts.width(), pts.height()))
    img = doc.render(0, QSize(max(1, round(pts.width() * scale)),
                              max(1, round(pts.height() * scale))))
    return (not img.isNull()) and img.save(str(png), "PNG")


def export_png(asset, path, max_px=2400) -> bool:
    """Write the asset as a PNG (for Word / LibreOffice export); False if unreadable."""
    import numpy as np
    from PyQt6.QtGui import QImage
    arr = raster(asset, max_px)
    if arr is None:
        return False
    arr = np.ascontiguousarray(arr)
    img = QImage(arr.data, arr.shape[1], arr.shape[0], arr.shape[1] * 4,
                 QImage.Format.Format_RGBA8888)
    return img.save(str(path), "PNG")


def guess_ext(path) -> str:
    ext = Path(path).suffix.lower().lstrip(".")
    return "jpg" if ext == "jpeg" else ext
