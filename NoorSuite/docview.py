"""The Document tab: a LaTeX source editor next to a live PDF preview.

* ``\\noorsheet{<sheet>}`` figures are exported from the project's sheets as vector PDFs
  into the build folder before every compile (see :mod:`NoorSuite.latex`).
* Double-click a figure in the preview -- or a ``\\noorsheet`` line in the source -- to jump to
  that sheet (``sheetRequested``); edits to a referenced sheet recompile the document.
* Export the PDF, or a self-contained folder (main.tex + noorsuite.sty + figures).

The tab talks to the main window through a small *host* protocol (``document_sheets()``,
``export_sheet_pdf(sheet_id, path)``, ``sheet_fingerprint(sheet_id)``).
"""
from __future__ import annotations

import re
import shutil
import threading
from pathlib import Path

from PyQt6.QtCore import QModelIndex, QRectF, QSize, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import (QAction, QColor, QFont, QFontDatabase, QKeySequence, QPixmap,
                         QSyntaxHighlighter, QTextCharFormat)
from PyQt6.QtPdf import QPdfDocument, QPdfLinkModel
from PyQt6.QtWidgets import (QApplication, QCheckBox, QFileDialog, QFrame, QInputDialog,
                             QLabel, QMessageBox, QPlainTextEdit, QScrollArea, QSplitter,
                             QToolBar, QVBoxLayout, QWidget)

from . import latex

_NOOR_LINE_RE = re.compile(r"\\noorsheet\s*(?:\[[^\]]*\])?\s*\{([^{}]*)\}")


# ----------------------------------------------------------------------- source editor
class _LatexHighlighter(QSyntaxHighlighter):
    def __init__(self, doc):
        super().__init__(doc)

        def fmt(color, bold=False, italic=False):
            f = QTextCharFormat()
            f.setForeground(QColor(color))
            if bold:
                f.setFontWeight(QFont.Weight.Bold)
            f.setFontItalic(italic)
            return f

        self._rules = [
            (re.compile(r"\$[^$\n]*\$"), fmt("#ce9178")),                  # inline math
            (re.compile(r"\\[A-Za-z@]+\*?"), fmt("#569cd6")),              # commands
            (re.compile(r"\\noorsheet\b"), fmt("#c586c0", bold=True)),     # figures
            (re.compile(r"(?<!\\)%.*"), fmt("#6a9955", italic=True)),      # comments
        ]

    def highlightBlock(self, text):
        for rx, f in self._rules:
            for m in rx.finditer(text):
                self.setFormat(m.start(), m.end() - m.start(), f)


class LatexEditor(QPlainTextEdit):
    """Plain-text editor; double-clicking inside a ``\\noorsheet{...}`` emits
    ``sheetRequested(key)``."""

    sheetRequested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        font.setPointSize(10)
        self.setFont(font)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)
        self._hl = _LatexHighlighter(self.document())

    def mouseDoubleClickEvent(self, event):
        cursor = self.cursorForPosition(event.position().toPoint())
        line, col = cursor.block().text(), cursor.positionInBlock()
        for m in _NOOR_LINE_RE.finditer(line):
            if m.start() <= col <= m.end():
                self.sheetRequested.emit(m.group(1).strip())
                return
        super().mouseDoubleClickEvent(event)


# ------------------------------------------------------------------------ PDF preview
class _PageLabel(QLabel):
    def __init__(self, preview, index):
        super().__init__()
        self._preview, self._index = preview, index
        self.setMouseTracking(True)

    def mouseMoveEvent(self, event):
        hit = self._preview.link_at(self._index, event.position())
        self.setCursor(Qt.CursorShape.PointingHandCursor if hit
                       else Qt.CursorShape.ArrowCursor)
        self.setToolTip("Double-click to edit this figure in NoorSuite" if hit else "")

    def mouseDoubleClickEvent(self, event):
        key = self._preview.link_at(self._index, event.position())
        if key:
            self._preview.sheetRequested.emit(key)


class PdfPreview(QScrollArea):
    """Scrollable stack of rendered PDF pages; ``noorsuite://sheet/<key>`` links are hit-tested
    on double-click (``sheetRequested``). Ctrl+wheel zooms, Ctrl+0 fits the width."""

    sheetRequested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self._doc = QPdfDocument(self)
        self._container = QWidget()
        self._layout = QVBoxLayout(self._container)
        self._layout.setContentsMargins(8, 8, 8, 8)
        self._layout.setSpacing(10)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.setWidget(self._container)
        self._links: list = []            # per page: [(QRectF in points, key)]
        self._zoom = 1.0                  # manual zoom (1.0 = 96 dpi)
        self._fit = True
        self._path = ""
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._render)
        self._placeholder("No PDF yet - press Compile (F5).")

    def _placeholder(self, text):
        while self._layout.count():
            w = self._layout.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        lab = QLabel(text)
        lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._layout.addWidget(lab)

    def load(self, path: str):
        """(Re)load ``path``; the scroll position is kept across recompiles."""
        self._path = path
        pos = (self.horizontalScrollBar().value(), self.verticalScrollBar().value())
        self._doc.close()
        self._doc.load(path)
        if self._doc.status() != QPdfDocument.Status.Ready:
            self._placeholder("Could not open the PDF.")
            return
        self._links = []
        model = QPdfLinkModel(self)
        model.setDocument(self._doc)
        for p in range(self._doc.pageCount()):
            model.setPage(p)
            page = []
            for i in range(model.rowCount(QModelIndex())):
                idx = model.index(i, 0)
                url = idx.data(QPdfLinkModel.Role.Url.value)
                rect = idx.data(QPdfLinkModel.Role.Rectangle.value)
                if isinstance(url, QUrl) and url.toString().startswith(latex.LINK_SCHEME) \
                        and isinstance(rect, QRectF):
                    page.append((rect, url.toString()[len(latex.LINK_SCHEME):]))
            self._links.append(page)
        self._render()
        QTimer.singleShot(0, lambda: (self.horizontalScrollBar().setValue(pos[0]),
                                      self.verticalScrollBar().setValue(pos[1])))

    # -- geometry ---------------------------------------------------------------
    def _px_per_pt(self) -> float:
        if self._fit and self._doc.pageCount():
            width_pt = max(self._doc.pagePointSize(p).width()
                           for p in range(self._doc.pageCount()))
            avail = max(100, self.viewport().width() - 30)
            return avail / width_pt
        return self._zoom * 96.0 / 72.0

    def _render(self):
        if self._doc.status() != QPdfDocument.Status.Ready:
            return
        while self._layout.count():
            w = self._layout.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        scale = self._px_per_pt()
        dpr = self.devicePixelRatioF()
        for p in range(self._doc.pageCount()):
            pts = self._doc.pagePointSize(p)
            size = QSize(max(1, round(pts.width() * scale * dpr)),
                         max(1, round(pts.height() * scale * dpr)))
            image = self._doc.render(p, size)
            image.setDevicePixelRatio(dpr)
            lab = _PageLabel(self, p)
            lab.setPixmap(QPixmap.fromImage(image))
            lab.setFrameShape(QFrame.Shape.Box)
            lab.setFixedSize(QSize(round(pts.width() * scale), round(pts.height() * scale)))
            self._layout.addWidget(lab, 0, Qt.AlignmentFlag.AlignHCenter)

    def link_at(self, page: int, pos) -> str:
        if page >= len(self._links):
            return ""
        scale = self._px_per_pt()
        x, y = pos.x() / scale, pos.y() / scale
        for rect, key in self._links[page]:
            if rect.contains(x, y):
                return key
        return ""

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fit and self._doc.status() == QPdfDocument.Status.Ready:
            self._timer.start(120)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            base = self._px_per_pt() * 72.0 / 96.0
            self._zoom = min(max(base * 1.15 ** (event.angleDelta().y() / 120.0), 0.3), 5.0)
            self._fit = False
            self._timer.start(60)
            event.accept()
            return
        super().wheelEvent(event)

    def keyPressEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier and \
                event.key() == Qt.Key.Key_0:
            self._fit = True
            self._timer.start(0)
            return
        super().keyPressEvent(event)


# --------------------------------------------------------------------- document tab
class DocumentTab(QWidget):
    """Editor + preview + build for one :class:`~NoorSuite.model.DocumentModel`."""

    sheetRequested = pyqtSignal(str)      # a sheet id (resolved) to open
    contentChanged = pyqtSignal()
    _built = pyqtSignal(object)           # BuildResult, from the worker thread

    AUTO_DELAY_MS = 1500

    def __init__(self, model, host, parent=None):
        super().__init__(parent)
        self.model = model
        self.host = host
        self._building = False
        self._again = False
        self._last_fp = None
        self._pdf = ""
        self.workdir = latex.default_workdir(model.doc_id)

        toolbar = QToolBar()
        toolbar.setMovable(False)
        self.compile_act = QAction("Compile", self)
        self.compile_act.setShortcut(QKeySequence("F5"))
        self.compile_act.setToolTip("Export the referenced sheets and build the PDF (F5)")
        self.compile_act.triggered.connect(lambda: self.compile(force=True))
        toolbar.addAction(self.compile_act)
        self.auto_check = QCheckBox("Auto-compile")
        self.auto_check.setChecked(True)
        self.auto_check.setToolTip("Rebuild shortly after the text or a referenced sheet changes")
        toolbar.addWidget(self.auto_check)
        toolbar.addSeparator()
        insert_act = QAction("Insert figure...", self)
        insert_act.setToolTip("Insert a \\noorsheet figure for one of the project's sheets")
        insert_act.triggered.connect(self.insert_figure)
        toolbar.addAction(insert_act)
        toolbar.addSeparator()
        pdf_act = QAction("Export PDF...", self)
        pdf_act.triggered.connect(self.export_pdf)
        toolbar.addAction(pdf_act)
        bundle_act = QAction("Export LaTeX folder...", self)
        bundle_act.setToolTip("main.tex + noorsuite.sty + figure PDFs: compiles anywhere "
                              "(journal submission, arXiv, Overleaf)")
        bundle_act.triggered.connect(self.export_bundle)
        toolbar.addAction(bundle_act)
        self.status_label = QLabel("")
        toolbar.addSeparator()
        toolbar.addWidget(self.status_label)

        self.editor = LatexEditor()
        self.editor.setPlainText(model.source)
        self.editor.textChanged.connect(self._on_text_changed)
        self.editor.sheetRequested.connect(self._open_ref)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("LaTeX output appears here.")
        self.log.setMaximumBlockCount(5000)
        left = QSplitter(Qt.Orientation.Vertical)
        left.addWidget(self.editor)
        left.addWidget(self.log)
        left.setStretchFactor(0, 4)
        left.setStretchFactor(1, 1)

        self.preview = PdfPreview()
        self.preview.sheetRequested.connect(self._open_ref)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(left)
        split.addWidget(self.preview)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(toolbar)
        lay.addWidget(split, 1)

        self._source_timer = QTimer(self)
        self._source_timer.setSingleShot(True)
        self._source_timer.timeout.connect(lambda: self.compile())
        self._built.connect(self._on_built)

        engine = latex.find_engine()
        self.status_label.setText(
            f"  engine: {Path(engine).stem}" if engine else "  no LaTeX engine found")
        if engine is None:
            self.log.setPlainText(latex.build_pdf(self.workdir, None).log)
        self._load_cached_pdf()

    # -- identity -----------------------------------------------------------------
    @property
    def doc_id(self) -> str:
        return self.model.doc_id

    def source(self) -> str:
        return self.editor.toPlainText()

    def set_source(self, text: str):
        if text != self.editor.toPlainText():
            self.editor.setPlainText(text)

    def _on_text_changed(self):
        self.model.source = self.editor.toPlainText()
        self.contentChanged.emit()
        if self.auto_check.isChecked():
            self._source_timer.start(self.AUTO_DELAY_MS)

    # -- sheets -------------------------------------------------------------------
    def referenced_sheets(self) -> dict:
        """{key: sheet model or None} for every ``\\noorsheet`` key in the text."""
        sheets = self.host.document_sheets()
        return {k: latex.resolve_ref(k, sheets) for k in latex.figure_refs(self.source())}

    def _open_ref(self, key: str):
        sm = latex.resolve_ref(key, self.host.document_sheets())
        if sm is None:
            QMessageBox.information(self, "Unknown sheet",
                                    f"There is no sheet matching \"{key}\" in this project.")
            return
        self.sheetRequested.emit(sm.sheet_id)

    def sheets_changed(self):
        """Called by the window after any sheet was re-rendered/edited."""
        if not self.auto_check.isChecked():
            return
        fp = self._fingerprint()
        if fp != self._last_fp and any(self.referenced_sheets().values()):
            self._source_timer.start(self.AUTO_DELAY_MS)

    def _fingerprint(self):
        parts = [self.source()]
        for key, sm in sorted(self.referenced_sheets().items()):
            parts.append(f"{key}:{self.host.sheet_fingerprint(sm.sheet_id) if sm else '-'}")
        return hash("\n".join(parts))

    def insert_figure(self):
        sheets = self.host.document_sheets()
        if not sheets:
            QMessageBox.information(self, "Insert figure", "The project has no sheets yet.")
            return
        names = [f"{sm.name}   [{sm.sheet_id}]" for sm in sheets]
        item, ok = QInputDialog.getItem(self, "Insert figure", "Sheet:", names, 0, False)
        if not ok:
            return
        sm = sheets[names.index(item)]
        caption, ok = QInputDialog.getText(self, "Insert figure", "Caption:", text=sm.name)
        if not ok:
            return
        self.editor.textCursor().insertText(
            latex.figure_snippet(sm.sheet_id, sm.name, caption.strip()))

    # -- build --------------------------------------------------------------------
    def _load_cached_pdf(self):
        pdf = self.workdir / "out" / "main.pdf"
        if pdf.is_file():
            self._pdf = str(pdf)
            self.preview.load(self._pdf)

    def compile(self, force=False):
        if self._building:
            self._again = True
            return
        fp = self._fingerprint()
        if not force and fp == self._last_fp:
            return
        self._last_fp = fp
        engine = latex.find_engine()
        if engine is None:
            self.log.setPlainText(latex.build_pdf(self.workdir, None).log)
            self.status_label.setText("  no LaTeX engine found")
            return
        # export the referenced sheets on the GUI thread (they are Qt widgets) ...
        figures = self.workdir / latex.FIGURES_DIR
        latex.prepare_workdir(self.workdir, self.source())
        missing = []
        for key, sm in self.referenced_sheets().items():
            if sm is None or not re.fullmatch(r"[A-Za-z0-9._-]+", key):
                missing.append(key)
                continue
            try:
                self.host.export_sheet_pdf(sm.sheet_id, str(figures / f"{key}.pdf"))
            except Exception as exc:                       # keep building the rest
                missing.append(key)
                self.log.appendPlainText(f"[figure {key}] export failed: {exc}")
        # ... then run the LaTeX engine off-thread
        self._building = True
        self.status_label.setText(f"  building ({Path(engine).stem})...")
        self.compile_act.setEnabled(False)

        def work():
            res = latex.build_pdf(self.workdir, engine)
            res.missing = missing
            self._built.emit(res)

        threading.Thread(target=work, daemon=True).start()

    def _on_built(self, res):
        self._building = False
        self.compile_act.setEnabled(True)
        self.log.setPlainText(res.log)
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())
        note = f", {len(res.missing)} missing sheet(s): {', '.join(res.missing)}" \
            if res.missing else ""
        if res.ok:
            self._pdf = res.pdf
            self.preview.load(res.pdf)
            self.status_label.setText(f"  built in {res.seconds:.1f} s{note}")
        else:
            self.status_label.setText("  build FAILED - see the log" + note)
        if self._again:
            self._again = False
            QTimer.singleShot(50, self.compile)

    # -- export -------------------------------------------------------------------
    def _ensure_built(self) -> bool:
        if self._pdf and Path(self._pdf).is_file() and self._fingerprint() == self._last_fp:
            return True
        QMessageBox.information(self, "Export",
                                "The PDF is not up to date yet - press Compile (F5), wait "
                                "for \"built\", then export.")
        return False

    def export_pdf(self):
        if not self._ensure_built():
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export PDF", f"{self.model.name}.pdf",
                                              "PDF (*.pdf)")
        if path:
            shutil.copyfile(self._pdf, path)
            self.status_label.setText(f"  exported {Path(path).name}")

    def export_bundle(self):
        self.compile(force=True) if not self._building else None
        folder = QFileDialog.getExistingDirectory(self, "Export LaTeX folder")
        if not folder:
            return
        dest = Path(folder) / (re.sub(r"[^\w.-]+", "_", self.model.name) or "document")
        latex.write_bundle(dest, self.source(), self.workdir / latex.FIGURES_DIR)
        self.status_label.setText(f"  exported folder {dest.name}")
