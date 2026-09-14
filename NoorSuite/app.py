"""The SciSuite main window.

Left side (vertical splitter, top -> bottom):
  1. data pool   -- one row per :class:`~NoorSuite.model.DataObject`
  2. column picker -- tick one X and one-or-more Y columns + head() preview
  3. project tree -- folders + sheets (icons); double-click a sheet to open its tab

Center: one tab per open sheet. Right: axes / trace inspectors + the active
subplot's trace list (checkbox = temporarily shown/hidden).
"""
from __future__ import annotations

import json
import os
import time
from io import BytesIO
from pathlib import Path

import numpy as np
from matplotlib import rcParams
from PyQt6.QtCore import QByteArray, QMimeData, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QDrag, QIcon, QImage, QKeySequence
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QDialog,
                             QDialogButtonBox, QFileDialog, QHBoxLayout,
                             QInputDialog, QLabel, QLineEdit, QListWidget,
                             QListWidgetItem, QMainWindow, QMenu, QMessageBox,
                             QPushButton, QComboBox, QSpinBox, QSplitter,
                             QStackedWidget, QStyle, QTableWidget, QTableWidgetItem,
                             QTabWidget, QToolBar, QTreeWidget, QTreeWidgetItem,
                             QVBoxLayout, QWidget)

from .colormap import available_specs, load_file, resolve_spec, sample, save_file
from .dialogs import (AxesDialog, AxesStyleWidget, BulkTraceEditWidget,
                      ColormapPanel, FigureDialog, FigureStyleWidget, ImageDialog,
                      LegendDialog, TextDialog, TraceStyleDialog, TraceStyleWidget)
from .fuzzy import fuzzy_match
from .ipc import (ACTION_ADD_IMAGE_TO_SHEET, ACTION_ADD_TO_SHEET,
                  ACTION_APPEND_DATAFRAME, ACTION_APPEND_IMAGE,
                  ACTION_APPEND_TRACE, ACTION_CLEAR, ACTION_REMOVE_DATA,
                  ACTION_REMOVE_TRACE, DEFAULT_PORT, IPCBridge)
from .model import (INDEX_COL, PLOT_TYPES, ColorMap, DataObject, ImageObject,
                    ImageRef, ProjectModel, SheetModel, SubplotModel, TraceRef,
                    _new_id, aggregate_series, common_label, resolve_sidecar_names)
from .sheet import PlotSheet
from .widgets import CollapsibleSection

APP_NAME = "NOORSUITE"
APP_ID = "NOORSUITE.SciSuite"          # Windows AppUserModelID (taskbar grouping)
_ICON_NAME = "NOORSUITE_ICON.jpg"
_AUTOSAVE_SECONDS = 120               # periodic re-save once a project file is set
_UNTITLED = "Untitled project"

# The Fusion style (set app-wide in __main__.main) doesn't pick up an OS dark theme's
# palette on its own, so a plain checkbox indicator can end up nearly invisible
# (light-on-light or the reverse). Give the checkbox columns explicit, theme-independent
# colours so it's always obvious where to click -- used by ColumnTree and the active
# subplot's trace list.
_CHECKBOX_QSS = """
QTreeView::indicator, QListView::indicator {
    width: 14px; height: 14px; border-radius: 2px;
    border: 1px solid #6b6b6b; background: #ffffff;
}
QTreeView::indicator:hover, QListView::indicator:hover { border-color: #1f77b4; }
QTreeView::indicator:checked, QListView::indicator:checked {
    background: #1f77b4; border: 1px solid #135686;
    image: none;
}
QTreeView::indicator:indeterminate, QListView::indicator:indeterminate {
    background: #a9c9e8; border: 1px solid #135686;
}
"""

# "Apply to all subplots" (AxesStyleWidget) copies every SubplotModel field except the
# ones that are inherently per-subplot *content* rather than style: the title/labels
# (text) and the axis limits (data-range specific -- different subplots often show very
# different y ranges, so forcing one subplot's limits onto another would usually be wrong).
_AXES_STYLE_BROADCAST_FIELDS = tuple(
    f for f in SubplotModel._FIELDS
    if f not in ("title", "x_label", "y_label", "x_min", "x_max", "y_min", "y_max"))
# "Apply to all traces" (TraceStyleWidget) copies line/marker style, not colour (which
# differentiates traces) or scale_factor (which is data-dependent, like axis limits).
_TRACE_STYLE_BROADCAST_FIELDS = ("plot_type", "line_style", "line_width",
                                 "marker", "marker_size", "alpha", "yerr_mode")

ITEM_TYPE_ROLE = Qt.ItemDataRole.UserRole + 1
ITEM_ID_ROLE = Qt.ItemDataRole.UserRole + 2
TYPE_FOLDER = "folder"
TYPE_SHEET = "sheet"
MIME_COLS = "application/x-scisuite-cols"


def icon_path() -> str:
    """Locate the bundled logo (package dir first, then repo root)."""
    here = Path(__file__).resolve().parent
    for candidate in (here / _ICON_NAME, here.parent / _ICON_NAME):
        if candidate.is_file():
            return str(candidate)
    return ""


def app_icon() -> QIcon:
    path = icon_path()
    return QIcon(path) if path else QIcon()


def _cycle_color(n: int) -> str:
    colors = rcParams["axes.prop_cycle"].by_key().get("color", ["#1f77b4"])
    return colors[n % len(colors)]


# =====================================================================
# Column picker tree (middle-left panel)
# =====================================================================
class ColumnTree(QTreeWidget):
    """Lists a data object's columns with an exclusive X tick and Y ticks."""

    selectionValidChanged = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(3)
        self.setHeaderLabels(["Column", "X", "Y"])
        self.setRootIsDecorated(False)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        # ExtendedSelection: Ctrl/Shift click a range of column rows, then tick one
        # row's Y box to check that same box on every selected row at once (a quick
        # way to add several traces in one go). The X/Y checkboxes -- not this row
        # selection -- are what actually drives the drag/"Add to sheet" payload.
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setStyleSheet(_CHECKBOX_QSS)   # keep the X/Y checkboxes visible in any theme
        self._data_id = None
        self._loading = False
        self.itemChanged.connect(self._on_item_changed)

    def set_object(self, obj: DataObject | None):
        self._loading = True
        self.clear()
        self._data_id = obj.id if obj else None
        if obj is not None:
            # "row index" row: X-checkable only (no Y checkbox).
            idx_item = QTreeWidgetItem(self, ["row index", "", ""])
            idx_item.setData(0, Qt.ItemDataRole.UserRole, INDEX_COL)
            idx_item.setFlags(idx_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            idx_item.setCheckState(1, Qt.CheckState.Checked)
            for name in obj.column_order:
                it = QTreeWidgetItem(self, [name, "", ""])
                it.setData(0, Qt.ItemDataRole.UserRole, name)
                it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                it.setCheckState(1, Qt.CheckState.Unchecked)
                it.setCheckState(2, Qt.CheckState.Unchecked)
        self._loading = False
        for i in range(3):
            self.resizeColumnToContents(i)
        self._emit_valid()

    def _on_item_changed(self, item, column):
        if self._loading:
            return
        if column == 1 and item.checkState(1) == Qt.CheckState.Checked:
            self._loading = True
            for i in range(self.topLevelItemCount()):
                other = self.topLevelItem(i)
                if other is not item:
                    other.setCheckState(1, Qt.CheckState.Unchecked)
            self._loading = False
        elif column == 2:
            # Shift/Ctrl-selected several rows? Ticking one row's Y box ticks (or
            # unticks) the same box on every other selected row -- select a range,
            # click once, get all of them as traces.
            selected = self.selectedItems()
            if item in selected and len(selected) > 1:
                state = item.checkState(2)
                self._loading = True
                for other in selected:
                    if other is not item and other.checkState(2) != state \
                            and other.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                        other.setCheckState(2, state)
                self._loading = False
        self._emit_valid()

    def _emit_valid(self):
        self.selectionValidChanged.emit(self.current_selection() is not None)

    def current_selection(self) -> dict | None:
        if self._data_id is None:
            return None
        x_col, y_cols = None, []
        for i in range(self.topLevelItemCount()):
            it = self.topLevelItem(i)
            col = it.data(0, Qt.ItemDataRole.UserRole)
            if it.checkState(1) == Qt.CheckState.Checked:
                x_col = col
            if it.checkState(2) == Qt.CheckState.Checked:
                y_cols.append(col)
        if x_col is None or not y_cols:
            return None
        return {"data_id": self._data_id, "x_col": x_col, "y_cols": y_cols}

    def startDrag(self, supported_actions):
        sel = self.current_selection()
        if not sel:
            return
        mime = QMimeData()
        mime.setData(MIME_COLS, QByteArray(json.dumps(sel).encode("utf-8")))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)


# =====================================================================
# Project tree (bottom-left panel)
# =====================================================================
class ProjectTree(QTreeWidget):
    sheetActivated = pyqtSignal(str)
    columnsDropped = pyqtSignal(object, dict)   # (sheet_id | None, selection)
    deleteRequested = pyqtSignal()              # Delete/Backspace on the selection

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setColumnCount(1)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setAcceptDrops(True)
        # Ctrl / Shift click to multi-select folders + sheets (bulk delete).
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.itemClicked.connect(self._on_activate)
        self.itemDoubleClicked.connect(self._on_activate)

    def _on_activate(self, item, _column):
        # Don't switch tabs while the user is building a multi-selection.
        if len(self.selectedItems()) > 1:
            return
        if item is not None and item.data(0, ITEM_TYPE_ROLE) == TYPE_SHEET:
            self.sheetActivated.emit(item.data(0, ITEM_ID_ROLE))

    def keyPressEvent(self, event):
        if (event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace)
                and self.state() != QAbstractItemView.State.EditingState
                and self.selectedItems()):
            self.deleteRequested.emit()
            return
        super().keyPressEvent(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(MIME_COLS):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat(MIME_COLS):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasFormat(MIME_COLS):
            item = self.itemAt(event.position().toPoint())
            sheet_id = None
            if item is not None and item.data(0, ITEM_TYPE_ROLE) == TYPE_SHEET:
                sheet_id = item.data(0, ITEM_ID_ROLE)
            try:
                payload = json.loads(bytes(event.mimeData().data(MIME_COLS)).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return
            self.columnsDropped.emit(sheet_id, payload)
            event.acceptProposedAction()
            return
        self._move_dragged_items(event)

    @staticmethod
    def _is_descendant(candidate, ancestor) -> bool:
        p = candidate.parent()
        while p is not None:
            if p is ancestor:
                return True
            p = p.parent()
        return False

    def _move_dragged_items(self, event):
        """Reparent/reorder the dragged folder(s)/sheet(s) ourselves via take + insert.

        Only `InternalMove` gets Qt's fast, identity-preserving same-tree path;
        plain `DragDrop` (needed here so a column drag from `ColumnTree` can also
        land on the tree) falls back to a generic MIME round trip for same-widget
        drops too, which is not reliable for a QTreeWidget's own custom item data
        (a moved sheet's `ITEM_ID_ROLE` can come back stale, opening the wrong --
        effectively empty -- sheet on the next click). Doing the move by hand
        keeps the exact same `QTreeWidgetItem` objects, so every role survives.
        """
        dragged = [it for it in self.selectedItems() if it.flags() & Qt.ItemFlag.ItemIsDragEnabled]
        target = self.itemAt(event.position().toPoint())
        pos = self.dropIndicatorPosition()
        if self._reparent_items(dragged, target, pos):
            event.acceptProposedAction()
        else:
            event.ignore()

    def _reparent_items(self, dragged, target, pos) -> bool:
        """Move `dragged` items to sit at `target`/`pos` (a `DropIndicatorPosition`,
        or `None`/`OnViewport` for "append at the root"). Pure tree-item surgery --
        no `QDropEvent` involved, so it's usable directly from `dropEvent` and from
        tests alike. Returns False (no-op) for an empty or invalid drop."""
        root = self.invisibleRootItem()
        # if one dragged item is an ancestor of another, only move the ancestor
        dragged = [it for it in dragged
                  if not any(other is not it and self._is_descendant(it, other) for other in dragged)]
        if not dragged:
            return False
        if target is not None and any(target is it or self._is_descendant(target, it)
                                      for it in dragged):
            return False             # can't drop a folder onto itself or its own contents

        if target is not None and pos == QAbstractItemView.DropIndicatorPosition.OnItem \
                and target.data(0, ITEM_TYPE_ROLE) == TYPE_FOLDER:
            new_parent, insert_at = target, target.childCount()
        elif target is not None:
            new_parent = target.parent() or root
            insert_at = new_parent.indexOfChild(target)
            if pos == QAbstractItemView.DropIndicatorPosition.BelowItem:
                insert_at += 1
        else:
            new_parent, insert_at = root, root.childCount()

        for it in dragged:
            old_parent = it.parent() or root
            idx = old_parent.indexOfChild(it)
            if old_parent is new_parent and idx < insert_at:
                insert_at -= 1        # closing the gap left behind shifts the target down
            old_parent.takeChild(idx)
            new_parent.insertChild(min(insert_at, new_parent.childCount()), it)
            insert_at += 1
            it.setSelected(True)

        if new_parent is not root:
            new_parent.setExpanded(True)
        return True


class ReorderList(QListWidget):
    """QListWidget that emits ``reordered`` after an internal drag-drop move."""

    reordered = pyqtSignal()

    def dropEvent(self, event):
        super().dropEvent(event)
        self.reordered.emit()


class DataPoolList(QListWidget):
    """Data-pool list: Ctrl / Shift click to multi-select, Delete to remove them."""

    deleteRequested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)

    def keyPressEvent(self, event):
        if (event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace)
                and self.selectedItems()):
            self.deleteRequested.emit()
            return
        super().keyPressEvent(event)


class ImageAxesPanel(QWidget):
    """Middle-left page for an ImageObject: pick the two display axes, then add to a sheet."""

    addRequested = pyqtSignal(dict, str)   # ({image_id, display_axes}, "active"|"new")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._image_id = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 2, 6, 2)
        lay.addWidget(QLabel("Image  (pick the two axes to display)"))
        self.info = QLabel("-")
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        row = QHBoxLayout()
        row.addWidget(QLabel("Row axis:"))
        self.row_combo = QComboBox()
        row.addWidget(self.row_combo, 1)
        lay.addLayout(row)
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Col axis:"))
        self.col_combo = QComboBox()
        row2.addWidget(self.col_combo, 1)
        lay.addLayout(row2)
        self.row_combo.currentIndexChanged.connect(self._validate)
        self.col_combo.currentIndexChanged.connect(self._validate)
        btns = QHBoxLayout()
        self.add_active_btn = QPushButton("Add to active subplot")
        self.add_active_btn.clicked.connect(lambda: self._emit("active"))
        self.add_new_btn = QPushButton("Add to new sheet")
        self.add_new_btn.clicked.connect(lambda: self._emit("new"))
        btns.addWidget(self.add_active_btn)
        btns.addWidget(self.add_new_btn)
        lay.addLayout(btns)
        lay.addStretch(1)

    def set_image(self, obj):
        self._image_id = obj.id if obj else None
        self.row_combo.blockSignals(True)
        self.col_combo.blockSignals(True)
        self.row_combo.clear()
        self.col_combo.clear()
        if obj is not None:
            self.info.setText(f"{obj.name}\n{obj.head_meta()}")
            for i, nm in enumerate(obj.axis_names):
                self.row_combo.addItem(f"{nm} ({obj.shape[i]})", i)
                self.col_combo.addItem(f"{nm} ({obj.shape[i]})", i)
            r = max(0, obj.ndim - 2)
            c = max(0, obj.ndim - 1)
            self.row_combo.setCurrentIndex(r)
            self.col_combo.setCurrentIndex(c)
        self.row_combo.blockSignals(False)
        self.col_combo.blockSignals(False)
        self._validate()

    def _validate(self, *_):
        ok = (self._image_id is not None
              and self.row_combo.currentData() is not None
              and self.row_combo.currentData() != self.col_combo.currentData())
        self.add_active_btn.setEnabled(ok)
        self.add_new_btn.setEnabled(ok)

    def _emit(self, target):
        if self._image_id is None:
            return
        r, c = self.row_combo.currentData(), self.col_combo.currentData()
        if r is None or r == c:
            return
        self.addRequested.emit({"image_id": self._image_id,
                                "display_axes": [int(r), int(c)]}, target)


class CommonColumnsDialog(QDialog):
    """Pick a column common to several selected :class:`DataObject`s and add it as
    one trace per object in a single action (right-click a multi-selection in the
    data pool -> "Add common column(s) to sheet...")."""

    def __init__(self, objs: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add common column(s)")
        self.target = None      # set to "active" / "new" by whichever button is clicked
        self._loading = False

        common = sorted(set.intersection(*(set(o.column_order) for o in objs)))
        layout = QVBoxLayout(self)
        info = QLabel(
            f"{len(objs)} data objects: " + ", ".join(o.name for o in objs) + "\n"
            f"{len(common)} column(s) in common -- tick one X, one-or-more Y; a trace is "
            "added for each selected object, for each ticked Y column.")
        info.setWordWrap(True)
        layout.addWidget(info)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Column", "X", "Y"])
        self.tree.setRootIsDecorated(False)
        self.tree.setStyleSheet(_CHECKBOX_QSS)
        idx_item = QTreeWidgetItem(self.tree, ["row index", "", ""])
        idx_item.setData(0, Qt.ItemDataRole.UserRole, INDEX_COL)
        idx_item.setFlags(idx_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        idx_item.setCheckState(1, Qt.CheckState.Checked)
        for name in common:
            it = QTreeWidgetItem(self.tree, [name, "", ""])
            it.setData(0, Qt.ItemDataRole.UserRole, name)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(1, Qt.CheckState.Unchecked)
            it.setCheckState(2, Qt.CheckState.Unchecked)
        self.tree.itemChanged.connect(self._on_item_changed)
        for i in range(3):
            self.tree.resizeColumnToContents(i)
        layout.addWidget(self.tree)

        btns = QHBoxLayout()
        self.active_btn = QPushButton("Add to active subplot")
        self.active_btn.clicked.connect(lambda: self._choose("active"))
        self.new_btn = QPushButton("Add to new sheet")
        self.new_btn.clicked.connect(lambda: self._choose("new"))
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btns.addWidget(self.active_btn)
        btns.addWidget(self.new_btn)
        btns.addWidget(cancel_btn)
        layout.addLayout(btns)

    def _on_item_changed(self, item, column):
        if self._loading or column != 1 or item.checkState(1) != Qt.CheckState.Checked:
            return
        self._loading = True
        for i in range(self.tree.topLevelItemCount()):
            other = self.tree.topLevelItem(i)
            if other is not item:
                other.setCheckState(1, Qt.CheckState.Unchecked)
        self._loading = False

    def _choose(self, target):
        self.target = target
        self.accept()

    def selection(self):
        x_col, y_cols = None, []
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            col = it.data(0, Qt.ItemDataRole.UserRole)
            if it.checkState(1) == Qt.CheckState.Checked:
                x_col = col
            if it.checkState(2) == Qt.CheckState.Checked:
                y_cols.append(col)
        return x_col, y_cols


class CombineTracesDialog(QDialog):
    """"Combine into mean +/- error trace..." on 2+ selected traces in the active
    subplot's trace list -- pick the resulting trace's base plot style (so it's
    "Scatter + errorbar" or "Line + errorbar", the error overlay is independent of
    plot type -- see TraceRef.show_errorbar) and whether to remove the originals."""

    def __init__(self, refs: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Combine into mean ± error trace")
        layout = QVBoxLayout(self)
        names = ", ".join(r.display_label for r in refs)
        info = QLabel(
            f"Combine {len(refs)} traces ({names}) into one trace showing their "
            "row-wise mean, with the std. deviation or the min/max span available as "
            "an error-bar overlay (switch between them afterwards from the Trace "
            "Style tab).")
        info.setWordWrap(True)
        layout.addWidget(info)

        form = QHBoxLayout()
        form.addWidget(QLabel("Base plot style:"))
        self.style_combo = QComboBox()
        self.style_combo.addItems(PLOT_TYPES)
        form.addWidget(self.style_combo, 1)
        layout.addLayout(form)

        self.remove_check = QCheckBox("Remove the original traces")
        self.remove_check.setChecked(True)
        layout.addWidget(self.remove_check)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def selection(self):
        """Return ``(plot_type, remove_originals)``."""
        return self.style_combo.currentText(), self.remove_check.isChecked()


# =====================================================================
# Main window
# =====================================================================
class SciSuiteWindow(QMainWindow):
    def __init__(self, port: int = DEFAULT_PORT):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} - Scientific Comparison & Exploration")
        self.setWindowIcon(app_icon())
        self.resize(1500, 940)

        st = self.style()
        self._folder_icon = st.standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        self._sheet_icon = st.standardIcon(QStyle.StandardPixmap.SP_FileIcon)
        self._data_icon = st.standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView)
        self._image_icon = st.standardIcon(QStyle.StandardPixmap.SP_FileDialogContentsView)

        self.repository: dict[str, DataObject] = {}
        self.images: dict[str, ImageObject] = {}
        self.sheets: dict[str, SheetModel] = {}
        self.open_tabs: dict[str, PlotSheet] = {}
        self.colormaps: list[ColorMap] = []      # project-wide colormap library
        self._ipc_data_full: dict = {}           # full column data for client get_data()
        self._ipc_image_full: dict = {}          # full image arrays for client get_image()
        self.project_path: str | None = None     # set by Save As / Open
        self.port = port
        self.session_file = os.path.expanduser("~/.scisuite_session.json")

        self.ipc = IPCBridge(port=port)
        self.ipc.data_received.connect(self.handle_incoming_ipc)
        self.ipc.start()

        self._init_ui()
        self.load_session()
        if not self.sheets:
            self.create_new_sheet("Sheet 1")
        self._refresh_ipc_snapshot()
        self._update_title()

        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(_AUTOSAVE_SECONDS * 1000)
        self._autosave_timer.timeout.connect(self._periodic_autosave)
        self._autosave_timer.start()

    # --------------------------------------------------------- project identity
    def _project_name(self) -> str:
        return Path(self.project_path).stem if self.project_path else _UNTITLED

    def _update_title(self):
        name = self._project_name()
        self.setWindowTitle(f"{APP_NAME} - {name}")
        if hasattr(self, "project_label"):
            self.project_label.setText(f"  Project: {name}  ")

    # ----------------------------------------------------------------------- UI
    def _init_ui(self):
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_center_panel())
        splitter.addWidget(_in_scroll(self._build_right_panel()))
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 7)
        splitter.setStretchFactor(2, 3)
        self.setCentralWidget(splitter)
        self._init_actions()

    def _build_left_panel(self) -> QWidget:
        vsplit = QSplitter(Qt.Orientation.Vertical)

        # --- 1. data pool ---
        pool = QWidget()
        pl = QVBoxLayout(pool)
        pl.setContentsMargins(6, 6, 6, 2)
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Search data objects...")
        self.search_bar.setClearButtonEnabled(True)
        self.search_bar.textChanged.connect(self._filter_data_pool)
        pl.addWidget(self.search_bar)
        pl.addWidget(QLabel("Data pool"))
        self.data_list = DataPoolList()
        self.data_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.data_list.setMinimumHeight(70)
        self.data_list.currentItemChanged.connect(self._on_data_selected)
        self.data_list.deleteRequested.connect(self._delete_selected_data)
        self.data_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.data_list.customContextMenuRequested.connect(self._data_menu)
        pl.addWidget(self.data_list)
        vsplit.addWidget(pool)

        # --- 2. middle panel: column picker (data objects) / axis picker (images) ---
        self.middle_stack = QStackedWidget()

        picker = QWidget()
        gl = QVBoxLayout(picker)
        gl.setContentsMargins(6, 2, 6, 2)
        gl.addWidget(QLabel("Columns  (tick one X, one-or-more Y)"))
        self.col_tree = ColumnTree()
        self.col_tree.selectionValidChanged.connect(self._update_add_buttons)
        gl.addWidget(self.col_tree)
        self.preview_table = QTableWidget()
        self.preview_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.preview_table.setMaximumHeight(150)
        gl.addWidget(self.preview_table)
        btn_row = QHBoxLayout()
        self.add_active_btn = QPushButton("Add to active subplot")
        self.add_active_btn.clicked.connect(lambda: self._add_from_picker("active"))
        self.add_new_btn = QPushButton("Add to new sheet")
        self.add_new_btn.clicked.connect(lambda: self._add_from_picker("new"))
        btn_row.addWidget(self.add_active_btn)
        btn_row.addWidget(self.add_new_btn)
        gl.addLayout(btn_row)
        self.middle_stack.addWidget(picker)                # page 0

        self.image_panel = ImageAxesPanel()
        self.image_panel.addRequested.connect(self._add_image_from_panel)
        self.middle_stack.addWidget(self.image_panel)      # page 1

        vsplit.addWidget(self.middle_stack)

        # --- 3. project tree ---
        proj = QWidget()
        tl = QVBoxLayout(proj)
        tl.setContentsMargins(6, 2, 6, 6)
        tl.addWidget(QLabel("Project (folders & sheets)"))
        self.tree_search = QLineEdit()
        self.tree_search.setPlaceholderText("Fuzzy search sheets (name, tags, titles, data)...")
        self.tree_search.setClearButtonEnabled(True)
        self.tree_search.textChanged.connect(self._filter_project_tree)
        tl.addWidget(self.tree_search)
        self.tree = ProjectTree()
        self.tree.sheetActivated.connect(self._open_sheet_tab)
        self.tree.columnsDropped.connect(self._on_columns_dropped)
        self.tree.deleteRequested.connect(self._delete_selected_tree_items)
        self.tree.itemChanged.connect(self._on_tree_item_changed)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        tl.addWidget(self.tree)
        vsplit.addWidget(proj)

        vsplit.setChildrenCollapsible(False)
        vsplit.setStretchFactor(0, 3)
        vsplit.setStretchFactor(1, 4)
        vsplit.setStretchFactor(2, 3)
        return vsplit

    def _build_center_panel(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(4, 4, 4, 4)
        self.tab_widget = QTabWidget()
        self.tab_widget.setTabsClosable(True)
        self.tab_widget.tabCloseRequested.connect(self.close_tab)
        self.tab_widget.currentChanged.connect(self.on_sheet_tab_changed)
        layout.addWidget(self.tab_widget)
        return widget

    def _build_right_panel(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(6, 6, 6, 6)

        grid_bar = QHBoxLayout()
        grid_bar.addWidget(QLabel("Grid:"))
        self.rows_spin = QSpinBox(); self.rows_spin.setRange(1, 6); self.rows_spin.setPrefix("rows ")
        self.cols_spin = QSpinBox(); self.cols_spin.setRange(1, 6); self.cols_spin.setPrefix("cols ")
        self.rows_spin.valueChanged.connect(self.on_grid_changed)
        self.cols_spin.valueChanged.connect(self.on_grid_changed)
        grid_bar.addWidget(self.rows_spin)
        grid_bar.addWidget(self.cols_spin)
        reset_btn = QPushButton("Reset View")
        reset_btn.clicked.connect(self.reset_zoom_current_subplot)
        grid_bar.addWidget(reset_btn)
        layout.addLayout(grid_bar)

        strip_body = QWidget()
        sb = QVBoxLayout(strip_body)
        sb.setContentsMargins(0, 0, 0, 0)
        self.subplot_strip = ReorderList()
        self.subplot_strip.setMaximumHeight(96)
        self.subplot_strip.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.subplot_strip.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.subplot_strip.reordered.connect(self._on_strip_reordered)
        self.subplot_strip.itemSelectionChanged.connect(self._on_strip_selection)
        sb.addWidget(self.subplot_strip)
        layout.addWidget(CollapsibleSection("Subplot order (drag to rearrange)", strip_body))

        traces_body = QWidget()
        tb = QVBoxLayout(traces_body)
        tb.setContentsMargins(0, 0, 0, 0)
        self.subplot_traces = ReorderList()
        self.subplot_traces.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.subplot_traces.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.subplot_traces.setStyleSheet(_CHECKBOX_QSS)   # keep "enabled" checkboxes visible
        self.subplot_traces.itemChanged.connect(self._on_subplot_trace_toggled)
        self.subplot_traces.itemSelectionChanged.connect(self._on_subplot_trace_selection)
        self.subplot_traces.reordered.connect(self._on_traces_reordered)
        tb.addWidget(self.subplot_traces)
        rm_btn = QPushButton("Remove selected trace(s)")
        rm_btn.clicked.connect(self._remove_selected_subplot_traces)
        tb.addWidget(rm_btn)
        self.combine_btn = QPushButton("Combine into mean ± error trace...")
        self.combine_btn.setToolTip(
            "Select 2 or more traces that share the same x values (e.g. repeat "
            "measurements) and merge them into one trace showing their mean, with an "
            "error-bar overlay (std. deviation or min/max span).")
        self.combine_btn.setEnabled(False)
        self.combine_btn.clicked.connect(self._combine_selected_traces_dialog)
        tb.addWidget(self.combine_btn)
        layout.addWidget(CollapsibleSection("Active subplot traces (tick = shown)", traces_body))

        self.colormap_panel = ColormapPanel(self)
        self.colormap_panel.applied.connect(self._after_colormap_applied)
        layout.addWidget(CollapsibleSection("Colours / colormap", self.colormap_panel,
                                            expanded=False))

        self.inspector_tabs = QTabWidget()

        self.axes_widget = AxesStyleWidget()
        self.axes_widget.changed.connect(self._after_axes_edit)
        axes_tab = QWidget()
        axes_tab_l = QVBoxLayout(axes_tab)
        axes_tab_l.setContentsMargins(0, 0, 0, 0)
        axes_tab_l.addWidget(_in_scroll(self.axes_widget), 1)
        self.axes_broadcast_btn = QPushButton("Apply this style to all subplots")
        self.axes_broadcast_btn.setToolTip(
            "Copies this subplot's tick/spine/background/grid/legend/number-format/aspect "
            "style to every other subplot in the sheet. Title, axis labels, and axis limits "
            "stay per-subplot.")
        self.axes_broadcast_btn.clicked.connect(self._apply_axes_style_to_all_subplots)
        axes_tab_l.addWidget(self.axes_broadcast_btn)
        self.inspector_tabs.addTab(axes_tab, "Axes / Panel")

        self.trace_widget = TraceStyleWidget()
        self.trace_widget.changed.connect(self._after_trace_edit)
        self.bulk_widget = BulkTraceEditWidget()
        self.bulk_widget.changed.connect(self._after_trace_edit)
        self._trace_tab_stack = QWidget()
        _sl = QVBoxLayout(self._trace_tab_stack)
        _sl.setContentsMargins(0, 0, 0, 0)
        _sl.addWidget(self.trace_widget)
        _sl.addWidget(self.bulk_widget)
        self.bulk_widget.hide()
        trace_tab = QWidget()
        trace_tab_l = QVBoxLayout(trace_tab)
        trace_tab_l.setContentsMargins(0, 0, 0, 0)
        trace_tab_l.addWidget(_in_scroll(self._trace_tab_stack), 1)
        self.trace_broadcast_btn = QPushButton("Apply this trace's line style to all traces")
        self.trace_broadcast_btn.setToolTip(
            "Copies plot type, line style, line width, marker, marker size and opacity (not "
            "colour) from this trace to every other trace in every subplot of the sheet. "
            "Enabled when exactly one trace is selected.")
        self.trace_broadcast_btn.clicked.connect(self._apply_trace_style_to_all_traces)
        self.trace_broadcast_btn.setEnabled(False)
        trace_tab_l.addWidget(self.trace_broadcast_btn)
        self.inspector_tabs.addTab(trace_tab, "Trace Style")

        self.figure_widget = FigureStyleWidget()
        self.figure_widget.changed.connect(self._after_figure_edit)
        self.inspector_tabs.addTab(_in_scroll(self.figure_widget), "Figure")
        self.inspector_tabs.setMinimumHeight(260)
        layout.addWidget(self.inspector_tabs, 1)
        return widget

    def _init_actions(self):
        toolbar = QToolBar("Main Actions")
        self.addToolBar(toolbar)

        self.project_label = QLabel()
        self.project_label.setStyleSheet("font-weight: bold; font-size: 13px;")
        toolbar.addWidget(self.project_label)
        toolbar.addSeparator()

        save_act = QAction("Save", self)
        save_act.setShortcut(QKeySequence.StandardKey.Save)   # Ctrl+S / Strg+S
        save_act.setToolTip("Save to the current project file (Ctrl+S)")
        save_act.triggered.connect(self._quick_save)
        self.addAction(save_act)                              # window-level shortcut
        toolbar.addAction(save_act)

        for text, slot in (
            ("Save As...", self.save_project_file),
            ("Open Project", self.load_project_file),
            (None, None),
            ("New Folder", lambda: self._add_folder_item("New Folder", self._selected_folder())),
            ("New Sheet", lambda: self.create_new_sheet()),
            (None, None),
            ("Copy Plot", self.copy_plot_to_clipboard),
            ("Export SVG", self.export_svg),
        ):
            if text is None:
                toolbar.addSeparator()
                continue
            action = QAction(text, self)
            action.triggered.connect(slot)
            toolbar.addAction(action)

        self.statusBar().showMessage("Ready")

    # -------------------------------------------------------------- data pool
    def _refresh_data_list(self):
        keep = None
        if self.data_list.currentItem():
            keep = self.data_list.currentItem().data(Qt.ItemDataRole.UserRole)
        self.data_list.blockSignals(True)
        self.data_list.clear()
        for obj in self.repository.values():
            item = QListWidgetItem(self._data_icon, f"{obj.name}  ({obj.nrows}x{obj.ncols})")
            item.setData(Qt.ItemDataRole.UserRole, obj.id)
            if obj.tags:
                item.setToolTip(", ".join(obj.tags))
            self.data_list.addItem(item)
        for obj in self.images.values():
            item = QListWidgetItem(self._image_icon,
                                   f"{obj.name}  [{'x'.join(str(s) for s in obj.shape)}]")
            item.setData(Qt.ItemDataRole.UserRole, obj.id)
            item.setToolTip(obj.head_meta())
            self.data_list.addItem(item)
        self.data_list.blockSignals(False)
        if keep:
            for i in range(self.data_list.count()):
                if self.data_list.item(i).data(Qt.ItemDataRole.UserRole) == keep:
                    self.data_list.setCurrentRow(i)
                    break
        if self.data_list.currentItem() is None and self.data_list.count():
            self.data_list.setCurrentRow(0)
        self._on_data_selected(self.data_list.currentItem(), None)

    def _on_data_selected(self, current, _previous):
        key = current.data(Qt.ItemDataRole.UserRole) if current else None
        img = self.images.get(key)
        if img is not None:
            self.middle_stack.setCurrentIndex(1)
            self.image_panel.set_image(img)
            self.col_tree.set_object(None)
            self._fill_preview(None)
            return
        self.middle_stack.setCurrentIndex(0)
        obj = self.repository.get(key)
        self.col_tree.set_object(obj)
        self._fill_preview(obj)
        self._update_add_buttons()

    def _fill_preview(self, obj: DataObject | None):
        self.preview_table.clear()
        if obj is None:
            self.preview_table.setRowCount(0)
            self.preview_table.setColumnCount(0)
            return
        headers, rows = obj.head(8)
        self.preview_table.setColumnCount(len(headers))
        self.preview_table.setHorizontalHeaderLabels(headers)
        self.preview_table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                self.preview_table.setItem(r, c, QTableWidgetItem(f"{value:g}"))

    def _update_add_buttons(self, *_):
        ok = self.col_tree.current_selection() is not None
        self.add_active_btn.setEnabled(ok)
        self.add_new_btn.setEnabled(ok)

    def _data_menu(self, pos):
        item = self.data_list.itemAt(pos)
        if item is None:
            return
        selected = self.data_list.selectedItems()
        if item in selected and len(selected) > 1:
            keys = [it.data(Qt.ItemDataRole.UserRole) for it in selected]
            objs = [self.repository[k] for k in keys if k in self.repository]
            menu = QMenu()
            common_act = None
            if len(objs) > 1 and len(objs) == len(keys):   # no images mixed in
                common_act = menu.addAction("Add common column(s) to sheet...")
            del_act = menu.addAction(f"Delete {len(selected)} selected")
            action = menu.exec(self.data_list.viewport().mapToGlobal(pos))
            if action == common_act:
                self._add_common_columns_dialog(objs)
            elif action == del_act:
                self._delete_selected_data()
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        img = self.images.get(key)
        if img is not None:
            menu = QMenu()
            del_act = menu.addAction("Delete image")
            if menu.exec(self.data_list.viewport().mapToGlobal(pos)) == del_act:
                self._remove_image(img.id)
            return
        obj = self.repository.get(key)
        if obj is None:
            return
        menu = QMenu()
        rename_act = menu.addAction("Rename...")
        retag_act = menu.addAction("Set tags...")
        del_act = menu.addAction("Delete data object")
        action = menu.exec(self.data_list.viewport().mapToGlobal(pos))
        if action == rename_act:
            self._rename_data_object(obj)
        elif action == retag_act:
            self._retag_data_object(obj)
        elif action == del_act:
            self._confirm_remove_data(obj)

    def _rename_data_object(self, obj):
        from PyQt6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "Rename data object", "Name:", text=obj.name)
        if ok and name:
            obj.name = name
            self._refresh_data_list()
            self._refresh_open_sheets()
            self._refresh_ipc_snapshot()

    def _retag_data_object(self, obj):
        from PyQt6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(self, "Set tags", "Comma-separated tags:",
                                        text=", ".join(obj.tags))
        if ok:
            obj.tags = [t.strip() for t in text.split(",") if t.strip()]
            self._refresh_data_list()
            self._refresh_ipc_snapshot()

    def _confirm_remove_data(self, obj):
        n = sum(1 for sm in self.sheets.values() for sub in sm.subplots
                for t in sub.traces if t.data_id == obj.id)
        if n and QMessageBox.question(
                self, "Delete data object",
                f"'{obj.name}' is used by {n} trace(s). Delete it and those traces?"
        ) != QMessageBox.StandardButton.Yes:
            return
        self._remove_data(obj.id)

    def _delete_selected_data(self):
        """Remove every data object / image currently selected in the data pool."""
        keys = [it.data(Qt.ItemDataRole.UserRole)
                for it in self.data_list.selectedItems() if not it.isHidden()]
        objs = [self.repository[k] for k in keys if k in self.repository]
        imgs = [self.images[k] for k in keys if k in self.images]
        if not objs and not imgs:
            return
        if len(objs) + len(imgs) == 1:
            if objs:
                self._confirm_remove_data(objs[0])
            else:
                self._remove_image(imgs[0].id)
            return
        obj_ids = {o.id for o in objs}
        ntr = sum(1 for sm in self.sheets.values() for sub in sm.subplots
                  for t in sub.traces if t.data_id in obj_ids)
        what = " and ".join(p for p in (
            f"{len(objs)} data object(s)" if objs else "",
            f"{len(imgs)} image(s)" if imgs else "") if p)
        msg = f"Delete {what}?"
        if ntr:
            msg += f"\n\nThis also removes {ntr} trace(s) that use them."
        if QMessageBox.question(self, "Delete selected", msg) \
                != QMessageBox.StandardButton.Yes:
            return
        for obj in objs:
            self._remove_data(obj.id)
        for img in imgs:
            self._remove_image(img.id)

    # -------------------------------------------------------------- add traces
    def _add_from_picker(self, target):
        sel = self.col_tree.current_selection()
        if not sel:
            return
        if target == "new":
            sm = self._create_sheet_model()
        else:
            sm = self._active_sheet_model() or self._create_sheet_model()
        self._open_sheet_tab(sm.sheet_id)
        self._add_traces(sel, sm.sheet_id)

    def _on_columns_dropped(self, sheet_id, sel):
        if sheet_id is None or sheet_id not in self.sheets:
            sheet_id = self._create_sheet_model().sheet_id
        self._open_sheet_tab(sheet_id)
        self._add_traces(sel, sheet_id)

    def _add_traces(self, sel, sheet_id, subplot_index=None):
        data = self.repository.get(sel.get("data_id"))
        sm = self.sheets.get(sheet_id)
        if data is None or sm is None:
            return
        si = sm.active_index if subplot_index is None else min(subplot_index, len(sm.subplots) - 1)
        sub = sm.subplots[si]
        for y_col in sel.get("y_cols", []):
            if y_col not in data.columns:
                continue
            ref = TraceRef(data.id, sel.get("x_col", INDEX_COL), y_col)
            ref.color = self._next_trace_color(sm, sub)
            sub.traces.append(ref)
        self._render_sheet(sheet_id)
        if sm is self._active_sheet_model():
            self._sync_subplot_trace_list()
        self._refresh_ipc_snapshot()

    def _add_common_columns_dialog(self, objs: list):
        """Right-click action on several selected data objects: pick a column they
        all share and add it as one trace per object, in a single action."""
        common = sorted(set.intersection(*(set(o.column_order) for o in objs)))
        if not common:
            QMessageBox.information(
                self, "No common columns",
                "The selected data objects don't share any column names.")
            return
        dlg = CommonColumnsDialog(objs, self)
        if dlg.exec() != QDialog.DialogCode.Accepted or dlg.target is None:
            return
        x_col, y_cols = dlg.selection()
        if not y_cols:
            return
        self._add_common_columns(objs, x_col or INDEX_COL, y_cols, dlg.target)

    def _add_common_columns(self, objs: list, x_col: str, y_cols: list, target: str):
        """Add one trace per (object, y_col) pair -- ``objs`` share ``x_col``/``y_cols``
        by construction (see `_add_common_columns_dialog`)."""
        sm = self._create_sheet_model() if target == "new" \
            else (self._active_sheet_model() or self._create_sheet_model())
        self._open_sheet_tab(sm.sheet_id)
        sub = sm.get_active_subplot()
        for obj in objs:
            for y_col in y_cols:
                if y_col not in obj.columns:
                    continue
                ref = TraceRef(obj.id, x_col, y_col, label=f"{obj.name}: {y_col}")
                ref.color = self._next_trace_color(sm, sub)
                sub.traces.append(ref)
        self._render_sheet(sm.sheet_id)
        if sm is self._active_sheet_model():
            self._sync_subplot_trace_list()
        self._refresh_ipc_snapshot()

    def _combine_selected_traces_dialog(self):
        """"Combine into mean +/- error trace..." on 2+ selected traces in the active
        subplot's trace list -- a "joint series" (e.g. several repeat measurements each
        already added as their own trace). Each is resolved with its own current style
        (so a per-series Y-transform, e.g. "Relative to nth point...", is already baked
        in if the user set one on each first), row-wise aggregated into mean/std/min-max,
        and replaces the selection with one new trace carrying the result plus an
        errorbar overlay -- see :class:`CombineTracesDialog`."""
        refs = self._selected_trace_refs()
        if len(refs) < 2:
            return
        sm = self._active_sheet_model()
        if sm is None:
            return
        resolved = []
        for ref in refs:
            data = self.repository.get(ref.data_id)
            if data is None or ref.y_col not in data.columns:
                QMessageBox.warning(self, "Can't combine",
                                    f"'{ref.display_label}' has no data to resolve.")
                return
            try:
                x, y = ref.resolve(data)
            except Exception as e:
                QMessageBox.warning(self, "Can't combine", str(e))
                return
            resolved.append((x, y))
        x0 = resolved[0][0]
        for x, _ in resolved[1:]:
            if len(x) != len(x0) or not np.allclose(x, x0, equal_nan=True):
                QMessageBox.warning(
                    self, "Can't combine",
                    "The selected traces don't share the same x values -- combining "
                    "them row-by-row wouldn't be meaningful.")
                return

        dlg = CombineTracesDialog(refs, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        plot_type, remove_originals = dlg.selection()
        self._combine_selected_traces(sm, refs, x0, [y for _, y in resolved],
                                      plot_type, remove_originals)

    def _combine_selected_traces(self, sm, refs, x, ys, plot_type, remove_originals):
        agg = aggregate_series(ys)
        label = common_label([r.display_label for r in refs])
        columns = {"x": x, "mean": agg["mean"], "std": agg["std"],
                  "err_min": agg["err_min"], "err_max": agg["err_max"]}
        new_obj = DataObject(f"{label} (n={len(refs)} mean±err)", columns,
                             ["x", "mean", "std", "err_min", "err_max"], source="combined")
        self.repository[new_obj.id] = new_obj

        # _selected_trace_refs() only ever returns rows from the active subplot's own
        # trace list, so that's where every ref in `refs` lives.
        sub = sm.get_active_subplot()
        insert_at = min((sub.traces.index(r) for r in refs if r in sub.traces),
                        default=len(sub.traces))
        color = refs[0].color

        new_ref = TraceRef(new_obj.id, "x", "mean", label=label)
        new_ref.plot_type = plot_type
        new_ref.color = color
        new_ref.show_errorbar = True
        new_ref.yerr_col = "std"
        new_ref.yerr_low_col = "err_min"
        new_ref.yerr_high_col = "err_max"

        if remove_originals:
            for sub2 in sm.subplots:
                sub2.traces = [t for t in sub2.traces if t not in refs]
            sub.traces.insert(min(insert_at, len(sub.traces)), new_ref)
        else:
            sub.traces.append(new_ref)

        self._render_sheet(sm.sheet_id)
        if sm is self._active_sheet_model():
            self._sync_subplot_trace_list()
        self._refresh_data_list()
        self._refresh_ipc_snapshot()
        self._refresh_ipc_data()

    # -------------------------------------------------------------- sheet/tabs
    def _create_sheet_model(self, name=None) -> SheetModel:
        name = name or f"Sheet {len(self.sheets) + 1}"
        sm = SheetModel(name=name)
        self.sheets[sm.sheet_id] = sm
        self._add_sheet_tree_item(sm, self._selected_folder())
        return sm

    def create_new_sheet(self, name=None) -> SheetModel:
        sm = self._create_sheet_model(name)
        self._open_sheet_tab(sm.sheet_id)
        return sm

    def _add_sheet_tree_item(self, sm: SheetModel, parent=None) -> QTreeWidgetItem:
        self.tree.blockSignals(True)
        item = QTreeWidgetItem(parent or self.tree.invisibleRootItem())
        item.setText(0, sm.name)
        item.setIcon(0, self._sheet_icon)
        item.setData(0, ITEM_TYPE_ROLE, TYPE_SHEET)
        item.setData(0, ITEM_ID_ROLE, sm.sheet_id)
        if sm.tags:
            item.setToolTip(0, ", ".join(sm.tags))
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable
                      | Qt.ItemFlag.ItemIsDragEnabled)
        self.tree.blockSignals(False)
        if parent is not None:
            parent.setExpanded(True)
        return item

    def _add_folder_item(self, name, parent=None) -> QTreeWidgetItem:
        self.tree.blockSignals(True)
        item = QTreeWidgetItem(parent or self.tree.invisibleRootItem())
        item.setText(0, name)
        item.setIcon(0, self._folder_icon)
        item.setData(0, ITEM_TYPE_ROLE, TYPE_FOLDER)
        item.setData(0, ITEM_ID_ROLE, _new_id())
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable
                      | Qt.ItemFlag.ItemIsDragEnabled | Qt.ItemFlag.ItemIsDropEnabled)
        self.tree.blockSignals(False)
        return item

    def _selected_folder(self):
        item = self.tree.currentItem()
        if item is not None and item.data(0, ITEM_TYPE_ROLE) == TYPE_FOLDER:
            return item
        return None

    def _open_sheet_tab(self, sheet_id) -> PlotSheet | None:
        if sheet_id in self.open_tabs:
            self.tab_widget.setCurrentWidget(self.open_tabs[sheet_id])
            return self.open_tabs[sheet_id]
        sm = self.sheets.get(sheet_id)
        if sm is None:
            return None
        ps = PlotSheet(sm)
        ps.active_subplot_changed.connect(self.on_subplot_selection_changed)
        ps.element_double_clicked.connect(self.open_element_editor)
        ps.element_right_clicked.connect(self._on_canvas_right_click)
        ps.notes_changed.connect(self._on_notes_edited)
        ps.image_changed.connect(self._refresh_ipc_snapshot)
        self.open_tabs[sheet_id] = ps
        idx = self.tab_widget.addTab(ps, self._sheet_icon, sm.name)
        self.tab_widget.setCurrentIndex(idx)
        ps.render(self.repository, self.images)
        return ps

    def _on_notes_edited(self):
        self.statusBar().showMessage(
            "Notes updated (saved with the project / Ctrl+S / autosave)", 2000)

    def close_tab(self, index):
        widget = self.tab_widget.widget(index)
        sheet_id = getattr(widget, "sheet_id", None)
        self.tab_widget.removeTab(index)
        self.open_tabs.pop(sheet_id, None)

    def current_sheet(self) -> PlotSheet | None:
        return self.tab_widget.currentWidget()

    def _active_sheet_model(self) -> SheetModel | None:
        cs = self.current_sheet()
        if cs is not None:
            return cs.model
        return next(iter(self.sheets.values()), None)

    def on_sheet_tab_changed(self, _index):
        if self.current_sheet():
            self._sync_grid_spinners()
            self.sync_active_subplot_inspector()
            self._sync_subplot_trace_list(keep_selection=False)
            self._sync_subplot_strip()
            self.render_current_sheet()
            self.colormap_panel.refresh()

    def on_subplot_selection_changed(self, _idx):
        # A different sheet/subplot's trace list has nothing to do with whatever row
        # was selected before -- keep_selection is index-based, so without this a
        # coincidentally-same row index in the new subplot would get carried over and
        # silently pop the inspector to "Trace Style" (see _on_subplot_trace_selection).
        self.sync_active_subplot_inspector()
        self._sync_subplot_trace_list(keep_selection=False)
        self._sync_subplot_strip()

    def on_grid_changed(self, _value=None):
        sm = self._active_sheet_model()
        if sm is None:
            return
        sm.set_grid(self.rows_spin.value(), self.cols_spin.value())
        self.sync_active_subplot_inspector()
        self._sync_subplot_trace_list(keep_selection=False)
        self._sync_subplot_strip()
        self.render_current_sheet()
        self.colormap_panel.refresh()
        self._refresh_ipc_snapshot()

    def _sync_grid_spinners(self):
        sm = self._active_sheet_model()
        if sm is None:
            return
        for spin, value in ((self.rows_spin, sm.rows), (self.cols_spin, sm.cols)):
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)

    def _on_tree_item_changed(self, item, _column):
        if item.data(0, ITEM_TYPE_ROLE) != TYPE_SHEET:
            return
        sm = self.sheets.get(item.data(0, ITEM_ID_ROLE))
        if sm and item.text(0) and item.text(0) != sm.name:
            sm.name = item.text(0)
            ps = self.open_tabs.get(sm.sheet_id)
            if ps is not None:
                self.tab_widget.setTabText(self.tab_widget.indexOf(ps), sm.name)
            self._refresh_ipc_snapshot()

    def _tree_menu(self, pos):
        item = self.tree.itemAt(pos)
        selected = self.tree.selectedItems()
        if item is not None and item in selected and len(selected) > 1:
            menu = QMenu()
            del_act = menu.addAction(f"Delete {len(selected)} selected")
            if menu.exec(self.tree.viewport().mapToGlobal(pos)) == del_act:
                self._delete_selected_tree_items()
            return
        is_sheet = item is not None and item.data(0, ITEM_TYPE_ROLE) == TYPE_SHEET
        menu = QMenu()
        new_folder_act = menu.addAction("New Folder")
        new_sheet_act = menu.addAction("New Sheet")
        tags_act = menu.addAction("Set tags...") if is_sheet else None
        rename_act = menu.addAction("Rename") if item is not None else None
        del_act = menu.addAction("Delete") if item is not None else None
        action = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if action is None:
            return
        parent = item if (item is not None and item.data(0, ITEM_TYPE_ROLE) == TYPE_FOLDER) else None
        if action == new_folder_act:
            self._add_folder_item("New Folder", parent)
        elif action == new_sheet_act:
            sm = SheetModel(name=f"Sheet {len(self.sheets) + 1}")
            self.sheets[sm.sheet_id] = sm
            self._add_sheet_tree_item(sm, parent)
            self._open_sheet_tab(sm.sheet_id)
        elif action == tags_act and is_sheet:
            self._set_sheet_tags(item)
        elif action == rename_act and item is not None:
            self.tree.editItem(item, 0)
        elif action == del_act and item is not None:
            self._delete_tree_item(item)

    def _set_sheet_tags(self, item):
        sm = self.sheets.get(item.data(0, ITEM_ID_ROLE))
        if sm is None:
            return
        text, ok = QInputDialog.getText(self, "Set sheet tags", "Comma-separated tags:",
                                        text=", ".join(sm.tags))
        if ok:
            sm.tags = [t.strip() for t in text.split(",") if t.strip()]
            item.setToolTip(0, ", ".join(sm.tags))
            self._refresh_ipc_snapshot()

    def _delete_tree_item(self, item):
        if item.data(0, ITEM_TYPE_ROLE) == TYPE_SHEET:
            self._forget_sheet(item.data(0, ITEM_ID_ROLE))
        else:
            for i in reversed(range(item.childCount())):
                self._delete_tree_item(item.child(i))
        (item.parent() or self.tree.invisibleRootItem()).removeChild(item)

    @staticmethod
    def _count_sheets_in(item) -> int:
        if item.data(0, ITEM_TYPE_ROLE) == TYPE_SHEET:
            return 1
        return sum(SciSuiteWindow._count_sheets_in(item.child(i))
                   for i in range(item.childCount()))

    def _delete_selected_tree_items(self):
        """Delete every selected folder / sheet (folders take their contents with them)."""
        selected = self.tree.selectedItems()
        sel_ids = {id(it) for it in selected}   # QTreeWidgetItem is unhashable
        chosen = []
        for it in selected:
            p = it.parent()
            while p is not None and id(p) not in sel_ids:
                p = p.parent()
            if p is None:                       # no selected ancestor -> a top-level pick
                chosen.append(it)
        if not chosen:
            return
        if len(chosen) == 1:
            self._delete_tree_item(chosen[0])
            return
        nsheets = sum(self._count_sheets_in(it) for it in chosen)
        msg = f"Delete {len(chosen)} selected item(s)"
        msg += f" and the {nsheets} sheet(s) inside?" if nsheets else "?"
        if QMessageBox.question(self, "Delete selected", msg) \
                != QMessageBox.StandardButton.Yes:
            return
        for it in chosen:
            self._delete_tree_item(it)

    def _forget_sheet(self, sheet_id):
        ps = self.open_tabs.pop(sheet_id, None)
        if ps is not None:
            self.tab_widget.removeTab(self.tab_widget.indexOf(ps))
        self.sheets.pop(sheet_id, None)
        self._refresh_ipc_snapshot()

    # ------------------------------------------------------- subplot trace list
    def _sync_subplot_trace_list(self, keep_selection=True):
        prev = ({it.data(Qt.ItemDataRole.UserRole)
                 for it in self.subplot_traces.selectedItems()}
                if keep_selection else set())
        self.subplot_traces.blockSignals(True)
        self.subplot_traces.clear()
        sm = self._active_sheet_model()
        if sm is not None:
            sub = sm.get_active_subplot()
            for i, ref in enumerate(sub.traces):
                obj = self.repository.get(ref.data_id)
                oname = obj.name if obj else "(missing)"
                x_disp = ref.x_col if ref.x_col and ref.x_col != INDEX_COL else "row index"
                item = QListWidgetItem(f"{oname}:  {ref.display_label}  vs  {x_disp}")
                item.setToolTip(f"y = {ref.y_col}   x = {x_disp}   ({oname})")
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if ref.enabled
                                   else Qt.CheckState.Unchecked)
                item.setData(Qt.ItemDataRole.UserRole, i)
                self.subplot_traces.addItem(item)
                if i in prev:
                    item.setSelected(True)
        self.subplot_traces.blockSignals(False)
        self._on_subplot_trace_selection()

    def _on_subplot_trace_toggled(self, item):
        sm = self._active_sheet_model()
        if sm is None:
            return
        sub = sm.get_active_subplot()
        i = item.data(Qt.ItemDataRole.UserRole)
        if i is None or i >= len(sub.traces):
            return
        sub.traces[i].enabled = item.checkState() == Qt.CheckState.Checked
        self.render_current_sheet()
        self._refresh_ipc_snapshot()

    def _selected_trace_refs(self) -> list:
        sm = self._active_sheet_model()
        if sm is None:
            return []
        sub = sm.get_active_subplot()
        refs = []
        for it in self.subplot_traces.selectedItems():
            i = it.data(Qt.ItemDataRole.UserRole)
            if i is not None and i < len(sub.traces):
                refs.append(sub.traces[i])
        return refs

    def _on_subplot_trace_selection(self):
        refs = self._selected_trace_refs()
        if len(refs) > 1:
            self.trace_widget.hide()
            self.bulk_widget.show()
            self.bulk_widget.set_traces(refs)
        else:
            self.bulk_widget.hide()
            self.trace_widget.show()
            self.trace_widget.set_trace(refs[0] if refs else None)
        if refs:
            self.inspector_tabs.setCurrentIndex(1)   # surface the (bulk or single) editor
        self.trace_broadcast_btn.setEnabled(len(refs) == 1)
        self.combine_btn.setEnabled(len(refs) >= 2)

    def _apply_trace_style_to_all_traces(self):
        """Copy the one selected trace's line/marker style to every other trace in
        every subplot of the active sheet (colour is left alone -- see
        _TRACE_STYLE_BROADCAST_FIELDS)."""
        refs = self._selected_trace_refs()
        if len(refs) != 1:
            return
        src = refs[0]
        sm = self._active_sheet_model()
        if sm is None:
            return
        targets = [t for sub in sm.subplots for t in sub.traces if t is not src]
        if not targets:
            return
        if QMessageBox.question(
                self, "Apply to all traces",
                f"Copy this trace's line style to the other {len(targets)} trace(s) in "
                f"\"{sm.name}\"? Colour is left alone."
        ) != QMessageBox.StandardButton.Yes:
            return
        for t in targets:
            for f in _TRACE_STYLE_BROADCAST_FIELDS:
                setattr(t, f, getattr(src, f))
        self.render_current_sheet()
        self._sync_subplot_trace_list()
        self._refresh_ipc_snapshot()

    def _remove_selected_subplot_traces(self):
        sm = self._active_sheet_model()
        if sm is None:
            return
        sub = sm.get_active_subplot()
        idxs = sorted((it.data(Qt.ItemDataRole.UserRole)
                       for it in self.subplot_traces.selectedItems()), reverse=True)
        for i in idxs:
            if i is not None and i < len(sub.traces):
                del sub.traces[i]
        self._sync_subplot_trace_list(keep_selection=False)
        self.render_current_sheet()
        self._refresh_ipc_snapshot()

    def _on_traces_reordered(self, *_):
        """Drag-drop reorder of the active subplot's trace list (one or several
        dragged together -- Qt's InternalMove handles a multi-selection natively)."""
        sm = self._active_sheet_model()
        if sm is None:
            return
        sub = sm.get_active_subplot()
        order = [self.subplot_traces.item(i).data(Qt.ItemDataRole.UserRole)
                 for i in range(self.subplot_traces.count())]
        if sorted(order) != list(range(len(sub.traces))):
            return
        old_traces = sub.traces
        moved = {id(old_traces[it.data(Qt.ItemDataRole.UserRole)])
                 for it in self.subplot_traces.selectedItems()}
        sub.traces = [old_traces[i] for i in order]
        for i, ref in enumerate(sub.traces):
            ref.color = self._color_for_index(sm, i)   # colour follows the new order
        self._sync_subplot_trace_list(keep_selection=False)
        for i in range(self.subplot_traces.count()):
            if id(sub.traces[i]) in moved:
                self.subplot_traces.item(i).setSelected(True)
        self.render_current_sheet()
        self._refresh_ipc_snapshot()

    # -------------------------------------------------------- subplot arrangement
    def _sync_subplot_strip(self):
        self.subplot_strip.blockSignals(True)
        self.subplot_strip.clear()
        sm = self._active_sheet_model()
        if sm is not None:
            for i, sub in enumerate(sm.subplots):
                item = QListWidgetItem(f"{i + 1}: {sub.title or '(untitled)'}")
                item.setData(Qt.ItemDataRole.UserRole, i)
                self.subplot_strip.addItem(item)
            if 0 <= sm.active_index < self.subplot_strip.count():
                self.subplot_strip.item(sm.active_index).setSelected(True)
        self.subplot_strip.blockSignals(False)

    def _on_strip_reordered(self, *_):
        sm = self._active_sheet_model()
        if sm is None:
            return
        order = [self.subplot_strip.item(i).data(Qt.ItemDataRole.UserRole)
                 for i in range(self.subplot_strip.count())]
        if sorted(order) != list(range(len(sm.subplots))):
            return
        active = sm.subplots[sm.active_index] if sm.subplots else None
        sm.subplots = [sm.subplots[i] for i in order]
        if active is not None and active in sm.subplots:
            sm.active_index = sm.subplots.index(active)
        self._sync_subplot_strip()
        self.render_current_sheet()
        self._refresh_ipc_snapshot()

    def _on_strip_selection(self):
        sm = self._active_sheet_model()
        rows = [it.data(Qt.ItemDataRole.UserRole)
                for it in self.subplot_strip.selectedItems()]
        if sm is None or not rows:
            return
        if sm.active_index != rows[-1]:
            sm.active_index = rows[-1]
            cs = self.current_sheet()
            if cs is not None:
                cs.canvas.draw_idle()
            self.sync_active_subplot_inspector()
            self._sync_subplot_trace_list(keep_selection=False)

    def selected_subplots(self) -> list:
        """Subplot models highlighted in the arrangement strip (for colormap scope)."""
        sm = self._active_sheet_model()
        if sm is None:
            return []
        out = []
        for it in self.subplot_strip.selectedItems():
            i = it.data(Qt.ItemDataRole.UserRole)
            if i is not None and i < len(sm.subplots):
                out.append(sm.subplots[i])
        return out

    # --------------------------------------------------------------- colormaps
    def active_sheet_model(self):
        return self._active_sheet_model()

    def project_colormaps(self):
        return self.colormaps

    def available_colormap_names(self) -> list:
        return available_specs(self._active_sheet_model(), self.colormaps)

    def resolve_colormap(self, name):
        return resolve_spec(name, self._active_sheet_model(), self.colormaps)

    def sample_colors(self, spec, n):
        return sample(spec, n)

    def copy_colormap_to_library(self, name):
        spec = self.resolve_colormap(name)
        if isinstance(spec, ColorMap) and not spec.builtin:
            self.colormaps = [c for c in self.colormaps if c.name != spec.name]
            self.colormaps.append(ColorMap(spec.name, list(spec.colors)))

    def export_colormap(self, cmap, path):
        try:
            save_file(path, cmap)
        except OSError as exc:
            QMessageBox.critical(self, "Export failed", str(exc))

    def import_colormap(self, path):
        sm = self._active_sheet_model()
        if sm is None:
            return
        try:
            cm = load_file(path)
        except (OSError, ValueError, KeyError) as exc:
            QMessageBox.critical(self, "Import failed", str(exc))
            return
        sm.colormaps = [c for c in sm.colormaps if c.name != cm.name]
        sm.colormaps.append(cm)

    def _after_colormap_applied(self):
        self.render_current_sheet()
        self._sync_subplot_trace_list()
        self.colormap_panel.refresh()
        self._refresh_ipc_snapshot()

    def _color_for_index(self, sm, i: int) -> str:
        """The colour a trace at position `i` (0-indexed) in its subplot would get
        if it were appended there -- the color cycle is positional, so this is also
        how colours get reassigned after a drag reorder (see `_on_traces_reordered`)."""
        spec = self.resolve_colormap(sm.active_colormap) if sm.active_colormap else None
        if spec is not None:
            return sample(spec, i + 1)[-1]
        return _cycle_color(i)

    def _next_trace_color(self, sm, sub) -> str:
        return self._color_for_index(sm, len(sub.traces))

    # ----------------------------------------------------------- inspector sync
    def sync_active_subplot_inspector(self):
        sm = self._active_sheet_model()
        self.axes_widget.set_subplot(sm.get_active_subplot() if sm else None)
        self.figure_widget.set_sheet(sm)

    def _after_trace_edit(self):
        self.render_current_sheet()
        self._sync_subplot_trace_list()
        self._refresh_ipc_snapshot()

    def _after_figure_edit(self):
        self.render_current_sheet()
        self._refresh_ipc_snapshot()

    def _after_axes_edit(self):
        self.render_current_sheet()

    def _apply_axes_style_to_all_subplots(self):
        """Copy the active subplot's cosmetics/grid/legend/number-format/aspect to
        every other subplot in the sheet (see _AXES_STYLE_BROADCAST_FIELDS -- title,
        axis labels, and axis limits stay per-subplot)."""
        sm = self._active_sheet_model()
        if sm is None:
            return
        src = sm.get_active_subplot()
        targets = [sub for sub in sm.subplots if sub is not src]
        if not targets:
            return
        if QMessageBox.question(
                self, "Apply to all subplots",
                f"Copy this subplot's style to the other {len(targets)} subplot(s) in "
                f"\"{sm.name}\"? Title, axis labels, and axis limits stay per-subplot."
        ) != QMessageBox.StandardButton.Yes:
            return
        for sub in targets:
            for f in _AXES_STYLE_BROADCAST_FIELDS:
                setattr(sub, f, getattr(src, f))
        self.render_current_sheet()
        self._refresh_ipc_snapshot()

    def _after_element_edit(self):
        self.render_current_sheet()
        self.sync_active_subplot_inspector()
        self._sync_subplot_trace_list()
        self._refresh_ipc_snapshot()

    def reset_zoom_current_subplot(self):
        cs = self.current_sheet()
        if cs is None or not cs.fig.axes:
            return
        sub = cs.get_active_subplot()
        sub.x_min = sub.x_max = sub.y_min = sub.y_max = None
        ax = cs.fig.axes[min(cs.active_index, len(cs.fig.axes) - 1)]
        ax.relim()
        ax.autoscale()
        cs.canvas.draw_idle()
        self.sync_active_subplot_inspector()

    # ---------------------------------------------------- double-click editors
    def open_element_editor(self, hit: dict):
        cs = self.current_sheet()
        if cs is None or not hit:
            return
        kind = hit.get("kind")
        if kind == "trace":
            ref = hit.get("ref")
            if ref is not None:
                TraceStyleDialog(ref, self._after_element_edit, self).exec()
        elif kind in ("title", "xlabel", "ylabel"):
            TextDialog(cs.model.subplots[hit["subplot_index"]], kind,
                       self._after_element_edit, self).exec()
        elif kind == "legend":
            LegendDialog(cs.model.subplots[hit["subplot_index"]],
                         self._after_element_edit, self).exec()
        elif kind == "axes":
            AxesDialog(cs.model.subplots[hit["subplot_index"]],
                       self._after_element_edit, self).exec()
        elif kind == "image":
            sub = cs.model.subplots[hit["subplot_index"]]
            if sub.image is not None:
                ImageDialog(sub, self._after_element_edit, self).exec()
        elif kind == "figure":
            FigureDialog(cs.model, self._after_element_edit, self).exec()

    def _on_canvas_right_click(self, hit: dict, gui_event):
        """Right-click a trace directly on the plot: quick delete (or edit) without
        going to the "Active subplot traces" list."""
        if not hit or hit.get("kind") != "trace":
            return
        ref = hit.get("ref")
        if ref is None:
            return
        menu = QMenu(self)
        edit_act = menu.addAction("Edit style...")
        del_act = menu.addAction("Delete trace")
        if gui_event is not None:
            try:
                pos = gui_event.globalPosition().toPoint()
            except AttributeError:
                pos = self.mapToGlobal(self.rect().center())
        else:
            pos = self.mapToGlobal(self.rect().center())
        action = menu.exec(pos)
        if action == del_act:
            self._delete_trace_ref(ref)
        elif action == edit_act:
            TraceStyleDialog(ref, self._after_element_edit, self).exec()

    def _delete_trace_ref(self, ref):
        """Remove one TraceRef, wherever it lives in the active sheet's subplots."""
        sm = self._active_sheet_model()
        if sm is None:
            return
        for sub in sm.subplots:
            if ref in sub.traces:
                sub.traces.remove(ref)
                break
        self._sync_subplot_trace_list()
        self.render_current_sheet()
        self._refresh_ipc_snapshot()

    # ------------------------------------------------------------- render engine
    def render_current_sheet(self):
        cs = self.current_sheet()
        if cs:
            cs.render(self.repository, self.images)

    def _render_sheet(self, sheet_id):
        ps = self.open_tabs.get(sheet_id)
        if ps is not None:
            ps.render(self.repository, self.images)

    def _refresh_open_sheets(self):
        for ps in self.open_tabs.values():
            ps.render(self.repository, self.images)

    # ---------------------------------------------------------- save / load / io
    def _serialize_tree(self) -> list:
        def node(item):
            if item.data(0, ITEM_TYPE_ROLE) == TYPE_SHEET:
                return {"type": "sheet", "sheet_id": item.data(0, ITEM_ID_ROLE)}
            return {"type": "folder",
                    "id": item.data(0, ITEM_ID_ROLE) or _new_id(),
                    "name": item.text(0),
                    "children": [node(item.child(i)) for i in range(item.childCount())]}
        return [node(self.tree.topLevelItem(i))
                for i in range(self.tree.topLevelItemCount())]

    def _build_tree_from_nodes(self, nodes):
        placed = set()

        def add(node_list, parent):
            for n in node_list:
                if n.get("type") == "sheet":
                    sm = self.sheets.get(n.get("sheet_id"))
                    if sm is not None:
                        self._add_sheet_tree_item(sm, parent)
                        placed.add(sm.sheet_id)
                else:
                    folder = self._add_folder_item(n.get("name", "Folder"), parent)
                    folder.setData(0, ITEM_ID_ROLE, n.get("id") or _new_id())
                    add(n.get("children", []), folder)

        add(nodes or [], self.tree.invisibleRootItem())
        for sheet_id, sm in self.sheets.items():
            if sheet_id not in placed:
                self._add_sheet_tree_item(sm)

    def _project_model(self) -> ProjectModel:
        pm = ProjectModel()
        pm.data_objects = list(self.repository.values())
        pm.images = list(self.images.values())
        pm.sheets = list(self.sheets.values())
        pm.tree = self._serialize_tree()
        pm.colormaps = list(self.colormaps)
        cs = self.current_sheet()
        pm.active_sheet_id = cs.sheet_id if cs else next(iter(self.sheets), "")
        return pm

    def _load_project_model(self, pm: ProjectModel):
        self.repository.clear()
        self.images.clear()
        self.sheets.clear()
        self.open_tabs.clear()
        self.colormaps = list(pm.colormaps)
        while self.tab_widget.count():
            self.tab_widget.removeTab(0)
        self.tree.blockSignals(True)
        self.tree.clear()
        self.tree.blockSignals(False)

        for obj in pm.data_objects:
            self.repository[obj.id] = obj
        for img in pm.images:
            self.images[img.id] = img
        for sm in pm.sheets:
            self.sheets[sm.sheet_id] = sm
        self._build_tree_from_nodes(pm.tree)
        self._refresh_data_list()

        if not self.sheets:
            self.create_new_sheet("Sheet 1")
        else:
            target = pm.active_sheet_id if pm.active_sheet_id in self.sheets \
                else next(iter(self.sheets))
            self._open_sheet_tab(target)
        self._sync_grid_spinners()
        self.sync_active_subplot_inspector()
        self._sync_subplot_trace_list()
        self._sync_subplot_strip()
        self.colormap_panel.refresh()
        self._refresh_open_sheets()
        self._refresh_ipc_data()
        self._refresh_ipc_snapshot()

    def _write_project(self, path: str) -> bool:
        try:
            self._project_model().save(path)
            return True
        except OSError as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return False

    def _quick_save(self):
        """Ctrl+S: save to the current project file, or fall back to Save As."""
        if not self.project_path:
            self.save_project_file()
            return
        if self._write_project(self.project_path):
            self.statusBar().showMessage(
                f"Saved {self._project_name()}  -  {time.strftime('%H:%M:%S')}", 4000)

    def _periodic_autosave(self):
        """Timer tick (every ``_AUTOSAVE_SECONDS``): always refresh the crash-recovery
        session file (``~/.scisuite_session.json``) -- previously that only happened on
        a clean window close, so a crash or a killed process lost everything since the
        last close -- and additionally re-save the named project file, if any."""
        self.save_session()
        if self.project_path:
            self._autosave_project()

    def _autosave_project(self):
        if not self.project_path:
            return
        try:
            self._project_model().save(self.project_path)
            self.statusBar().showMessage(
                f"Autosaved  -  {time.strftime('%H:%M:%S')}", 3000)
        except OSError as exc:
            self.statusBar().showMessage(f"Autosave failed: {exc}", 6000)

    def save_project_file(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Project As", self.project_path or "",
            "NOORSUITE Project (*.sciproj)")
        if not path:
            return
        if not path.lower().endswith(".sciproj"):
            path += ".sciproj"
        if self._write_project(path):
            self.project_path = path
            self._update_title()
            self.statusBar().showMessage(f"Saved {self._project_name()}", 4000)

    def load_project_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Project", "", "NOORSUITE Project (*.sciproj)")
        if not path:
            return
        try:
            pm = ProjectModel.load(path)
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            QMessageBox.critical(self, "Cannot open project", str(exc))
            return
        self._load_project_model(pm)
        self.project_path = path
        self._update_title()
        self.statusBar().showMessage(f"Opened {self._project_name()}", 4000)

    def save_session(self):
        try:
            self._project_model().save(self.session_file)
        except OSError:
            pass

    def load_session(self):
        if not os.path.exists(self.session_file):
            return
        try:
            pm = ProjectModel.load(self.session_file)
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            return
        self._load_project_model(pm)

    @staticmethod
    def _export_figsize_in(sm) -> "tuple[float, float] | None":
        """Explicit (width_in, height_in) for exporting `sm`'s figure, from its
        cm size in the Figure dialog -- or None to keep the current on-screen size."""
        if sm is not None and sm.fig_width_cm and sm.fig_height_cm:
            return sm.fig_width_cm / 2.54, sm.fig_height_cm / 2.54
        return None

    def _savefig_at_export_size(self, cs, dest, fmt: str):
        """savefig(dest, format=fmt) at the sheet's configured export size (or the
        current on-screen size, trimmed, if none is set); restores the figure after."""
        size = self._export_figsize_in(cs.model)
        old = cs.fig.get_size_inches().copy() if size else None
        if size:
            cs.fig.set_size_inches(*size)
        try:
            cs.fig.savefig(dest, format=fmt, dpi=300, bbox_inches=None if size else "tight",
                           facecolor=cs.fig.get_facecolor(),
                           edgecolor=cs.fig.patch.get_edgecolor())
        finally:
            if size:
                cs.fig.set_size_inches(*old)
                cs.canvas.draw_idle()

    def copy_plot_to_clipboard(self):
        cs = self.current_sheet()
        if cs is None:
            return
        buf = BytesIO()
        self._savefig_at_export_size(cs, buf, "png")
        QApplication.clipboard().setImage(QImage.fromData(buf.getvalue()))
        QMessageBox.information(self, "Clipboard", "Active sheet copied to clipboard (300 DPI).")

    def export_svg(self):
        cs = self.current_sheet()
        if cs is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export Sheet as SVG", "", "SVG Files (*.svg)")
        if path:
            self._savefig_at_export_size(cs, path, "svg")
            QMessageBox.information(self, "Export Successful", f"Saved: {path}")

    def closeEvent(self, event):
        self._autosave_timer.stop()
        self.save_session()
        if self.project_path:
            self._autosave_project()
        self.ipc.stop()
        event.accept()

    # ---------------------------------------------------------------- IPC intake
    def _sheet_haystack(self, sm: SheetModel) -> str:
        parts = [sm.name, " ".join(sm.tags), sm.notes]
        seen = set()
        for sub in sm.subplots:
            parts += [sub.title, sub.x_label, sub.y_label]
            for t in sub.traces:
                parts.append(t.label or t.y_col)
                obj = self.repository.get(t.data_id)
                if obj and obj.id not in seen:
                    seen.add(obj.id)
                    parts.append(obj.name)
            if sub.image is not None:
                img = self.images.get(sub.image.data_id)
                if img is not None:
                    parts.append(img.name)
        return " ".join(p for p in parts if p)

    def _filter_data_pool(self, text):
        query = text.strip()
        for i in range(self.data_list.count()):
            item = self.data_list.item(i)
            obj = self.repository.get(item.data(Qt.ItemDataRole.UserRole))
            hay = item.text() + " " + (" ".join(obj.tags) + " " + " ".join(obj.column_order)
                                       if obj else "")
            item.setHidden(not fuzzy_match(query, hay))

    def _filter_project_tree(self, text):
        query = text.strip()

        def recurse(item):
            child_match = any(recurse(item.child(i)) for i in range(item.childCount()))
            if item.data(0, ITEM_TYPE_ROLE) == TYPE_SHEET:
                sm = self.sheets.get(item.data(0, ITEM_ID_ROLE))
                hay = self._sheet_haystack(sm) if sm else item.text(0)
            else:
                hay = item.text(0)
            visible = fuzzy_match(query, hay) or child_match
            item.setHidden(bool(query) and not visible)
            if visible and query:
                item.setExpanded(True)
            return visible

        for i in range(self.tree.topLevelItemCount()):
            recurse(self.tree.topLevelItem(i))

    def _refresh_ipc_data(self):
        """Rebuild full column + image data (client get_data / get_image). Data mutations only."""
        self._ipc_data_full = {
            o.id: {"id": o.id, "name": o.name, "column_order": list(o.column_order),
                   "columns": {c: o.columns[c].tolist() for c in o.column_order}}
            for o in self.repository.values()
        }
        self._ipc_image_full = {
            im.id: {"id": im.id, "name": im.name, "shape": list(im.shape),
                    "dtype": str(im.data.dtype), "axis_names": list(im.axis_names),
                    "bytes": np.ascontiguousarray(im.data).tobytes()}
            for im in self.images.values()
        }
        if isinstance(self.ipc.snapshot, dict):
            self.ipc.snapshot["data_full"] = self._ipc_data_full
            self.ipc.snapshot["image_full"] = self._ipc_image_full

    def _refresh_ipc_snapshot(self):
        data_objects = [{
            "id": o.id, "name": o.name, "columns": list(o.column_order),
            "nrows": o.nrows, "tags": list(o.tags), "source": o.source,
        } for o in self.repository.values()]
        images = [{
            "id": im.id, "name": im.name, "shape": list(im.shape),
            "dtype": str(im.data.dtype), "axis_names": list(im.axis_names),
            "tags": list(im.tags), "source": im.source,
        } for im in self.images.values()]
        traces = []
        for sm in self.sheets.values():
            for i, sub in enumerate(sm.subplots):
                for t in sub.traces:
                    obj = self.repository.get(t.data_id)
                    traces.append({
                        "data_id": t.data_id,
                        "data_name": obj.name if obj else "",
                        "y_col": t.y_col, "x_col": t.x_col,
                        "sheet": sm.name, "subplot": i, "enabled": t.enabled,
                        "plot_type": t.plot_type, "color": t.color,
                    })
        name_counts: dict[str, int] = {}
        for sm in self.sheets.values():
            name_counts[sm.name] = name_counts.get(sm.name, 0) + 1
        sheets = [{
            "id": sm.sheet_id, "name": sm.name, "rows": sm.rows, "cols": sm.cols,
            "subplots": len(sm.subplots), "tags": list(sm.tags),
            "duplicate_name": name_counts[sm.name] > 1,
        } for sm in self.sheets.values()]
        self.ipc.snapshot = {"data_objects": data_objects, "images": images,
                             "traces": traces, "sheets": sheets,
                             "data_full": self._ipc_data_full,
                             "image_full": self._ipc_image_full}

    def _find_data(self, key):
        if key in self.repository:
            return self.repository[key]
        return next((o for o in self.repository.values() if o.name == key), None)

    def _find_image(self, key):
        if key in self.images:
            return self.images[key]
        return next((o for o in self.images.values() if o.name == key), None)

    def handle_incoming_ipc(self, payload: dict):
        action = payload.get("action")
        if action == ACTION_APPEND_DATAFRAME:
            self._ingest_dataobject(payload)
        elif action == ACTION_APPEND_TRACE:
            self._ingest_pushed_trace(payload)
        elif action == ACTION_APPEND_IMAGE:
            self._ingest_image(payload)
        elif action == ACTION_ADD_TO_SHEET:
            self._handle_add_to_sheet(payload)
        elif action == ACTION_ADD_IMAGE_TO_SHEET:
            self._handle_add_image_to_sheet(payload)
        elif action in (ACTION_REMOVE_DATA, ACTION_REMOVE_TRACE):
            self._remove_data(payload.get("key"))
        elif action == ACTION_CLEAR:
            self._clear_all()

    def _ingest_dataobject(self, payload: dict) -> DataObject:
        name = payload.get("name", "data")
        columns = payload.get("columns", {})
        order = payload.get("column_order") or list(columns)
        existing = None
        if payload.get("mode") == "update":
            existing = next((o for o in self.repository.values() if o.name == name), None)
        if existing is not None:
            existing.update_from(columns, payload.get("units"), order)
            obj = existing
        else:
            obj = DataObject(name, columns, order, payload.get("units"),
                             payload.get("tags"), payload.get("source", "dataframe"))
            self.repository[obj.id] = obj
        self._refresh_data_list()
        self._refresh_open_sheets()
        self._sync_subplot_trace_list()
        self._refresh_ipc_data()
        self._refresh_ipc_snapshot()
        return obj

    def _ingest_image(self, payload: dict) -> ImageObject:
        name = payload.get("name", "image")
        arr = np.frombuffer(payload["bytes"], dtype=payload["dtype"]).reshape(
            payload["shape"])
        axis_names = payload.get("axis_names")
        existing = None
        if payload.get("mode") == "update":
            existing = self._find_image(name)
        if existing is not None:
            existing.update_from(arr, axis_names)
            obj = existing
        else:
            obj = ImageObject(name, arr, axis_names, payload.get("axis_units"),
                              payload.get("tags"), payload.get("source", "array"))
            self.images[obj.id] = obj
        self._refresh_data_list()
        self._refresh_open_sheets()
        self._refresh_ipc_data()
        self._refresh_ipc_snapshot()
        return obj

    def _add_image_to_subplot(self, image_id, sheet_id, subplot_index, display_axes):
        obj = self.images.get(image_id)
        sm = self.sheets.get(sheet_id)
        if obj is None or sm is None:
            return
        si = min(int(subplot_index), len(sm.subplots) - 1)
        sub = sm.subplots[si]
        r, c = int(display_axes[0]), int(display_axes[1])
        index = {a: obj.shape[a] // 2 for a in range(obj.ndim) if a not in (r, c)}
        sub.image = ImageRef(obj.id, [r, c], index)
        sm.active_index = si
        self._render_sheet(sheet_id)
        self.sync_active_subplot_inspector()
        self._refresh_ipc_snapshot()

    def _add_image_from_panel(self, sel: dict, target: str):
        if target == "new":
            sm = self._create_sheet_model()
        else:
            sm = self._active_sheet_model() or self._create_sheet_model()
        self._open_sheet_tab(sm.sheet_id)
        self._add_image_to_subplot(sel["image_id"], sm.sheet_id, sm.active_index,
                                   sel["display_axes"])

    def _handle_add_image_to_sheet(self, payload: dict):
        obj = self._find_image(payload.get("name") or payload.get("id"))
        if obj is None:
            return
        target = payload.get("sheet", "__active__")
        if target == "__new__":
            sm = self._create_sheet_model()
        elif target in ("__active__", "", None):
            sm = self._active_sheet_model() or self._create_sheet_model()
        else:
            sm = self.sheets.get(target) or next(
                (s for s in self.sheets.values() if s.name == target), None)
            if sm is None:
                sm = self._create_sheet_model(target if isinstance(target, str) else None)
        self._open_sheet_tab(sm.sheet_id)
        sub_idx = min(int(payload.get("subplot_index", 0)), len(sm.subplots) - 1)
        axes = payload.get("display_axes") or [max(0, obj.ndim - 2), max(0, obj.ndim - 1)]
        self._add_image_to_subplot(obj.id, sm.sheet_id, sub_idx, axes)

    def _sync_image_sidecars(self):
        """Make the project's ``<stem>/`` folder hold exactly one ``.npy`` per current
        image (named after it); anything left by a delete or rename is removed."""
        if not self.project_path:
            return
        assets = ProjectModel._assets_dir(self.project_path)
        keep = set(resolve_sidecar_names(self.images.values()).values())
        ProjectModel._sweep_assets(assets, keep)

    def _remove_image(self, key):
        obj = self._find_image(key)
        if obj is None:
            return
        self.images.pop(obj.id, None)
        self._sync_image_sidecars()
        for sm in self.sheets.values():
            for sub in sm.subplots:
                if sub.image is not None and sub.image.data_id == obj.id:
                    sub.image = None
        self._refresh_data_list()
        self._refresh_open_sheets()
        self.sync_active_subplot_inspector()
        self._refresh_ipc_data()
        self._refresh_ipc_snapshot()

    def _ingest_pushed_trace(self, payload: dict):
        name = payload["name"]
        obj = self._ingest_dataobject({
            "name": name, "mode": "new",
            "columns": {"x": payload["x"], name: payload["y"]},
            "column_order": ["x", name],
            "units": {"x": payload.get("x_unit", ""), name: payload.get("y_unit", "")},
            "tags": payload.get("tags", []), "source": "push_trace",
        })
        sm = self._active_sheet_model() or self._create_sheet_model()
        self._open_sheet_tab(sm.sheet_id)
        sub = sm.get_active_subplot()
        ref = TraceRef(obj.id, "x", name)
        ref.color = payload.get("color") or self._next_trace_color(sm, sub)
        if payload.get("plot_type"):
            ref.plot_type = payload["plot_type"]
        sub.traces.append(ref)
        self._render_sheet(sm.sheet_id)
        self._sync_subplot_trace_list()
        self._refresh_ipc_snapshot()

    def _handle_add_to_sheet(self, payload: dict):
        # ACTION_ADD_TO_SHEET (and every mutation) is fire-and-forget over IPC -- the
        # Jupyter side always gets {"status": "success"} back the instant the socket
        # receives it, before this GUI-thread handler even runs, so it has no way to
        # learn about a lookup miss below. Surface it here instead, so it's at least
        # visible in the GUI rather than a silent no-op ("I entered the sheet name
        # but it did not work").
        key = payload.get("data_name") or payload.get("data_id")
        obj = self._find_data(key)
        if obj is None:
            self.statusBar().showMessage(
                f"plot(): no data object named/id {key!r} -- nothing added "
                "(check suite.list_data())", 8000)
            return
        target = payload.get("sheet", "__active__")
        if target == "__new__":
            sm = self._create_sheet_model()
        elif target in ("__active__", "", None):
            sm = self._active_sheet_model() or self._create_sheet_model()
        else:
            matches = [s for s in self.sheets.values() if s.name == target]
            sm = self.sheets.get(target) or (matches[0] if matches else None)
            if sm is None:
                sm = self._create_sheet_model(target if isinstance(target, str) else None)
            elif len(matches) > 1:
                self.statusBar().showMessage(
                    f"plot(): {len(matches)} sheets are named {target!r} -- used the "
                    f"first (id {sm.sheet_id}); target sheet=<id> to be exact", 8000)
        self._open_sheet_tab(sm.sheet_id)
        requested = int(payload.get("subplot_index", 0))
        sub_idx = min(requested, len(sm.subplots) - 1)
        if requested != sub_idx:
            self.statusBar().showMessage(
                f"plot(): subplot {requested} doesn't exist on '{sm.name}' "
                f"({len(sm.subplots)} subplot(s)) -- used subplot {sub_idx} instead", 8000)
        sel = {"data_id": obj.id,
               "x_col": payload.get("x_col", INDEX_COL) or INDEX_COL,
               "y_cols": list(payload.get("y_cols", []))}
        self._add_traces(sel, sm.sheet_id, sub_idx)

    def _remove_data(self, key):
        if self._find_image(key) is not None:
            self._remove_image(key)
            return
        obj = self._find_data(key)
        if obj is None:
            return
        self.repository.pop(obj.id, None)
        for sm in self.sheets.values():
            for sub in sm.subplots:
                sub.traces = [t for t in sub.traces if t.data_id != obj.id]
        self._refresh_data_list()
        self._refresh_open_sheets()
        self._sync_subplot_trace_list()
        self._refresh_ipc_data()
        self._refresh_ipc_snapshot()

    def _clear_all(self):
        self.repository.clear()
        self.images.clear()
        self._sync_image_sidecars()
        for sm in self.sheets.values():
            for sub in sm.subplots:
                sub.traces = []
                sub.image = None
        self._refresh_data_list()
        self._refresh_open_sheets()
        self._sync_subplot_trace_list()
        self._refresh_ipc_data()
        self._refresh_ipc_snapshot()


def _in_scroll(widget: QWidget) -> QWidget:
    from PyQt6.QtWidgets import QScrollArea
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(widget)
    return area


# Backwards-compatible alias.
ModernOriginSuite = SciSuiteWindow
