"""The Document tab: a LaTeX source editor next to a live PDF preview, with a side panel for
figures (thumbnails, search, drag and drop) and references (Zotero).

* ``\\noorsheet{<sheet>}`` figures are exported from the project's sheets as vector PDFs
  into the build folder before a compile -- only the sheets that changed (see
  :meth:`DocumentTab.compile`).
* Double-click a figure in the preview -- or a ``\\noorsheet`` line in the source -- to jump to
  that sheet (``sheetRequested``); edits to a referenced sheet recompile the document.
* Drag a sheet (or a whole folder) from the project tree, or a thumbnail from the Figures
  list, into the source to insert its ``figure`` environment.
* Cite from Zotero: the picked items are stored in the project and written to
  ``references.bib`` at build time (no Better BibTeX export needed, see :mod:`NoorSuite.zotero`).

The tab talks to the main window through a small *host* protocol (``document_sheets()``,
``document_references()``, ``add_references(records)``, ``remove_references(keys)``,
``export_sheet_pdf(sheet_id, path)``, ``sheet_fingerprint(sheet_id)``,
``sheet_thumbnail(sheet_id)``).
"""
from __future__ import annotations

import json
import re
import shutil
import threading
import time
from pathlib import Path

from PyQt6.QtCore import (QModelIndex, QMimeData, QRectF, QSettings, QSize, Qt, QTimer, QUrl,
                          pyqtSignal)
from PyQt6.QtGui import (QAction, QColor, QDrag, QFont, QFontDatabase, QIcon, QKeySequence,
                         QPixmap, QSyntaxHighlighter, QTextCharFormat)
from PyQt6.QtPdf import QPdfDocument, QPdfLinkModel
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox,
                             QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
                             QListView, QListWidget, QListWidgetItem, QMessageBox,
                             QPlainTextEdit, QPushButton, QScrollArea, QSplitter, QTabWidget,
                             QToolBar, QVBoxLayout, QWidget)

from . import latex, zotero
from .fuzzy import smart_score

_NOOR_LINE_RE = re.compile(r"\\noorsheet\s*(?:\[[^\]]*\])?\s*\{([^{}]*)\}")

# Explicit high-contrast colours (the app theme must not decide readability): a white page
# with black text by default, or light text on a dark ground.
THEMES = {
    "light": {"bg": "#ffffff", "fg": "#000000", "sel": "#cfe3ff", "panel": "#f5f5f5",
              "border": "#b8b8b8", "page_bg": "#c8c8c8",
              "cmd": "#0b4fb3", "noor": "#8a1fc2", "comment": "#2a7a2a", "math": "#a84300"},
    "dark": {"bg": "#1b1b1b", "fg": "#f2f2f2", "sel": "#34507a", "panel": "#262626",
             "border": "#555555", "page_bg": "#101010",
             "cmd": "#7db7ff", "noor": "#e0a3ff", "comment": "#86d086", "math": "#ffb37a"},
}


def _stylesheet(t: dict) -> str:
    return f"""
    QPlainTextEdit, QListWidget, QLineEdit, QComboBox, QTextEdit {{
        background: {t['bg']}; color: {t['fg']};
        selection-background-color: {t['sel']}; selection-color: {t['fg']};
        border: 1px solid {t['border']};
    }}
    QListWidget::item {{ color: {t['fg']}; padding: 2px; }}
    QListWidget::item:selected {{ background: {t['sel']}; color: {t['fg']}; }}
    QTabWidget::pane {{ border: 1px solid {t['border']}; background: {t['panel']}; }}
    QScrollArea#preview {{ background: {t['page_bg']}; border: 1px solid {t['border']}; }}
    QScrollArea#preview > QWidget > QWidget {{ background: {t['page_bg']}; }}
    QLabel#pageLabel {{ background: #ffffff; border: 1px solid #888888; }}
    """


# ----------------------------------------------------------------------- source editor
class _LatexHighlighter(QSyntaxHighlighter):
    def __init__(self, doc):
        super().__init__(doc)
        self._rules = []
        self.set_theme("light")

    def set_theme(self, name: str):
        t = THEMES[name]

        def fmt(color, bold=False, italic=False):
            f = QTextCharFormat()
            f.setForeground(QColor(color))
            if bold:
                f.setFontWeight(QFont.Weight.Bold)
            f.setFontItalic(italic)
            return f

        self._rules = [
            (re.compile(r"\$[^$\n]*\$"), fmt(t["math"])),                    # inline math
            (re.compile(r"\\[A-Za-z@]+\*?"), fmt(t["cmd"])),                 # commands
            (re.compile(r"\\noorsheet\b"), fmt(t["noor"], bold=True)),       # figures
            (re.compile(r"\\[A-Za-z]*cite[A-Za-z]*\b"), fmt(t["noor"], bold=True)),
            (re.compile(r"(?<!\\)%.*"), fmt(t["comment"], italic=True)),     # comments
        ]
        self.rehighlight()

    def highlightBlock(self, text):
        for rx, f in self._rules:
            for m in rx.finditer(text):
                self.setFormat(m.start(), m.end() - m.start(), f)


class LatexEditor(QPlainTextEdit):
    """Plain-text editor. Double-click in a ``\\noorsheet{...}`` emits ``sheetRequested``;
    text dropped from the project tree / figure list is inserted at the drop position."""

    sheetRequested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        font.setPointSize(10)
        self.setFont(font)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)
        self.setAcceptDrops(True)
        self.highlighter = _LatexHighlighter(self.document())

    def mouseDoubleClickEvent(self, event):
        cursor = self.cursorForPosition(event.position().toPoint())
        line, col = cursor.block().text(), cursor.positionInBlock()
        for m in _NOOR_LINE_RE.finditer(line):
            if m.start() <= col <= m.end():
                self.sheetRequested.emit(m.group(1).strip())
                return
        super().mouseDoubleClickEvent(event)

    def dropEvent(self, event):
        md = event.mimeData()
        if md.hasText() and event.source() is not self:
            self.setTextCursor(self.cursorForPosition(event.position().toPoint()))
            text = md.text()
            if not text.endswith("\n"):
                text += "\n"
            self.textCursor().insertText(text)
            event.setDropAction(Qt.DropAction.CopyAction)   # never "move": the source stays
            event.accept()
            self.setFocus()
            return
        super().dropEvent(event)


# ------------------------------------------------------------------------ PDF preview
class _PageLabel(QLabel):
    def __init__(self, preview, index):
        super().__init__()
        self.setObjectName("pageLabel")
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
        self.setObjectName("preview")
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

    def _clear(self):
        while self._layout.count():
            w = self._layout.takeAt(0).widget()
            if w is not None:
                w.deleteLater()

    def _placeholder(self, text):
        self._clear()
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
        self._clear()
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


# ---------------------------------------------------------------------- side panels
class _FigureList(QListWidget):
    """Thumbnail list of the project's sheets; drag out = the figure environment as text."""

    def __init__(self, panel, parent=None):
        super().__init__(parent)
        self._panel = panel
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setIconSize(QSize(150, 110))
        self.setGridSize(QSize(170, 150))
        self.setWordWrap(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)

    def startDrag(self, supportedActions):
        items = self.selectedItems()
        if not items:
            return
        drag = QDrag(self)
        md = QMimeData()
        md.setText(self._panel.snippets_for(items))
        drag.setMimeData(md)
        drag.exec(Qt.DropAction.CopyAction, Qt.DropAction.CopyAction)


class FigurePanel(QWidget):
    """Searchable thumbnails of every sheet; double-click or "Insert" puts a figure into
    the document, dragging does too."""

    insertRequested = pyqtSignal(str)       # LaTeX text

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self.host = host
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Search figures: words, #tag, "phrase", -exclude')
        self.search.textChanged.connect(self._filter)
        lay.addWidget(self.search)
        self.list = _FigureList(self)
        self.list.itemDoubleClicked.connect(lambda it: self.insertRequested.emit(
            self.snippets_for([it])))
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.insert_btn = QPushButton("Insert selected")
        self.insert_btn.setToolTip("Insert the selected figures at the cursor "
                                   "(or double-click / drag a thumbnail)")
        self.insert_btn.clicked.connect(lambda: self.insertRequested.emit(
            self.snippets_for(self.list.selectedItems())))
        row.addWidget(self.insert_btn)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        row.addWidget(refresh)
        lay.addLayout(row)
        self._pending: list = []
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._next_thumbnail)
        self.refresh()

    # -- content --------------------------------------------------------------
    def refresh(self):
        self._timer.stop()
        sel = {it.data(Qt.ItemDataRole.UserRole) for it in self.list.selectedItems()}
        self.list.clear()
        placeholder = QIcon()
        for sm in self.host.document_sheets():
            item = QListWidgetItem(placeholder, sm.name)
            item.setData(Qt.ItemDataRole.UserRole, sm.sheet_id)
            item.setSizeHint(QSize(160, 140))     # fixed cell: icon + two lines of text
            item.setToolTip(f"{sm.name}\nid: {sm.sheet_id}"
                            + (f"\ntags: {', '.join(sm.tags)}" if sm.tags else "")
                            + (f"\n{sm.notes[:200]}" if sm.notes else ""))
            self.list.addItem(item)
            if sm.sheet_id in sel:
                item.setSelected(True)
        self._pending = list(range(self.list.count()))
        self._filter(self.search.text())
        self._timer.start(15)

    def _next_thumbnail(self):
        # one thumbnail per tick keeps the window responsive; results are cached on disk
        while self._pending:
            i = self._pending.pop(0)
            item = self.list.item(i)
            if item is None or item.isHidden():
                continue
            pix = self.host.sheet_thumbnail(item.data(Qt.ItemDataRole.UserRole))
            if pix is not None and not pix.isNull():
                item.setIcon(QIcon(pix))
            return
        self._timer.stop()

    def _filter(self, text):
        by_id = {sm.sheet_id: sm for sm in self.host.document_sheets()}
        for i in range(self.list.count()):
            item = self.list.item(i)
            sm = by_id.get(item.data(Qt.ItemDataRole.UserRole))
            fields = [("name", 1.0, sm.name), ("tags", 0.9, " ".join(sm.tags)),
                      ("content", 0.4, sm.notes + " " + sm.sheet_id)] if sm else []
            item.setHidden(bool(text.strip()) and smart_score(text, fields) <= 0)
        self._pending = [i for i in range(self.list.count())
                         if not self.list.item(i).isHidden() and self.list.item(i).icon().isNull()]
        if self._pending and not self._timer.isActive():
            self._timer.start(15)

    def snippets_for(self, items) -> str:
        by_id = {sm.sheet_id: sm for sm in self.host.document_sheets()}
        out = []
        for it in items:
            sm = by_id.get(it.data(Qt.ItemDataRole.UserRole))
            if sm is not None:
                out.append(latex.figure_snippet(sm.sheet_id, sm.name, sm.name))
        return "\n".join(out)


_BIB_STYLES = [
    ("plain  - numbered, sorted by author", "plain", False),
    ("unsrt  - numbered, in order of citation", "unsrt", False),
    ("ieeetr - IEEE-like numbered", "ieeetr", False),
    ("abbrv  - numbered, abbreviated names", "abbrv", False),
    ("alpha  - [Smi20] labels", "alpha", False),
    ("plainnat - author-year (natbib)", "plainnat", True),
    ("abbrvnat - author-year, abbreviated (natbib)", "abbrvnat", True),
    ("unsrtnat - author-year, citation order (natbib)", "unsrtnat", True),
    ("apalike - author-year (APA-like)", "apalike", False),
]


class ReferencePanel(QWidget):
    """The project's bibliography: add from Zotero, cite, link, insert the reference list."""

    insertRequested = pyqtSignal(str)
    pickRequested = pyqtSignal(bool)         # insert a \cite after picking?
    ensurePackage = pyqtSignal(str)          # e.g. "natbib"

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self.host = host
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        self.status = QLabel("")
        lay.addWidget(self.status)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search references: author, year, title, key")
        self.search.textChanged.connect(self._filter)
        lay.addWidget(self.search)
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.itemDoubleClicked.connect(lambda _: self._cite("cite"))
        lay.addWidget(self.list, 1)

        row = QHBoxLayout()
        add = QPushButton("Add from Zotero...")
        add.setToolTip("Open Zotero's picker (Zotero + Better BibTeX must be running); the "
                       "items are stored in this project")
        add.clicked.connect(lambda: self.pickRequested.emit(False))
        row.addWidget(add)
        rm = QPushButton("Remove")
        rm.clicked.connect(self._remove)
        row.addWidget(rm)
        lay.addLayout(row)
        row2 = QHBoxLayout()
        for text, mode, tip in (("\\cite", "cite", "\\cite{key,...} - numbered citations"),
                                ("\\citep", "citep", "\\citep{key} - (Author, year), natbib"),
                                ("\\citet", "citet", "\\citet{key} - Author (year), natbib")):
            b = QPushButton(text)
            b.setToolTip(tip + " for the selected references")
            b.clicked.connect(lambda _=False, m=mode: self._cite(m))
            row2.addWidget(b)
        link = QPushButton("Zotero link")
        link.setToolTip("\\href{zotero://select/...}{Author (year)}: opens the item in Zotero")
        link.clicked.connect(self._link)
        row2.addWidget(link)
        lay.addLayout(row2)

        self.style_combo = QComboBox()
        for text, _name, _nat in _BIB_STYLES:
            self.style_combo.addItem(text)
        lay.addWidget(QLabel("Reference list format:"))
        lay.addWidget(self.style_combo)
        ins = QPushButton("Insert reference list at cursor")
        ins.setToolTip("\\bibliographystyle{...}\\bibliography{references} -- references.bib is "
                       "generated from the stored items (only the cited ones)")
        ins.clicked.connect(self._insert_bibliography)
        lay.addWidget(ins)
        self.refresh()

    def refresh(self):
        keep = {it.data(Qt.ItemDataRole.UserRole) for it in self.list.selectedItems()}
        self.list.clear()
        for rec in sorted(self.host.document_references(),
                          key=lambda r: (r.get("authors") or [[""]])[0][0].lower()):
            item = QListWidgetItem(f"{rec['key']}\n    {zotero.label(rec)}")
            item.setData(Qt.ItemDataRole.UserRole, rec["key"])
            item.setToolTip(zotero.label(rec) + (f"\n{rec['doi']}" if rec.get("doi") else ""))
            self.list.addItem(item)
            if rec["key"] in keep:
                item.setSelected(True)
        self.status.setText(f"{self.list.count()} reference(s) in this project")
        self._filter(self.search.text())

    def _filter(self, text):
        recs = {r["key"]: r for r in self.host.document_references()}
        for i in range(self.list.count()):
            item = self.list.item(i)
            r = recs.get(item.data(Qt.ItemDataRole.UserRole))
            fields = [("name", 1.0, zotero.label(r) if r else ""),
                      ("tags", 0.9, r["key"] if r else ""),
                      ("content", 0.4, (r.get("container", "") + " " + r.get("doi", ""))
                       if r else "")]
            item.setHidden(bool(text.strip()) and smart_score(text, fields) <= 0)

    def _selected_keys(self) -> list:
        return [it.data(Qt.ItemDataRole.UserRole) for it in self.list.selectedItems()]

    def _cite(self, cmd):
        keys = self._selected_keys()
        if not keys:
            return
        if cmd in ("citep", "citet"):
            self.ensurePackage.emit("natbib")
        self.insertRequested.emit(f"\\{cmd}{{{','.join(keys)}}}")

    def _link(self):
        recs = {r["key"]: r for r in self.host.document_references()}
        parts = []
        for k in self._selected_keys():
            r = recs.get(k)
            if r and zotero.zotero_url(r):
                who = (r.get("authors") or [["n.a.", ""]])[0][0].rstrip("*")
                parts.append(f"\\href{{{zotero.zotero_url(r)}}}{{{who} ({r.get('year', '')})}}")
        if parts:
            self.insertRequested.emit(", ".join(parts))

    def _remove(self):
        keys = self._selected_keys()
        if keys and QMessageBox.question(
                self, "Remove references",
                f"Remove {len(keys)} reference(s) from the project?") == \
                QMessageBox.StandardButton.Yes:
            self.host.remove_references(keys)

    def _insert_bibliography(self):
        _text, name, nat = _BIB_STYLES[self.style_combo.currentIndex()]
        if nat:
            self.ensurePackage.emit("natbib")
        self.insertRequested.emit(
            f"\\bibliographystyle{{{name}}}\n\\bibliography{{references}}")


# --------------------------------------------------------------------- document tab
class DocumentTab(QWidget):
    """Editor + preview + build for one :class:`~NoorSuite.model.DocumentModel`."""

    sheetRequested = pyqtSignal(str)      # a sheet id (resolved) to open
    contentChanged = pyqtSignal()
    _built = pyqtSignal(object)           # BuildResult, from the worker thread
    _picked = pyqtSignal(object, bool, str)   # raw Zotero items / error, insert?, message

    AUTO_DELAY_MS = 1500

    def __init__(self, model, host, parent=None):
        super().__init__(parent)
        self.model = model
        self.host = host
        self._building = False
        self._again = False
        self._last_fp = None
        self._pdf = ""
        self._fig_fp: dict = {}               # key -> fingerprint of the exported PDF
        self.workdir = latex.default_workdir(model.doc_id)
        self._settings = QSettings("NoorSuite", "NoorSuite")
        self._theme = "dark" if self._settings.value("doc/dark", False, type=bool) else "light"

        toolbar = QToolBar()
        toolbar.setMovable(False)
        self.compile_act = QAction("Compile", self)
        self.compile_act.setShortcut(QKeySequence("F5"))
        self.compile_act.setToolTip("Export the changed sheets and build the PDF (F5)")
        self.compile_act.triggered.connect(lambda: self.compile(force=True))
        toolbar.addAction(self.compile_act)
        self.auto_check = QCheckBox("Auto-compile")
        self.auto_check.setChecked(True)
        self.auto_check.setToolTip("Rebuild shortly after the text or a referenced sheet changes")
        toolbar.addWidget(self.auto_check)
        toolbar.addSeparator()
        self.panel_act = QAction("Figures && references", self)
        self.panel_act.setCheckable(True)
        self.panel_act.setChecked(True)
        self.panel_act.setToolTip("Show / hide the side panel with figure thumbnails "
                                  "and the reference list")
        toolbar.addAction(self.panel_act)
        cite_act = QAction("Cite (Zotero)...", self)
        cite_act.setShortcut(QKeySequence("Ctrl+Shift+Z"))
        cite_act.setToolTip("Pick items in Zotero and insert \\cite{...} at the cursor "
                            "(Ctrl+Shift+Z)")
        cite_act.triggered.connect(lambda: self.pick_from_zotero(insert=True))
        toolbar.addAction(cite_act)
        toolbar.addSeparator()
        pdf_act = QAction("Export PDF...", self)
        pdf_act.triggered.connect(self.export_pdf)
        toolbar.addAction(pdf_act)
        bundle_act = QAction("Export LaTeX folder...", self)
        bundle_act.setToolTip("main.tex + references.bib + noorsuite.sty + figure PDFs: "
                              "compiles anywhere (journal submission, arXiv, Overleaf)")
        bundle_act.triggered.connect(self.export_bundle)
        toolbar.addAction(bundle_act)
        toolbar.addSeparator()
        self.dark_act = QAction("Dark editor", self)
        self.dark_act.setCheckable(True)
        self.dark_act.setChecked(self._theme == "dark")
        self.dark_act.setToolTip("Switch between black text on white (default) and "
                                 "light text on dark")
        self.dark_act.toggled.connect(self._on_dark_toggled)
        toolbar.addAction(self.dark_act)
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
        mid = QSplitter(Qt.Orientation.Vertical)
        mid.addWidget(self.editor)
        mid.addWidget(self.log)
        mid.setStretchFactor(0, 4)
        mid.setStretchFactor(1, 1)

        self.figures = FigurePanel(host)
        self.figures.insertRequested.connect(self.insert_text)
        self.refs = ReferencePanel(host)
        self.refs.insertRequested.connect(self.insert_text)
        self.refs.pickRequested.connect(lambda ins: self.pick_from_zotero(insert=ins))
        self.refs.ensurePackage.connect(self._ensure_package)
        self.side = QTabWidget()
        self.side.addTab(self.figures, "Figures")
        self.side.addTab(self.refs, "References")
        self.panel_act.toggled.connect(self.side.setVisible)

        self.preview = PdfPreview()
        self.preview.sheetRequested.connect(self._open_ref)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self.side)
        split.addWidget(mid)
        split.addWidget(self.preview)
        self.side.setMaximumWidth(380)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setStretchFactor(2, 1)
        split.setSizes([280, 500, 500])
        self._split = split
        self.panel_act.toggled.connect(lambda on: self._split.setSizes(
            [280 if on else 0, 500, 500]))

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(toolbar)
        lay.addWidget(split, 1)

        self._source_timer = QTimer(self)
        self._source_timer.setSingleShot(True)
        self._source_timer.timeout.connect(lambda: self.compile())
        self._built.connect(self._on_built)
        self._picked.connect(self._on_picked)
        self._apply_theme()

        engine = latex.find_engine()
        self.status_label.setText(
            f"  engine: {Path(engine).stem}" if engine else "  no LaTeX engine found")
        if engine is None:
            self.log.setPlainText(latex.build_pdf(self.workdir, None).log)
        self._load_cached_pdf()

    # -- theme --------------------------------------------------------------------
    def _apply_theme(self):
        self.setStyleSheet(_stylesheet(THEMES[self._theme]))
        self.editor.highlighter.set_theme(self._theme)

    def _on_dark_toggled(self, on: bool):
        self._theme = "dark" if on else "light"
        self._settings.setValue("doc/dark", on)
        self._apply_theme()

    # -- identity / text -------------------------------------------------------------
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

    def insert_text(self, text: str):
        if text:
            self.editor.textCursor().insertText(text if text.endswith("\n") or "\n" not in text
                                                else text + "\n")
            self.editor.setFocus()

    def _ensure_package(self, name: str):
        """Add ``\\usepackage{name}`` before ``\\begin{document}`` if it is missing."""
        src = self.source()
        if re.search(r"\\usepackage(?:\[[^\]]*\])?\{[^}]*\b" + re.escape(name) + r"\b", src):
            return
        m = re.search(r"\\begin\{document\}", src)
        if m is None:
            return
        cur = self.editor.textCursor()
        pos = cur.position()
        cur.setPosition(m.start())
        cur.insertText(f"\\usepackage{{{name}}}\n")
        cur.setPosition(pos + len(f"\\usepackage{{{name}}}\n") if pos >= m.start() else pos)
        self.editor.setTextCursor(cur)

    # -- sheets / references -----------------------------------------------------------
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
        self.figures_dirty = True
        if not self.auto_check.isChecked():
            return
        fp = self._fingerprint()
        if fp != self._last_fp and any(self.referenced_sheets().values()):
            self._source_timer.start(self.AUTO_DELAY_MS)

    def references_changed(self):
        self.refs.refresh()
        if self.auto_check.isChecked():
            self._source_timer.start(self.AUTO_DELAY_MS)

    def _fingerprint(self):
        parts = [self.source()]
        for key, sm in sorted(self.referenced_sheets().items()):
            parts.append(f"{key}:{self.host.sheet_fingerprint(sm.sheet_id) if sm else '-'}")
        used = set(zotero.cite_keys(self.source()))
        parts += [json.dumps(r, sort_keys=True) for r in self.host.document_references()
                  if r["key"] in used]
        return hash("\n".join(parts))

    # -- Zotero --------------------------------------------------------------------------
    def pick_from_zotero(self, insert: bool = True):
        """Show Zotero's picker (in Zotero), store the items, optionally insert ``\\cite``."""
        self.status_label.setText("  waiting for Zotero's picker ...")

        def work():
            try:
                items = zotero.cayw_pick()
                self._picked.emit(items, insert, "")
            except zotero.ZoteroError as exc:
                self._picked.emit([], insert, str(exc))

        threading.Thread(target=work, daemon=True).start()

    def _on_picked(self, items, insert, error):
        if error:
            self.status_label.setText("  Zotero: not available")
            QMessageBox.warning(self, "Zotero", error)
            return
        records = [r for r in (zotero.normalize(i) for i in items) if r]
        if not records:
            self.status_label.setText("  nothing picked")
            return
        keys = self.host.add_references(records)
        self.side.setCurrentWidget(self.refs)
        self.status_label.setText(f"  added {len(keys)} reference(s)")
        if insert:
            self.editor.textCursor().insertText("\\cite{" + ",".join(keys) + "}")
            self.editor.setFocus()

    # -- build ---------------------------------------------------------------------------
    def _load_cached_pdf(self):
        pdf = self.workdir / "out" / "main.pdf"
        if pdf.is_file():
            self._pdf = str(pdf)
            self.preview.load(self._pdf)

    def _export_figures(self, figures: Path):
        """Export the referenced sheets that changed since their PDF was written."""
        missing, exported, cached = [], 0, 0
        refs = self.referenced_sheets()
        for n, (key, sm) in enumerate(refs.items(), 1):
            if sm is None or not re.fullmatch(r"[A-Za-z0-9._-]+", key):
                missing.append(key)
                continue
            target = figures / f"{key}.pdf"
            fp = self.host.sheet_fingerprint(sm.sheet_id)
            if target.is_file() and self._fig_fp.get(key) == fp:
                cached += 1
                continue
            self.status_label.setText(f"  exporting figure {n}/{len(refs)} ...")
            QApplication.processEvents()
            try:
                self.host.export_sheet_pdf(sm.sheet_id, str(target))
                self._fig_fp[key] = fp
                exported += 1
            except Exception as exc:                       # keep building the rest
                missing.append(key)
                self.log.appendPlainText(f"[figure {key}] export failed: {exc}")
        return missing, exported, cached

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
        self._building = True
        self.compile_act.setEnabled(False)
        t0 = time.time()
        latex.prepare_workdir(self.workdir, self.source())
        # references.bib from the stored Zotero items (only the ones the text cites)
        cited = zotero.cite_keys(self.source())
        recs = self.host.document_references()
        known = {r["key"] for r in recs}
        (self.workdir / "references.bib").write_text(
            zotero.bibtex_file(recs, used=cited), encoding="utf-8")
        undefined = [k for k in cited if k not in known]
        # figures on the GUI thread (they are Qt widgets), the engine off-thread
        missing, exported, cached = self._export_figures(self.workdir / latex.FIGURES_DIR)
        t_fig = time.time() - t0
        self.status_label.setText(f"  building ({Path(engine).stem}) ...")

        def work():
            res = latex.build_pdf(self.workdir, engine)
            res.missing = missing
            res.undefined = undefined
            res.fig_seconds, res.fig_exported, res.fig_cached = t_fig, exported, cached
            self._built.emit(res)

        threading.Thread(target=work, daemon=True).start()

    def _on_built(self, res):
        self._building = False
        self.compile_act.setEnabled(True)
        log = res.log
        if getattr(res, "undefined", None):
            log = f"[warning] citation key(s) not in the project's references: " \
                  f"{', '.join(res.undefined)}\n\n" + log
        self.log.setPlainText(log)
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())
        notes = []
        if res.missing:
            notes.append(f"{len(res.missing)} missing sheet(s): {', '.join(res.missing)}")
        if getattr(res, "undefined", None):
            notes.append(f"{len(res.undefined)} unknown citation(s)")
        note = (", " + "; ".join(notes)) if notes else ""
        if res.ok:
            self._pdf = res.pdf
            self.preview.load(res.pdf)
            self.status_label.setText(
                f"  built in {res.seconds + getattr(res, 'fig_seconds', 0):.1f} s "
                f"(LaTeX {res.seconds:.1f} s; figures {getattr(res, 'fig_exported', 0)} new / "
                f"{getattr(res, 'fig_cached', 0)} cached){note}")
        else:
            self.status_label.setText("  build FAILED - see the log" + note)
        if self._again:
            self._again = False
            QTimer.singleShot(50, self.compile)

    # -- export --------------------------------------------------------------------------
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
        if not self._building:
            self.compile(force=True)
        folder = QFileDialog.getExistingDirectory(self, "Export LaTeX folder")
        if not folder:
            return
        dest = Path(folder) / (re.sub(r"[^\w.-]+", "_", self.model.name) or "document")
        latex.write_bundle(dest, self.source(), self.workdir / latex.FIGURES_DIR)
        bib = self.workdir / "references.bib"
        if bib.is_file():
            shutil.copy2(bib, dest / "references.bib")
        self.status_label.setText(f"  exported folder {dest.name}")
