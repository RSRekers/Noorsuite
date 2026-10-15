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
                             QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                             QListView, QListWidget, QListWidgetItem, QMessageBox,
                             QMenu, QPlainTextEdit, QPushButton, QScrollArea, QSplitter,
                             QTabWidget, QToolBar, QToolButton, QVBoxLayout, QWidget)

from . import latex, zotero
from .fuzzy import smart_score

_NOOR_LINE_RE = re.compile(
    r"\\(noorsheet|noorfile|noortable)\s*(?:\[[^\]]*\])?\s*\{([^{}]*)\}")

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
    transformRequested = pyqtSignal(int)          # line number of a figure call

    def contextMenuEvent(self, event):
        menu = self.createStandardContextMenu()
        cur = self.cursorForPosition(event.pos())
        line = cur.blockNumber()
        if _NOOR_LINE_RE.search(cur.block().text()):
            menu.addSeparator()
            menu.addAction("Transform figure (size, rotation, shift)...",
                           lambda: self.transformRequested.emit(line))
        menu.exec(event.globalPos())

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
                key = m.group(2).strip()
                prefix = {"noorfile": "asset:", "noortable": "table:"}.get(m.group(1), "")
                self.sheetRequested.emit(prefix + key)
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
        self.setToolTip("Double-click to edit this figure in NoorSuite" if hit
                        else "Double-click: jump to this place in the text")

    def contextMenuEvent(self, event):
        key = self._preview.link_at(self._index, event.pos())
        if key:
            scale = self._preview._px_per_pt()
            self._preview.figureMenuRequested.emit(key, self._index, event.pos().x() / scale,
                                                   event.pos().y() / scale, event.globalPos())

    def mouseDoubleClickEvent(self, event):
        key = self._preview.link_at(self._index, event.position())
        if key:
            self._preview.sheetRequested.emit(key)
            return
        scale = self._preview._px_per_pt()
        self._preview.sourceRequested.emit(self._index, event.position().x() / scale,
                                           event.position().y() / scale)


class PdfPreview(QScrollArea):
    """Scrollable stack of rendered PDF pages; ``noorsuite://sheet/<key>`` links are hit-tested
    on double-click (``sheetRequested``). Ctrl+wheel zooms, Ctrl+0 fits the width."""

    sheetRequested = pyqtSignal(str)
    sourceRequested = pyqtSignal(int, float, float)       # page (0-based), x, y in points
    figureMenuRequested = pyqtSignal(str, int, float, float, object)

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


class _NoWheelComboBox(QComboBox):
    """A combo box that only changes on click: scrolling a panel with the mouse wheel over
    it must never switch the value (that silently reformatted the bibliography)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event):
        if self.view().isVisible():
            super().wheelEvent(event)
        else:
            event.ignore()                    # let the surrounding panel scroll instead


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
        wrow = QHBoxLayout()
        wrow.addWidget(QLabel("Width:"))
        self.width_combo = _NoWheelComboBox()
        self.width_combo.setToolTip("Figure width for the journal of this document")
        wrow.addWidget(self.width_combo, 1)
        self.match_check = QCheckBox("Resize sheet")
        self.match_check.setChecked(True)
        self.match_check.setToolTip(
            "Also set the sheet's own figure size to this width (aspect ratio kept), so text "
            "and line widths come out at their real size in the journal")
        wrow.addWidget(self.match_check)
        lay.addLayout(wrow)
        self.width_provider = None             # set by the DocumentTab: -> [(label, cm, span)]
        self.two_column_provider = None        # -> True when the layout has two columns
        self.beamer_provider = None            # -> True for presentations (insert frames)
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
        row2 = QHBoxLayout()
        add_file = QPushButton("Add figure file...")
        add_file.setToolTip("Import an SVG, PDF, PNG or JPG into the project. SVGs open in "
                            "Inkscape on double-click in the preview / \"Edit\".")
        add_file.clicked.connect(self._add_files)
        row2.addWidget(add_file)
        self.edit_btn = QPushButton("Edit file")
        self.edit_btn.setToolTip("Open the selected imported figure in Inkscape / its editor")
        self.edit_btn.clicked.connect(self._edit_selected)
        row2.addWidget(self.edit_btn)
        self.remove_btn = QPushButton("Remove file")
        self.remove_btn.clicked.connect(self._remove_selected)
        row2.addWidget(self.remove_btn)
        lay.addLayout(row2)
        self._pending: list = []
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._next_thumbnail)
        self.refresh()

    def refresh_widths(self):
        opts = self.width_provider() if self.width_provider else []
        cur = self.width_combo.currentData()
        self.width_combo.blockSignals(True)
        self.width_combo.clear()
        self.width_combo.addItem("Natural size (sheet size)", None)
        for label, cm, span in opts:
            self.width_combo.addItem(label, (cm, span))
        i = self.width_combo.findData(cur)
        self.width_combo.setCurrentIndex(max(0, i))
        self.width_combo.blockSignals(False)

    # -- content --------------------------------------------------------------
    def refresh(self):
        self.refresh_widths()
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
        for a in self.host.document_assets():
            item = QListWidgetItem(QIcon(), f"{a.name} (.{a.ext})")
            item.setData(Qt.ItemDataRole.UserRole, "asset:" + a.asset_id)
            item.setSizeHint(QSize(160, 140))
            item.setToolTip(f"imported figure file: {a.name}.{a.ext}\nid: {a.asset_id}\n"
                            f"version {a.version}")
            self.list.addItem(item)
            if "asset:" + a.asset_id in sel:
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
            ref = item.data(Qt.ItemDataRole.UserRole)
            pix = (self.host.asset_thumbnail(ref[6:]) if str(ref).startswith("asset:")
                   else self.host.sheet_thumbnail(ref))
            if pix is not None and not pix.isNull():
                item.setIcon(QIcon(pix))
            return
        self._timer.stop()

    def _filter(self, text):
        by_id = {sm.sheet_id: sm for sm in self.host.document_sheets()}
        by_asset = {"asset:" + a.asset_id: a for a in self.host.document_assets()}
        for i in range(self.list.count()):
            item = self.list.item(i)
            ref = item.data(Qt.ItemDataRole.UserRole)
            sm = by_id.get(ref)
            fields = [("name", 1.0, sm.name), ("tags", 0.9, " ".join(sm.tags)),
                      ("content", 0.4, sm.notes + " " + sm.sheet_id)] if sm else []
            if ref in by_asset:
                a = by_asset[ref]
                fields = [("name", 1.0, a.name), ("tags", 0.9, a.ext),
                          ("content", 0.4, a.asset_id)]
            item.setHidden(bool(text.strip()) and smart_score(text, fields) <= 0)
        self._pending = [i for i in range(self.list.count())
                         if not self.list.item(i).isHidden() and self.list.item(i).icon().isNull()]
        if self._pending and not self._timer.isActive():
            self._timer.start(15)

    def snippets_for(self, items) -> str:
        by_id = {sm.sheet_id: sm for sm in self.host.document_sheets()}
        by_asset = {"asset:" + a.asset_id: a for a in self.host.document_assets()}
        out = []
        for it in items:
            ref = it.data(Qt.ItemDataRole.UserRole)
            sm = by_id.get(ref)
            if sm is not None:
                choice = self.width_combo.currentData()
                width, star = "", False
                if choice:
                    cm, span = choice
                    width = f"width={cm:g}cm"
                    star = bool(span) and bool(self.two_column_provider
                                               and self.two_column_provider())
                    if self.match_check.isChecked() and hasattr(self.host, "set_sheet_width"):
                        self.host.set_sheet_width(sm.sheet_id, cm)
                if self.beamer_provider and self.beamer_provider():
                    out.append(latex.frame_snippet(sm.sheet_id, sm.name, width))
                    continue
                out.append(latex.figure_snippet(sm.sheet_id, sm.name, sm.name, width=width,
                                                star=star))
            elif ref in by_asset:
                a = by_asset[ref]
                out.append(latex.asset_snippet(a.asset_id, a.name, a.name))
        return "\n".join(out)

    # -- imported figure files -------------------------------------------------------
    def _add_files(self):
        self.host.import_assets_dialog()
        self.refresh()

    def _selected_assets(self) -> list:
        return [str(it.data(Qt.ItemDataRole.UserRole))[6:] for it in self.list.selectedItems()
                if str(it.data(Qt.ItemDataRole.UserRole)).startswith("asset:")]

    def _edit_selected(self):
        for aid in self._selected_assets():
            self.host.edit_asset(aid)

    def _remove_selected(self):
        ids = self._selected_assets()
        if ids and QMessageBox.question(
                self, "Remove figure file",
                f"Remove {len(ids)} imported figure file(s) from the project? Documents and "
                "plot pictures that use them will show a missing-figure box.") \
                == QMessageBox.StandardButton.Yes:
            for aid in ids:
                self.host.remove_asset(aid)
            self.refresh()


class PresentationView(QWidget):
    """Full-screen slide show of a built presentation PDF.

    Keys: Right / Down / PageDown / Space / Enter / N next, Left / Up / PageUp / Backspace / P
    previous, Home / End first / last, B or . black screen, W or , white screen, L laser pointer,
    a number + Enter jumps, Esc or Q leaves. Mouse: left click next, right click previous."""

    closed = pyqtSignal(int)             # the page that was showing when the show ended
    pageChanged = pyqtSignal(int)

    def __init__(self, pdf_path: str, start: int = 0, parent=None, fade: bool = False):
        super().__init__(parent)
        self._laser = False              # red pointer that follows the mouse (key L)
        self._laser_pos = None           # normalised position inside the slide
        self._fade = fade                # short cross-fade between slides
        self._blend = 1.0
        self._prev_img = None
        self._anim = None
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setStyleSheet("background: black;")
        self.setCursor(Qt.CursorShape.BlankCursor)
        self.setMouseTracking(True)
        self._doc = QPdfDocument(self)
        self._doc.load(pdf_path)
        self._page = max(0, min(start, max(0, self._doc.pageCount() - 1)))
        self._cover = None               # None | "black" | "white"
        self._digits = ""
        self._cache: dict = {}

    def page_count(self) -> int:
        return self._doc.pageCount()

    @property
    def page(self) -> int:
        return self._page

    def go(self, page: int):
        n = self.page_count()
        if n:
            new = max(0, min(int(page), n - 1))
            if self._fade and new != self._page and self.isVisible() \
                    and self._doc.status() == QPdfDocument.Status.Ready:
                self._start_fade(new)
            changed = new != self._page
            self._page = new
            self._cover = None
            self.update()
            if changed:
                self.pageChanged.emit(new)

    def set_laser(self, on: bool):
        self._laser = bool(on)
        if not on:
            self._laser_pos = None
        self.update()

    def laser_at(self, nx: float, ny: float):
        if self._laser:
            self._laser_pos = (nx, ny)
            self.update()

    def laser_hide(self):
        self._laser_pos = None
        self.update()

    def toggle_cover(self, kind: str):
        self._cover = None if self._cover == kind else kind
        self.update()

    def _slide_rect(self):
        from PyQt6.QtCore import QRect
        img = self._image()
        return QRect((self.width() - img.width()) // 2, (self.height() - img.height()) // 2,
                     img.width(), img.height())

    def mouseMoveEvent(self, e):
        if self._laser and self._doc.status() == QPdfDocument.Status.Ready:
            r = self._slide_rect()
            if r.width() and r.height():
                self._laser_pos = ((e.position().x() - r.x()) / r.width(),
                                   (e.position().y() - r.y()) / r.height())
                self.update()

    def _start_fade(self, new: int):
        from PyQt6.QtCore import QVariantAnimation
        try:
            self._prev_img = self._image()
        except Exception:
            self._prev_img = None
            return
        self._blend = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(220)

        def tick(v):
            self._blend = float(v)
            if self._blend >= 1.0:
                self._prev_img = None
            self.update()
        self._anim.valueChanged.connect(tick)
        self._anim.start()

    def _image(self):
        w, h = max(1, self.width()), max(1, self.height())
        key = (self._page, w, h)
        if key not in self._cache:
            if len(self._cache) > 6:
                self._cache.clear()
            pts = self._doc.pagePointSize(self._page)
            scale = min(w / max(1.0, pts.width()), h / max(1.0, pts.height()))
            self._cache[key] = self._doc.render(
                self._page, QSize(max(1, round(pts.width() * scale)),
                                  max(1, round(pts.height() * scale))))
        return self._cache[key]

    def paintEvent(self, _event):
        from PyQt6.QtGui import QColor, QPainter
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("white" if self._cover == "white" else "black"))
        if self._cover is None and self._doc.status() == QPdfDocument.Status.Ready \
                and self.page_count():
            img = self._image()
            if self._prev_img is not None and self._blend < 1.0:
                old = self._prev_img
                p.drawImage((self.width() - old.width()) // 2,
                            (self.height() - old.height()) // 2, old)
                p.setOpacity(self._blend)
            p.drawImage((self.width() - img.width()) // 2, (self.height() - img.height()) // 2,
                        img)
            if self._laser and self._laser_pos is not None:
                from PyQt6.QtCore import QPointF
                from PyQt6.QtGui import QRadialGradient
                r = self._slide_rect()
                c = QPointF(r.x() + self._laser_pos[0] * r.width(),
                            r.y() + self._laser_pos[1] * r.height())
                rad = max(10.0, r.height() / 45.0)
                g = QRadialGradient(c, rad * 2.2)
                g.setColorAt(0.0, QColor(255, 40, 40, 255))
                g.setColorAt(0.35, QColor(255, 40, 40, 230))
                g.setColorAt(1.0, QColor(255, 40, 40, 0))
                p.setOpacity(1.0)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(g)
                p.drawEllipse(c, rad * 2.2, rad * 2.2)
        p.end()

    def keyPressEvent(self, e):
        k, t = e.key(), e.text().lower()
        K = Qt.Key
        if t.isdigit():                              # type a slide number, then Enter
            self._digits += t
            return
        if k in (K.Key_Return, K.Key_Enter) and self._digits:
            self.go(int(self._digits) - 1)
            self._digits = ""
            return
        self._digits = ""
        if k == K.Key_Escape or t == "q":
            self.close()
        elif k in (K.Key_Right, K.Key_Down, K.Key_PageDown, K.Key_Space, K.Key_Return,
                   K.Key_Enter) or t == "n":
            self.go(self._page + 1)
        elif k in (K.Key_Left, K.Key_Up, K.Key_PageUp, K.Key_Backspace) or t == "p":
            self.go(self._page - 1)
        elif k == K.Key_Home:
            self.go(0)
        elif k == K.Key_End:
            self.go(self.page_count() - 1)
        elif t == "l":
            self.set_laser(not self._laser)
        elif t in ("b", "."):
            self._cover = None if self._cover == "black" else "black"
            self.update()
        elif t in ("w", ","):
            self._cover = None if self._cover == "white" else "white"
            self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.RightButton:
            self.go(self._page - 1)
        else:
            self.go(self._page + 1)

    def wheelEvent(self, e):
        self.go(self._page + (1 if e.angleDelta().y() < 0 else -1))

    def closeEvent(self, e):
        self.closed.emit(self._page)
        super().closeEvent(e)


class ReferencePanel(QWidget):
    """The project's bibliography and a live search of the Zotero library: find, add + cite,
    format, reference list."""

    insertRequested = pyqtSignal(str)
    pickRequested = pyqtSignal(bool)         # insert a \cite after picking?
    ensurePackage = pyqtSignal(str)          # a full \usepackage line to make sure is there
    importFileRequested = pyqtSignal()
    importLibraryRequested = pyqtSignal()    # the whole Zotero library
    zoteroSearchRequested = pyqtSignal(str)
    zoteroAddRequested = pyqtSignal(list, bool)    # records, also insert \cite?
    styleChosen = pyqtSignal(str)            # user picked a format for THIS document
    makeDefaultRequested = pyqtSignal(str)
    citeModeChosen = pyqtSignal(str)         # how numbers look in the text (this document)

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self.host = host
        self._zotero_records: list = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        self.status = QLabel("")
        lay.addWidget(self.status)

        self.tabs = QTabWidget()
        # -- page 1: the project's references
        page = QWidget()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(2, 2, 2, 2)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter the project's references")
        self.search.textChanged.connect(self._filter)
        pl.addWidget(self.search)
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.itemDoubleClicked.connect(lambda _: self._cite("cite"))
        pl.addWidget(self.list, 1)
        self.tabs.addTab(page, "In this project")
        # -- page 2: search the whole Zotero library (Better BibTeX JSON-RPC)
        zpage = QWidget()
        zl = QVBoxLayout(zpage)
        zl.setContentsMargins(2, 2, 2, 2)
        self.zsearch = QLineEdit()
        self.zsearch.setPlaceholderText("Search Zotero: author, title, year, citation key ...")
        self._ztimer = QTimer(self)
        self._ztimer.setSingleShot(True)
        self._ztimer.timeout.connect(lambda: self.zoteroSearchRequested.emit(
            self.zsearch.text().strip()))
        self.zsearch.textChanged.connect(lambda _t: self._ztimer.start(450))
        zl.addWidget(self.zsearch)
        self.zlist = QListWidget()
        self.zlist.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.zlist.itemDoubleClicked.connect(lambda _: self._zotero_add(True))
        zl.addWidget(self.zlist, 1)
        self.zstatus = QLabel("Type to search your Zotero library.")
        self.zstatus.setWordWrap(True)
        zl.addWidget(self.zstatus)
        zrow = QHBoxLayout()
        add_cite = QPushButton("Add && cite")
        add_cite.setToolTip("Store the selected items in the project and insert \\cite{...} "
                            "at the cursor (or double-click)")
        add_cite.clicked.connect(lambda: self._zotero_add(True))
        zrow.addWidget(add_cite)
        add = QPushButton("Add to project")
        add.clicked.connect(lambda: self._zotero_add(False))
        zrow.addWidget(add)
        zl.addLayout(zrow)
        self.tabs.addTab(zpage, "Zotero library")
        lay.addWidget(self.tabs, 1)

        row = QHBoxLayout()
        pick = QPushButton("Pick in Zotero...")
        pick.setToolTip("Zotero's own picker window (Better BibTeX 'cite as you write')")
        pick.clicked.connect(lambda: self.pickRequested.emit(False))
        row.addWidget(pick)
        rm = QPushButton("Remove")
        rm.clicked.connect(self._remove)
        row.addWidget(rm)
        lay.addLayout(row)
        row_i = QHBoxLayout()
        imp_lib = QPushButton("Import whole library")
        imp_lib.setToolTip("Copy every item of the Zotero library into this project "
                           "(thousands of entries - normally not needed: cited keys are "
                           "looked up automatically)")
        imp_lib.clicked.connect(self.importLibraryRequested.emit)
        row_i.addWidget(imp_lib)
        imp_file = QPushButton("Import file...")
        imp_file.setToolTip("A .bib (BibTeX) or .json (CSL-JSON) export")
        imp_file.clicked.connect(self.importFileRequested.emit)
        row_i.addWidget(imp_file)
        lay.addLayout(row_i)
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

        self.style_combo = _NoWheelComboBox()
        for st in latex.BIB_STYLES:
            self.style_combo.addItem(st["label"], st["id"])
        self.style_combo.setCurrentIndex(max(0, self.style_combo.findData(latex.get_bib_style())))
        self.style_combo.activated.connect(      # only a real user choice, never programmatic
            lambda _i: self.styleChosen.emit(self.style_combo.currentData()))
        lay.addWidget(QLabel("Citation / reference list format of this document:"))
        lay.addWidget(self.style_combo)
        from .refstyle import CITE_MODES
        self.cite_mode_combo = _NoWheelComboBox()
        for mode, text in CITE_MODES.items():
            self.cite_mode_combo.addItem(text, mode)
        self.cite_mode_combo.activated.connect(
            lambda _i: self.citeModeChosen.emit(self.cite_mode_combo.currentData()))
        lay.addWidget(QLabel("Numbers in the text (ACS Guide allows either):"))
        lay.addWidget(self.cite_mode_combo)
        self.make_default_btn = QPushButton("Use as default for new documents")
        self.make_default_btn.clicked.connect(
            lambda: self.makeDefaultRequested.emit(self.style_combo.currentData()))
        lay.addWidget(self.make_default_btn)
        ins = QPushButton("Insert reference list at cursor")
        ins.setToolTip("Marks where the reference list goes (generated from the cited items)")
        ins.clicked.connect(self._insert_bibliography)
        lay.addWidget(ins)
        self.refresh()

    # -- project references -------------------------------------------------------------
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

    # -- Zotero library search -------------------------------------------------------------
    def set_zotero_results(self, records, message=""):
        self._zotero_records = list(records)
        self.zlist.clear()
        for rec in records[:300]:
            item = QListWidgetItem(zotero.label(rec) + f"\n    {rec['key']}")
            item.setToolTip(zotero.label(rec) + (f"\n{rec['doi']}" if rec.get("doi") else ""))
            item.setData(Qt.ItemDataRole.UserRole, rec["key"])
            self.zlist.addItem(item)
        extra = f" (showing the first 300 - refine the search)" if len(records) > 300 else ""
        self.zstatus.setText(message or f"{len(records)} item(s){extra}")

    def _zotero_add(self, cite: bool):
        keys = {it.data(Qt.ItemDataRole.UserRole) for it in self.zlist.selectedItems()}
        chosen = [r for r in self._zotero_records if r["key"] in keys]
        if chosen:
            self.zoteroAddRequested.emit(chosen, cite)

    # -- inserting -------------------------------------------------------------------------
    def _cite(self, cmd):
        keys = self._selected_keys()
        if not keys:
            return
        if cmd in ("citep", "citet"):
            self.ensurePackage.emit(r"\usepackage{natbib}")
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
        st = latex.bib_style(self.style_combo.currentData())
        for line in st["packages"]:
            self.ensurePackage.emit(line)
        if st.get("native"):                  # NoorSuite writes the list at this marker
            self.insertRequested.emit("\\bibliography{references}")
            return
        self.insertRequested.emit(
            f"\\bibliographystyle{{{st['bst']}}}\n\\bibliography{{references}}")


# --------------------------------------------------------------------- document tab
class DocumentTab(QWidget):
    """Editor + preview + build for one :class:`~NoorSuite.model.DocumentModel`."""

    sheetRequested = pyqtSignal(str)      # a sheet id (resolved) to open
    contentChanged = pyqtSignal()
    _built = pyqtSignal(object)           # BuildResult, from the worker thread
    _picked = pyqtSignal(object, bool, str)   # raw Zotero items / error, insert?, message
    _zsearched = pyqtSignal(object, str, int)  # records, message, generation
    _resolved = pyqtSignal(object, object)     # records found, keys still missing

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
        from . import templates as _templates
        toolbar.addWidget(QLabel(" Layout: "))
        self.layout_combo = _NoWheelComboBox()
        self.populate_layouts()
        self.layout_combo.setToolTip(
            "Journal layout of this document. Switching rewrites the preamble and front "
            "matter (title, authors, abstract) and keeps your text; undoable with Ctrl+Z.")
        self.layout_combo.activated.connect(
            lambda _i: self.set_layout(self.layout_combo.currentData()))
        toolbar.addWidget(self.layout_combo)
        save_layout_act = QAction("Save layout...", self)
        save_layout_act.setToolTip("Keep this document's class, packages and macros as your own "
                                   "reusable layout (appears in the Layout box and in New "
                                   "journal manuscript)")
        save_layout_act.triggered.connect(self.save_layout)
        toolbar.addAction(save_layout_act)
        del_layout_act = QAction("Delete layout...", self)
        del_layout_act.triggered.connect(self.delete_layout)
        toolbar.addAction(del_layout_act)
        toolbar.addSeparator()
        pdf_act = QAction("Export PDF...", self)
        pdf_act.triggered.connect(self.export_pdf)
        toolbar.addAction(pdf_act)
        bundle_act = QAction("Export LaTeX folder...", self)
        bundle_act.setToolTip("main.tex + references.bib + noorsuite.sty + figure PDFs: "
                              "compiles anywhere (journal submission, arXiv, Overleaf)")
        bundle_act.triggered.connect(self.export_bundle)
        toolbar.addAction(bundle_act)
        authors_act = QAction("Authors...", self)
        authors_act.setToolTip("Edit the author list: names, affiliations, e-mail, "
                               "corresponding author (written in the layout's convention)")
        authors_act.triggered.connect(self.edit_authors)
        toolbar.addAction(authors_act)
        si_act = QAction("New SI document", self)
        si_act.setToolTip("A Supporting Information document with this paper's layout, authors "
                          "and macros; figures / tables / equations are numbered S1, S2, ...")
        si_act.triggered.connect(self.new_si_document)
        toolbar.addAction(si_act)
        table_act = QAction("Insert table...", self)
        table_act.setToolTip("A table of a data object (columns, number formats, rows). It stays "
                             "live: the document holds a reference, the numbers follow the data.")
        table_act.triggered.connect(lambda: self.insert_table())
        toolbar.addAction(table_act)
        present_act = QAction("Present", self)
        present_act.setShortcut(QKeySequence("F11"))
        present_act.setToolTip("Run the built PDF as a full-screen slide show (F11). Arrow keys / "
                               "space / click: next, B: black screen, Esc: leave")
        present_act.triggered.connect(lambda: self.present(False))
        toolbar.addAction(present_act)
        presenter_act = QAction("Presenter view", self)
        presenter_act.setShortcut(QKeySequence("Shift+F11"))
        presenter_act.setToolTip(
            "Slide show with a presenter console: current and next slide, your \\note{...}, a "
            "timer with target time, a laser pointer (L). The audience goes full screen on a "
            "second monitor (Shift+F11)")
        presenter_act.triggered.connect(lambda: self.present(True))
        toolbar.addAction(presenter_act)
        pptx_act = QAction("Export PowerPoint...", self)
        pptx_act.setToolTip("Editable .pptx of a presentation (uses your imported PowerPoint "
                            "template's master and layouts when the theme comes from one)")
        pptx_act.triggered.connect(self.export_pptx)
        toolbar.addAction(pptx_act)
        fmt_btn = QToolButton()
        fmt_btn.setText("Format")
        fmt_btn.setToolTip("Format the selection: bold (Ctrl+B), italic (Ctrl+I), underline "
                           "(Ctrl+U), colour, size, lists, alignment, super / subscript")
        fmt_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        fmt_btn.setMenu(self._format_menu(fmt_btn))
        toolbar.addWidget(fmt_btn)
        for keys, kind in (("Ctrl+B", "bold"), ("Ctrl+I", "italic"), ("Ctrl+U", "underline")):
            act = QAction(self)
            act.setShortcut(QKeySequence(keys))
            act.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            act.triggered.connect(lambda _c=False, k=kind: self.format_selection(k))
            self.addAction(act)
        fx = QToolButton()
        fx.setText("Effects")
        fx.setToolTip("Overlays and slide transitions (Beamer): content that appears step by "
                      "step, dimming, transitions. Exported to PowerPoint as click animations.")
        fx.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        fx.setMenu(self._effects_menu(fx))
        toolbar.addWidget(fx)
        docx_act = QAction("Export Word...", self)
        docx_act.setToolTip("Word (.docx) through pandoc: figures as 300 dpi PNGs, numbered "
                            "references, equations as native Word equations")
        docx_act.triggered.connect(lambda: self.export_office("docx"))
        toolbar.addAction(docx_act)
        odt_act = QAction("Export LibreOffice...", self)
        odt_act.setToolTip("LibreOffice Writer (.odt) through pandoc (also opens in Word)")
        odt_act.triggered.connect(lambda: self.export_office("odt"))
        toolbar.addAction(odt_act)
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
        from .findbar import FindBar
        self.findbar = FindBar(self.editor)
        edit_box = QWidget()
        eb = QVBoxLayout(edit_box)
        eb.setContentsMargins(0, 0, 0, 0)
        eb.setSpacing(0)
        eb.addWidget(self.editor, 1)
        eb.addWidget(self.findbar)
        for keys, fn in (("Ctrl+F", lambda: self.findbar.open_find(False)),
                         ("Ctrl+H", lambda: self.findbar.open_find(True)),
                         ("F3", lambda: self.findbar.step(False)),
                         ("Shift+F3", lambda: self.findbar.step(True))):
            act = QAction(self)
            act.setShortcut(QKeySequence(keys))
            act.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            act.triggered.connect(fn)
            self.addAction(act)
        mid = QSplitter(Qt.Orientation.Vertical)
        mid.addWidget(edit_box)
        mid.addWidget(self.log)
        mid.setStretchFactor(0, 4)
        mid.setStretchFactor(1, 1)

        self.figures = FigurePanel(host)
        self.figures.insertRequested.connect(self.insert_text)
        from . import templates as _tpl
        self.figures.width_provider = lambda: _tpl.figure_widths(self.current_layout())
        self.figures.two_column_provider = lambda: _tpl.get(self.current_layout())["two_column"]
        self.figures.beamer_provider = lambda: _tpl.base_of(self.current_layout()) == "beamer"
        self.refs = ReferencePanel(host)
        self.refs.insertRequested.connect(self.insert_text)
        self.refs.pickRequested.connect(lambda ins: self.pick_from_zotero(insert=ins))
        self.refs.ensurePackage.connect(self._ensure_package_line)
        self.refs.importFileRequested.connect(self.import_reference_file)
        self.refs.styleChosen.connect(self.set_bib_style)
        self.refs.citeModeChosen.connect(self._set_cite_mode)
        self.refs.cite_mode_combo.setCurrentIndex(
            max(0, self.refs.cite_mode_combo.findData(model.cite_mode)))
        self.refs.makeDefaultRequested.connect(self._make_default_style)
        self.refs.importLibraryRequested.connect(self.import_zotero_library)
        self.refs.zoteroSearchRequested.connect(self.search_zotero)
        self.refs.zoteroAddRequested.connect(self.add_zotero_records)
        self.side = QTabWidget()
        self.side.addTab(self.figures, "Figures")
        self.side.addTab(self.refs, "References")
        self.panel_act.toggled.connect(self.side.setVisible)

        self.preview = PdfPreview()
        self.built_source = None             # editor text of the last successful build
        self._compiling_source = None
        from .slideeditor import SlideEditor
        self.slide_editor = SlideEditor(self)
        self.right_tabs = QTabWidget()
        self.right_tabs.addTab(self.preview, "PDF preview")
        self.right_tabs.addTab(self.slide_editor, "Slide editor")
        self.right_tabs.currentChanged.connect(self._right_tab_changed)
        self.editor.cursorPositionChanged.connect(self._cursor_moved)
        self.preview.sheetRequested.connect(self._open_ref)
        self.preview.sourceRequested.connect(self.jump_to_source)
        self.preview.figureMenuRequested.connect(self._figure_menu)
        self.editor.transformRequested.connect(lambda ln: self.transform_figure(ln))
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self.side)
        split.addWidget(mid)
        split.addWidget(self.right_tabs)
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
        self._zsearched.connect(self._on_zsearched)
        self._resolved.connect(self._on_resolved)
        self._zgen = 0                       # newest library search wins
        self._lookup_tried: set = set()      # cite keys already looked up in Zotero
        self._apply_theme()

        engine = latex.find_engine()
        self.status_label.setText(
            f"  engine: {Path(engine).stem}" if engine else "  no LaTeX engine found")
        if engine is None:
            self.log.setPlainText(latex.build_pdf(self.workdir, None).log)
        self._load_cached_pdf()
        self.sync_layout_combo()
        QTimer.singleShot(1500, lambda: self._complete_records(None))   # old, thin records

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
        self.sync_layout_combo()

    # -- reference-list format of this document --------------------------------------------
    def bib_style_id(self) -> str:
        """The format in effect: the document's own ``\\bibliographystyle``, else the stored
        per-document choice, else the user's default."""
        return latex.detect_bib_style(self.source(), self.model.bib_style) \
            or self.model.bib_style or latex.get_bib_style()

    def _sync_style_combo(self):
        combo = self.refs.style_combo
        idx = combo.findData(self.bib_style_id())
        if idx >= 0 and idx != combo.currentIndex():
            combo.blockSignals(True)
            combo.setCurrentIndex(idx)
            combo.blockSignals(False)

    def set_bib_style(self, style_id: str):
        """Explicit user choice: store it on the document and rewrite the document's own
        package / ``\\bibliographystyle`` lines (one undoable edit)."""
        self.model.bib_style = style_id
        new = latex.apply_bib_style(self.source(), style_id)
        if new != self.source():
            cur = self.editor.textCursor()
            cur.beginEditBlock()
            cur.select(cur.SelectionType.Document)
            cur.insertText(new)
            cur.endEditBlock()
        self.status_label.setText(f"  format: {latex.bib_style(style_id)['label']}")
        self.contentChanged.emit()
        if self.auto_check.isChecked():
            self._source_timer.start(self.AUTO_DELAY_MS)

    def _replace_source(self, new: str):
        """One undoable edit of the whole text."""
        if new == self.source():
            return
        cur = self.editor.textCursor()
        cur.beginEditBlock()
        cur.select(cur.SelectionType.Document)
        cur.insertText(new)
        cur.endEditBlock()
        self.contentChanged.emit()
        if self.auto_check.isChecked():
            self._source_timer.start(self.AUTO_DELAY_MS)

    def edit_authors(self):
        """Author-list editor; the front matter is rewritten for the document's layout."""
        from . import templates
        from .authorsdialog import AuthorsDialog
        info = templates.parse_authors(self.source())
        dlg = AuthorsDialog(info["authors"], info["affiliations"], self)
        if dlg.exec() != dlg.DialogCode.Accepted:
            return
        new = templates.apply_template(self.source(), self.current_layout(),
                                       dlg.result_authors, dlg.result_affiliations)
        style = self.bib_style_id()
        if style:
            new = latex.apply_bib_style(new, style)
        self._replace_source(new)
        self.status_label.setText(f"  {len(dlg.result_authors)} author(s), "
                                  f"{len(dlg.result_affiliations)} affiliation(s)")

    def new_si_document(self):
        """Create the Supporting Information document of this paper and open it."""
        from . import templates
        try:
            src = templates.si_document(self.source())
        except ValueError as exc:
            QMessageBox.warning(self, "New SI document", str(exc))
            return
        dm = self.host.create_new_document(f"{self.model.name} - SI", src, open_tab=True)
        dm.layout = self.model.layout
        dm.bib_style, dm.cite_mode = self.model.bib_style, self.model.cite_mode
        self.status_label.setText(f"  created \"{dm.name}\"")

    def populate_layouts(self):
        from . import templates
        cur = self.layout_combo.currentData()
        self.layout_combo.blockSignals(True)
        self.layout_combo.clear()
        pres = templates.base_of(templates.detect(self.source())) == "beamer" \
            if hasattr(self, "editor") else False
        for tid, tlabel in templates.list_templates():
            if (templates.base_of(tid) == "beamer") == pres:     # presentations vs documents
                self.layout_combo.addItem(tlabel, tid)
        i = self.layout_combo.findData(cur)
        if i >= 0:
            self.layout_combo.setCurrentIndex(i)
        self.layout_combo.blockSignals(False)

    def current_layout(self) -> str:
        """The layout in effect: the stored user layout while the text still has its class,
        else the built-in layout detected from the text."""
        from . import templates
        det = templates.detect(self.source())
        mine = self.model.layout
        if mine and templates.is_user(mine) and templates.base_of(mine) == det \
                and mine in dict(templates.list_templates()):
            return mine
        return det

    def save_layout(self):
        from . import templates
        name, ok = QInputDialog.getText(self, "Save layout", "Name of the layout:")
        if not ok or not name.strip():
            return
        w, ok = QInputDialog.getText(
            self, "Figure widths",
            "Figure widths in cm, comma separated (single column first, double column last).\n"
            "Leave empty to keep those of the base layout:", text="")
        if not ok:
            return
        try:
            tid = templates.make_user_template(self.source(), name, w.strip() or None)
        except ValueError as exc:
            QMessageBox.warning(self, "Save layout", str(exc))
            return
        self.model.layout = tid
        self.populate_layouts()
        self.sync_layout_combo()
        self.status_label.setText(f"  layout saved: {name.strip()}")

    def delete_layout(self):
        from . import templates
        mine = [(t["label"], i) for i, t in templates.load_user_templates().items()]
        if not mine:
            QMessageBox.information(self, "Delete layout", "You have no saved layouts.")
            return
        label, ok = QInputDialog.getItem(self, "Delete layout", "Layout to delete:",
                                         [m[0] for m in mine], 0, False)
        if ok:
            templates.delete_user_template(dict(mine)[label])
            if self.model.layout == dict(mine)[label]:
                self.model.layout = ""
            self.populate_layouts()
            self.sync_layout_combo()

    def sync_layout_combo(self):
        if not hasattr(self, "layout_combo") or not hasattr(self, "figures"):
            return
        lid = self.current_layout()
        from . import templates as _t
        pres = _t.base_of(lid) == "beamer"
        if getattr(self, "_pres_seen", None) != pres:
            self._pres_seen = pres
            self.populate_layouts()
        i = self.layout_combo.findData(lid)
        if i >= 0 and i != self.layout_combo.currentIndex():
            self.layout_combo.setCurrentIndex(i)
        if lid != getattr(self, "_layout_seen", None):
            self._layout_seen = lid
            self.figures.refresh_widths()

    def set_layout(self, template_id: str):
        """Explicit user choice: re-lay the document out for a journal (one undoable edit)."""
        from . import templates
        if template_id == self.current_layout():
            return
        new = templates.apply_template(self.source(), template_id)
        # keep the document's own citation format working in the new class
        style = self.bib_style_id()
        if style:
            new = latex.apply_bib_style(new, style)
        cur = self.editor.textCursor()
        cur.beginEditBlock()
        cur.select(cur.SelectionType.Document)
        cur.insertText(new)
        cur.endEditBlock()
        self.model.layout = template_id if templates.is_user(template_id) else ""
        self.status_label.setText(f"  layout: {templates.get(template_id)['label']}")
        self.sync_layout_combo()
        self.contentChanged.emit()
        if self.auto_check.isChecked():
            self._source_timer.start(self.AUTO_DELAY_MS)

    def _set_cite_mode(self, mode: str):
        self.model.cite_mode = mode
        self.contentChanged.emit()
        if self.auto_check.isChecked():
            self._source_timer.start(self.AUTO_DELAY_MS)

    def _make_default_style(self, style_id: str):
        latex.set_bib_style(style_id)
        self.status_label.setText(f"  default for new documents: "
                                  f"{latex.bib_style(style_id)['label']}")

    def _on_text_changed(self):
        self.model.source = self.editor.toPlainText()
        self._sync_style_combo()
        self.sync_layout_combo()
        self.contentChanged.emit()
        if self.auto_check.isChecked():
            self._source_timer.start(self.AUTO_DELAY_MS)

    def _cursor_before_end_document(self):
        """The editor cursor, moved in front of ``\\end{document}`` if it was behind it
        (text there is ignored by LaTeX -- citations and figures would silently vanish)."""
        cur = self.editor.textCursor()
        ends = list(re.finditer(r"\\end\{document\}", self.source()))
        if ends and cur.position() > ends[-1].start():
            cur.setPosition(ends[-1].start())
            self.editor.setTextCursor(cur)
            self.status_label.setText("  inserted before \\end{document}")
        return cur

    def insert_text(self, text: str):
        if text:
            cur = self._cursor_before_end_document()
            cur.insertText(text if text.endswith("\n") or "\n" not in text else text + "\n")
            self.editor.setFocus()

    def _ensure_package_line(self, line: str):
        r"""Put a ``\usepackage...`` line before ``\begin{document}`` unless that package (or
        achemso, which brings its own citations) is already loaded."""
        m = re.search(r"\{([^}]*)\}\s*$", line)
        src = self.source()
        if m is None or latex.package_loaded(src, m.group(1)):
            return
        if "natbib" in m.group(1) and latex.package_loaded(src, "achemso"):
            return
        b = re.search(r"\\begin\{document\}", src)
        if b is None:
            return
        cur = self.editor.textCursor()
        pos, ins = cur.position(), line + "\n"
        cur.setPosition(b.start())
        cur.insertText(ins)
        cur.setPosition(pos + len(ins) if pos >= b.start() else pos)
        self.editor.setTextCursor(cur)

    # -- sheets / references -----------------------------------------------------------
    def referenced_sheets(self) -> dict:
        """{key: sheet model or None} for every ``\\noorsheet`` key in the text."""
        sheets = self.host.document_sheets()
        return {k: latex.resolve_ref(k, sheets) for k in latex.figure_refs(self.source())}

    def referenced_assets(self) -> dict:
        """{key: asset or None} for every ``\\noorfile`` key in the text."""
        assets = self.host.document_assets()
        return {k: latex.resolve_asset(k, assets) for k in latex.asset_refs(self.source())}

    def referenced_tables(self) -> dict:
        from . import tables
        tabs = self.host.document_tables()
        return {k: tables.resolve_table(k, tabs) for k in tables.table_refs(self.source())}

    def insert_table(self, table=None):
        """Open the table editor (new table, or ``table`` to edit); a new one is inserted at
        the cursor as ``table`` environment with a ``\\noortable`` reference."""
        from . import tables
        from .tabledialog import TableEditorDialog
        if not self.host.repository:
            QMessageBox.information(self, "Insert table", "The project has no data objects yet.")
            return
        dlg = TableEditorDialog(self.host, table, self)
        if dlg.exec() != dlg.DialogCode.Accepted or dlg.table is None:
            return
        tm = dlg.table
        self.host.upsert_table(tm)
        if table is None:
            key = tables._slug(tm.name) or tm.table_id
            self.insert_text(tables.table_snippet(tm.table_id if key == "" else key, tm.name))
        self.status_label.setText(f"  table \"{tm.name}\" saved")
        self._source_timer.start(self.AUTO_DELAY_MS)

    # -- slide editor glue -------------------------------------------------------------------
    def _right_tab_changed(self, _i):
        if self.right_tabs.currentWidget() is self.slide_editor:
            self.slide_editor.follow_cursor()
            self.slide_editor.refresh()

    def _cursor_moved(self):
        if self.right_tabs.currentWidget() is self.slide_editor and \
                not getattr(self, "_applying", False):
            self.slide_editor.follow_cursor()

    def apply_source(self, new: str):
        """Replace the text by ``new`` with the smallest edit (cursor and scroll position stay)."""
        old = self.source()
        if new == old:
            return
        a = 0
        n = min(len(old), len(new))
        while a < n and old[a] == new[a]:
            a += 1
        b = 0
        while b < n - a and old[len(old) - 1 - b] == new[len(new) - 1 - b]:
            b += 1

        def u16(s, i):
            return len(s[:i].encode("utf-16-le")) // 2
        cur = self.editor.textCursor()
        keep = cur.position()
        self._applying = True
        try:
            cur.beginEditBlock()
            cur.setPosition(u16(old, a))
            cur.setPosition(u16(old, len(old) - b), cur.MoveMode.KeepAnchor)
            cur.insertText(new[a:len(new) - b])
            cur.endEditBlock()
        finally:
            self._applying = False
        c2 = self.editor.textCursor()
        c2.setPosition(min(keep, len(self.editor.toPlainText())))
        self.editor.setTextCursor(c2)

    def _figure_menu(self, key: str, page: int, x: float, y: float, global_pos):
        menu = QMenu(self)
        menu.addAction("Edit in NoorSuite / Inkscape", lambda: self._open_ref(key))
        bare = key[6:] if key.startswith("asset:") else key
        menu.addAction("Transform (size, rotation, shift)...",
                       lambda: self.transform_figure(self._line_at(page, x, y), bare))
        menu.exec(global_pos)

    def _line_at(self, page: int, x: float, y: float):
        """Editor line (0-based) behind a preview point, or None."""
        from . import synctex
        data = synctex.parse(self.workdir / "out" / "main.synctex.gz")
        if data is None:
            return None
        line = synctex.locate(data, page + 1, x, y)
        if line is None:
            return None
        try:
            build = (self.workdir / "main.tex").read_text(encoding="utf-8").split("\n")
        except OSError:
            build = []
        return synctex.match_line(build, self.source().split("\n"), line)

    def transform_figure(self, line, key=None):
        """Dialog for size / rotation / shift of the figure call on ``line`` (or the one with
        ``key`` closest to it); rewrites its options."""
        from . import figureopts
        src = self.source()
        calls = figureopts.find_calls(src)
        if key:
            slug_ok = lambda c: c["key"] == key or latex.slug(c["key"]) == latex.slug(key)  # noqa: E731
            calls = [c for c in calls if slug_ok(c)]
            if not calls:
                self.status_label.setText("  this object is a free slide object - use the "
                                          "Slide editor tab to move / scale / rotate it")
                return
        if not calls:
            return
        pick = min(calls, key=lambda c: abs(c["line"] - (line if line is not None else 0))) \
            if line is not None else calls[0]
        dlg = figureopts.FigureTransformDialog(pick, self)
        if dlg.exec():
            self.apply_source(figureopts.replace_call(src, pick, dlg.updates()))
            self.status_label.setText("  figure options updated")

    def jump_to_source(self, page: int, x: float, y: float):
        """Preview double-click -> the matching line in the editor (through SyncTeX)."""
        from . import synctex
        data = synctex.parse(self.workdir / "out" / "main.synctex.gz")
        if data is None:
            self.status_label.setText("  no SyncTeX data yet - compile once more")
            return
        line = synctex.locate(data, page + 1, x, y)
        if line is None:
            self.status_label.setText("  no source position at that point")
            return
        try:
            build = (self.workdir / "main.tex").read_text(encoding="utf-8").split("\n")
        except OSError:
            build = []
        src = self.source().split("\n")
        target = synctex.match_line(build, src, line)
        block = self.editor.document().findBlockByNumber(target)
        if not block.isValid():
            return
        cur = self.editor.textCursor()
        cur.setPosition(block.position())
        cur.movePosition(cur.MoveOperation.EndOfBlock, cur.MoveMode.KeepAnchor)
        self.editor.setTextCursor(cur)
        self.editor.centerCursor()
        self.editor.setFocus()
        self.status_label.setText(f"  line {target + 1}")

    def _open_ref(self, key: str):
        if key.startswith("table:"):
            from . import tables
            tm = tables.resolve_table(key[6:], self.host.document_tables())
            if tm is None:
                QMessageBox.information(self, "Unknown table",
                                        f"There is no table matching \"{key[6:]}\" in this project.")
            else:
                self.insert_table(tm)
            return
        if key.startswith("asset:"):
            a = latex.resolve_asset(key[6:], self.host.document_assets())
            if a is None:
                QMessageBox.information(self, "Unknown figure file",
                                        f"There is no imported figure matching \"{key[6:]}\".")
            else:
                self.status_label.setText(f"  opening \"{a.name}\" in its editor ...")
                self.host.edit_asset(a.asset_id)
            return
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
        if fp != self._last_fp and (any(self.referenced_sheets().values())
                                    or any(self.referenced_assets().values())
                                    or any(self.referenced_tables().values())):
            self._source_timer.start(self.AUTO_DELAY_MS)

    def references_changed(self):
        self.refs.refresh()
        if self.auto_check.isChecked():
            self._source_timer.start(self.AUTO_DELAY_MS)

    def _fingerprint(self):
        parts = [self.source()]
        for key, sm in sorted(self.referenced_sheets().items()):
            parts.append(f"{key}:{self.host.sheet_fingerprint(sm.sheet_id) if sm else '-'}")
        for key, a in sorted(self.referenced_assets().items()):
            parts.append(f"asset:{key}:{self.host.asset_fingerprint(a.asset_id) if a else '-'}")
        for key, tm in sorted(self.referenced_tables().items()):
            parts.append(f"table:{key}:{self.host.table_fingerprint(tm.table_id) if tm else '-'}")
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
        records = [r if "key" in r and "authors" in r else zotero.normalize(r)
                   for r in items if isinstance(r, dict)]
        records = [r for r in records if r]
        if not records:
            self.status_label.setText("  nothing picked")
            return
        keys = self.host.add_references(records)
        self.side.setCurrentWidget(self.refs)
        self.status_label.setText(f"  added {len(keys)} reference(s)")
        self._complete_records(keys)
        if insert:
            self._cursor_before_end_document().insertText("\\cite{" + ",".join(keys) + "}")
            self.editor.setFocus()

    def import_reference_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import references", "", "References (*.bib *.json *.csljson);;All files (*)")
        if not path:
            return
        try:
            records = zotero.load_file(path)
        except Exception as exc:
            QMessageBox.warning(self, "Import references", f"Could not read {path}:\n{exc}")
            return
        keys = self.host.add_references(records)
        self.status_label.setText(f"  imported {len(keys)} reference(s) from {Path(path).name}")

    def import_zotero_library(self):
        """Copy the whole library into the project (Better BibTeX JSON-RPC, a few seconds)."""
        if QMessageBox.question(
                self, "Import whole library",
                "Copy every item of your Zotero library into this project?\n\n"
                "That can be thousands of entries (it makes the project file larger). "
                "You normally do not need it: search the library in the 'Zotero library' tab "
                "and add what you cite -- unknown \\cite keys are also looked up automatically."
        ) != QMessageBox.StandardButton.Yes:
            return
        self.status_label.setText("  importing the Zotero library ...")

        def work():
            try:
                self._picked.emit([r for r in zotero.rpc_search("")], False, "")
            except zotero.ZoteroError as exc:
                self._picked.emit([], False, str(exc))

        threading.Thread(target=work, daemon=True).start()
        self._importing_records = True

    # -- live search of the Zotero library -----------------------------------------------------
    def search_zotero(self, text: str):
        self._zgen += 1
        gen = self._zgen
        if not text:
            self.refs.set_zotero_results([], "Type to search your Zotero library.")
            return
        self.refs.zstatus.setText("searching Zotero ...")

        def work():
            try:
                recs = zotero.rpc_search(text, limit=2000)
                self._zsearched.emit(recs, "", gen)
            except zotero.ZoteroError as exc:
                self._zsearched.emit([], str(exc), gen)

        threading.Thread(target=work, daemon=True).start()

    def _on_zsearched(self, records, message, gen):
        if gen == self._zgen:
            self.refs.set_zotero_results(records, message)

    def add_zotero_records(self, records, cite: bool):
        keys = self.host.add_references(records)
        self.side.setCurrentWidget(self.refs)
        self.status_label.setText(f"  added {len(keys)} reference(s)")
        if cite and keys:
            self._cursor_before_end_document().insertText("\\cite{" + ",".join(keys) + "}")
            self.editor.setFocus()

    def _complete_records(self, keys=None):
        """Fetch the full data (authors, year, journal, pages, DOI ...) for records that
        only have a title -- picker items often do. Runs in the background, merges in."""
        recs = {r["key"]: r for r in self.host.document_references()}
        todo = [k for k in (keys if keys is not None else recs)
                if k in recs and not zotero.is_complete(recs[k])]
        self._resolve_unknown_keys(todo)

    # -- unknown \cite keys: look them up in Zotero (typed by hand, copied from Obsidian) ---
    def _resolve_unknown_keys(self, undefined):
        todo = [k for k in undefined if k not in self._lookup_tried]
        if not todo:
            return
        self._lookup_tried.update(todo)

        def work():
            try:
                found, missing = zotero.rpc_find_keys(todo)
            except zotero.ZoteroError:
                found, missing = [], todo
            self._resolved.emit(found, missing)

        threading.Thread(target=work, daemon=True).start()

    def _on_resolved(self, found, missing):
        if found:
            self.host.add_references(found)
            self.status_label.setText(f"  found {len(found)} cited key(s) in Zotero")

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
        for key, a in self.referenced_assets().items():          # imported figure files
            if a is None or not re.fullmatch(r"[A-Za-z0-9._-]+", key):
                missing.append(key)
                continue
            fp = self.host.asset_fingerprint(a.asset_id)
            marker = f"asset:{key}"
            if self._fig_fp.get(marker) == fp and any(
                    (figures / f"asset-{key}{e}").is_file() for e in (".pdf", ".png", ".jpg")):
                cached += 1
                continue
            try:
                out = self.host.export_asset(a.asset_id, figures, f"asset-{key}")
            except Exception as exc:
                out = None
                self.log.appendPlainText(f"[figure file {key}] export failed: {exc}")
            if out is None:
                missing.append(key)
            else:
                self._fig_fp[marker] = fp
                exported += 1
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
        self._compiling_source = self.source()
        self.compile_act.setEnabled(False)
        t0 = time.time()
        style_id = self.bib_style_id()
        recs = self.host.document_references()
        text, tab_missing = self.host.expand_document_tables(self.source())
        build_src, bib_notes, info = latex.prepare_build_source(
            text, recs, style_id, self.model.cite_mode)
        bib_notes = list(bib_notes) + [f"no table matches {k}" for k in tab_missing]
        latex.prepare_workdir(self.workdir, build_src)
        # references.bib from the stored items (used by the BibTeX formats)
        (self.workdir / "references.bib").write_text(
            zotero.bibtex_file(recs, used=info["cited"]), encoding="utf-8")
        undefined = info["undefined"]
        incomplete = [k for k in info["cited"] if k in {r["key"] for r in recs}
                      and not zotero.is_complete(next(r for r in recs if r["key"] == k))]
        if undefined or incomplete:
            self._resolve_unknown_keys(undefined + incomplete)
        # figures on the GUI thread (they are Qt widgets), the engine off-thread
        missing, exported, cached = self._export_figures(self.workdir / latex.FIGURES_DIR)
        t_fig = time.time() - t0
        self.status_label.setText(f"  building ({Path(engine).stem}) ...")

        def work():
            res = latex.build_pdf(self.workdir, engine)
            res.missing = missing
            res.undefined = undefined
            res.notes = bib_notes
            res.style = latex.bib_style(style_id)
            res.fig_seconds, res.fig_exported, res.fig_cached = t_fig, exported, cached
            self._built.emit(res)

        threading.Thread(target=work, daemon=True).start()

    def _on_built(self, res):
        self._building = False
        self.compile_act.setEnabled(True)
        log = res.log
        for n in getattr(res, "notes", []):
            log = f"[note] {n}\n\n" + log
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
        if getattr(res, "notes", None):
            notes.append("reference list added to the build (see log)")
        note = (", " + "; ".join(notes)) if notes else ""
        if res.ok:
            self._pdf = res.pdf
            self.built_source = self._compiling_source
            self.preview.load(res.pdf)
            if self.right_tabs.currentWidget() is self.slide_editor:
                self.slide_editor.refresh()
            cites = bool(zotero.cite_keys(self.source()))
            fmt = f"; references: {res.style['id']}" if cites and getattr(res, "style", None) \
                else ""
            self.status_label.setText(
                f"  built in {res.seconds + getattr(res, 'fig_seconds', 0):.1f} s "
                f"(LaTeX {res.seconds:.1f} s; figures {getattr(res, 'fig_exported', 0)} new / "
                f"{getattr(res, 'fig_cached', 0)} cached{fmt}){note}")
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

    # -- text formatting ------------------------------------------------------------------
    def _format_menu(self, parent) -> QMenu:
        from . import textformat
        m = QMenu(parent)
        for label, kind in (("Bold", "bold"), ("Italic", "italic"), ("Underline", "underline"),
                            ("Emphasis", "emph"), ("Highlight (alert)", "alert"),
                            ("Typewriter", "mono"), ("Small caps", "smallcaps"),
                            ("Superscript", "sup"), ("Subscript", "sub"), ("Inline math", "math")):
            m.addAction(label, lambda k=kind: self.format_selection(k))
        m.addSeparator()
        m.addAction("Colour...", self._format_color)
        size = m.addMenu("Size")
        for name in textformat.SIZES:
            size.addAction(name, lambda n=name: self.format_selection("size", n))
        m.addSeparator()
        m.addAction("Centre", lambda: self.format_selection("center"))
        m.addAction("Left-aligned block", lambda: self.format_selection("left"))
        m.addAction("Right-aligned block", lambda: self.format_selection("right"))
        m.addAction("Bullet list (one item per line)", lambda: self.format_selection("itemize"))
        m.addAction("Numbered list (one item per line)",
                    lambda: self.format_selection("enumerate"))
        return m

    def _format_color(self):
        from PyQt6.QtWidgets import QColorDialog
        col = QColorDialog.getColor(QColor("#C0392B"), self, "Text colour")
        if col.isValid():
            self.format_selection("color", col.name())

    def format_selection(self, kind: str, arg: str = ""):
        from . import textformat
        cur = self.editor.textCursor()
        text = cur.selectedText().replace("\u2029", "\n")
        new = textformat.wrap(kind, text, arg)
        cur.beginEditBlock()
        cur.insertText(new)
        if not text:                      # nothing selected: select the placeholder
            cur.setPosition(cur.position() - len(new))
            cur.setPosition(cur.position() + len(new), cur.MoveMode.KeepAnchor)
        cur.endEditBlock()
        self.editor.setTextCursor(cur)
        self.editor.setFocus()

    # -- effects (overlays, transitions) --------------------------------------------------
    def _effects_menu(self, parent) -> QMenu:
        m = QMenu(parent)
        m.addAction("Next content appears on click  (\\pause)",
                    lambda: self.insert_text("\\pause\n"))
        m.addAction("Reveal this list item by item", self.reveal_list)
        m.addAction("Reveal item by item, current item highlighted",
                    lambda: self.reveal_list("[<+-| alert@+>]"))
        m.addSeparator()
        m.addAction("Selection appears from step...", lambda: self.wrap_overlay("uncover"))
        m.addAction("Selection only on step...", lambda: self.wrap_overlay("only"))
        m.addAction("Item appears from step  (\\item<2->)",
                    lambda: self.insert_text("\\item<2-> "))
        m.addSeparator()
        m.addAction("Dim content that has not appeared yet",
                    lambda: self.add_preamble_line("\\setbeamercovered{transparent}"))
        sub = m.addMenu("Slide transition (this slide)")
        sub_all = m.addMenu("Slide transition (all slides)")
        for label, cmd in (("Fade", "\\transfade"), ("Dissolve", "\\transdissolve"),
                           ("Wipe", "\\transwipe"), ("Split", "\\transsplitverticalin"),
                           ("Blinds", "\\transblindshorizontal"),
                           ("Uncover", "\\transuncover")):
            sub.addAction(label, lambda c=cmd: self.insert_text(c + "\n"))
            sub_all.addAction(label, lambda c=cmd: self.add_preamble_line(c))
        return m

    def reveal_list(self, option: str = "[<+->]"):
        """Add the overlay option to the list around the cursor (or insert a new list)."""
        src, pos = self.source(), self.editor.textCursor().position()
        best = None
        for m in re.finditer(r"\\begin\{(itemize|enumerate|description)\}(\s*\[[^\]]*\])?", src):
            end = src.find("\\end{" + m.group(1) + "}", m.end())
            if m.start() <= pos <= (end if end >= 0 else len(src)):
                best = m                              # the innermost list wins (later start)
        if best is None:
            self.insert_text("\\begin{itemize}" + option + "\n  \\item First\n  \\item Second\n"
                             "\\end{itemize}\n")
            return
        cur = self.editor.textCursor()
        cur.beginEditBlock()
        cur.setPosition(best.start() + len("\\begin{" + best.group(1) + "}"))
        cur.setPosition(best.end(), cur.MoveMode.KeepAnchor)
        cur.insertText(option)
        cur.endEditBlock()

    def wrap_overlay(self, cmd: str):
        step, ok = QInputDialog.getInt(
            self, "Overlay step", "Appears with click number:", 2, 1, 50)
        if not ok:
            return
        spec = f"<{step}->" if cmd == "uncover" else f"<{step}>"
        cur = self.editor.textCursor()
        text = cur.selectedText().replace("\u2029", "\n") or "text"
        cur.insertText(f"\\{cmd}{spec}{{{text}}}")

    def add_preamble_line(self, line: str):
        """A line in the preamble (before ``\begin{document}``), once."""
        src = self.source()
        if line in src:
            return
        b = re.search(r"\\begin\{document\}", src)
        if b is None:
            return
        cur = self.editor.textCursor()
        pos, ins = cur.position(), line + "\n"
        cur.setPosition(b.start())
        cur.insertText(ins)
        cur.setPosition(pos + len(ins) if pos >= b.start() else pos)
        self.editor.setTextCursor(cur)
        self.status_label.setText(f"  added {line}")

    def present(self, console: bool = False):
        """Full-screen show of the current PDF (built first if it is stale). With ``console``
        the presenter view (current / next slide, notes, timer, laser) opens as well: the
        audience goes full screen on a second monitor when there is one."""
        if not self._ensure_built():
            return
        view = PresentationView(
            self._pdf, 0, fade=bool(re.search(r"\\trans(?:fade|dissolve)\b", self.source())))
        self._presenter = view
        view.closed.connect(lambda _p: setattr(self, "_presenter", None))
        if not console:
            view.showFullScreen()
            view.setFocus()
            return
        from . import pptxexport
        from .presenter import PresenterConsole
        try:
            parsed = pptxexport.parse_presentation(self.source())
        except Exception:
            parsed = {"frames": []}
        pages = pptxexport.page_map(parsed, view.page_count())
        notes = {i: f.get("note", "") for i, f in enumerate(parsed["frames"]) if f.get("note")}
        titles = {i: (f.get("title") or "") for i, f in enumerate(parsed["frames"])}
        con = PresenterConsole(view, self._pdf, pages, notes, titles)
        self._console = con
        screens = QApplication.screens()
        if len(screens) > 1:
            primary = QApplication.primaryScreen()
            other = next(s for s in screens if s is not primary)
            view.setGeometry(other.geometry())
            view.showFullScreen()
            con.move(primary.geometry().center() - con.rect().center())
            con.show()
        else:                                   # one monitor: the audience window is movable
            view.setWindowFlag(Qt.WindowType.FramelessWindowHint, False)
            view.setCursor(Qt.CursorShape.ArrowCursor)
            view.resize(960, 600)
            view.show()
            con.show()
        con.setFocus()

    def export_pptx(self):
        """Editable PowerPoint file of a Beamer presentation (see :mod:`NoorSuite.pptxexport`)."""
        from . import assets as assets_mod
        from . import templates
        if templates.base_of(templates.detect(self.source())) != "beamer":
            QMessageBox.information(self, "Export PowerPoint",
                                    "Only presentations (document class beamer) can be exported "
                                    "as PowerPoint. Create one with New presentation.")
            return
        try:
            from . import pptxexport
            pptxexport._need_pptx()
        except RuntimeError as exc:
            QMessageBox.information(self, "Export PowerPoint", str(exc))
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export PowerPoint",
                                              f"{self.model.name}.pptx", "PowerPoint (*.pptx)")
        if not path:
            return

        def asset_png(asset, dest):
            if not assets_mod.export_png(asset, dest):
                raise RuntimeError("figure file unreadable")

        effect = "appear"
        if re.search(r"\\pause\b|<[^<>\s]*->|\\(?:uncover|only|visible)<", self.source()):
            label, ok = QInputDialog.getItem(
                self, "Export PowerPoint", "Animation of content that appears step by step:",
                ["Appear", "Fade", "Wipe"], 0, False)
            if not ok:
                return
            effect = label.lower()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            ok, notes = pptxexport.export_pptx(
                self.host.expand_document_tables(self.source())[0], path,
                self.host.document_sheets(), self.host.document_assets(),
                lambda sm, p: self.host.export_sheet_raster(sm, p, 200), asset_png,
                self.workdir / "pptx", effect=effect)
        except Exception as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Export PowerPoint", str(exc))
            return
        QApplication.restoreOverrideCursor()
        self.log.setPlainText("\n".join(notes))
        self.status_label.setText(f"  exported {Path(path).name}"
                                  + (f" ({len(notes)} note(s), see the log)" if notes else ""))

    def export_office(self, fmt: str):
        """Word / LibreOffice document via pandoc (see :mod:`NoorSuite.docexport`)."""
        from . import assets as assets_mod
        from . import docexport
        pandoc = docexport.find_pandoc()
        if pandoc is None:
            QMessageBox.information(self, "pandoc not found", docexport.INSTALL_HINT)
            return
        label = docexport.FORMATS[fmt]
        path, _ = QFileDialog.getSaveFileName(
            self, f"Export {label}", f"{self.model.name}.{fmt}", f"{label} (*.{fmt})")
        if not path:
            return
        def asset_png(asset, dest):
            if not assets_mod.export_png(asset, dest):
                raise RuntimeError("figure file unreadable")

        self.status_label.setText(f"  exporting {fmt} ...")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            ok, log = docexport.export_office(
                self.host.expand_document_tables(self.source())[0],
                self.host.document_references(), self.model.cite_mode,
                self.host.document_sheets(), self.host.document_assets(), path, fmt,
                self.workdir / "office", self.host.export_sheet_raster,
                asset_png, pandoc)
        finally:
            QApplication.restoreOverrideCursor()
        self.log.setPlainText(log)
        if ok:
            self.status_label.setText(f"  exported {Path(path).name}")
        else:
            self.status_label.setText("  export failed (see the log)")
            QMessageBox.warning(self, "Export failed", log[-1500:] or "pandoc failed")

    def export_bundle(self):
        if not self._building:
            self.compile(force=True)
        folder = QFileDialog.getExistingDirectory(self, "Export LaTeX folder")
        if not folder:
            return
        dest = Path(folder) / (re.sub(r"[^\w.-]+", "_", self.model.name) or "document")
        latex.write_bundle(dest, self.host.expand_document_tables(self.source())[0],
                           self.workdir / latex.FIGURES_DIR)
        bib = self.workdir / "references.bib"
        if bib.is_file():
            shutil.copy2(bib, dest / "references.bib")
        self.status_label.setText(f"  exported folder {dest.name}")
