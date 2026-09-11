"""Reusable style-editing widgets and the double-click editor dialogs.

:class:`TraceStyleWidget` and :class:`AxesStyleWidget` are the single source of truth
for the style controls -- they are embedded both in the main window's right-hand
inspector and in the pop-up dialogs opened by double-clicking the canvas, so the two
can never drift apart. Dialogs put their content inside a scroll area with a capped
height so they always fit small screens.
"""
from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QButtonGroup, QCheckBox, QColorDialog, QComboBox,
                             QDialog, QDialogButtonBox, QDoubleSpinBox,
                             QFileDialog, QFormLayout, QHBoxLayout, QInputDialog,
                             QLabel, QLineEdit, QMessageBox, QPushButton,
                             QRadioButton, QScrollArea, QSpinBox, QVBoxLayout,
                             QWidget)

from .model import (LEGEND_LOCS, LINE_STYLE_LABELS, LINE_STYLES, MARKER_LABELS,
                    MARKERS, PLOT_TYPES, Y_TRANSFORM_LABELS, Y_TRANSFORMS,
                    ColorMap, apply_numeric_expr)
from .widgets import CollapsibleSection

_LINESTYLE_ITEMS = ["-", "--", "-.", ":"]
_KEEP = "(keep)"
# apply_numeric_expr is imported from model.py above and re-exported here for callers.


# --------------------------------------------------------------------------- utils
def _parse_float_or_none(text: str):
    text = text.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _fmt(value) -> str:
    return "" if value is None else f"{value:g}"


def _tighten(form: QFormLayout):
    form.setContentsMargins(6, 6, 6, 6)
    form.setVerticalSpacing(4)
    form.setHorizontalSpacing(6)


class ColorButton(QPushButton):
    """A button that shows its colour and opens a colour picker when clicked."""

    colorChanged = pyqtSignal(str)

    def __init__(self, color="#1f77b4", label="Choose colour", parent=None):
        super().__init__(label, parent)
        self._label = label
        self._color = color
        self.clicked.connect(self._pick)
        self._refresh()

    def color(self) -> str:
        return self._color

    def setColor(self, color: str):
        if color and color != self._color:
            self._color = color
            self._refresh()

    def _pick(self):
        chosen = QColorDialog.getColor(QColor(self._color), self, self._label)
        if chosen.isValid():
            self._color = chosen.name()
            self._refresh()
            self.colorChanged.emit(self._color)

    def _refresh(self):
        self.setText(f"{self._label}  ({self._color})")
        self.setStyleSheet(
            f"background-color: {self._color}; color: white; font-weight: bold; padding: 3px;"
        )


# ------------------------------------------------------------------- trace styling
class TraceStyleWidget(QWidget):
    """Bind to a :class:`~NoorSuite.model.TraceRef` and edit its visual properties."""

    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ref = None
        self._loading = False

        form = QFormLayout(self)
        _tighten(form)

        self.source_label = QLineEdit()
        self.source_label.setReadOnly(True)
        form.addRow("Source:", self.source_label)

        self.label_edit = QLineEdit()
        self.label_edit.setPlaceholderText("(defaults to column name)")
        self.label_edit.textEdited.connect(self._push)
        form.addRow("Legend label:", self.label_edit)

        self.enabled_check = QCheckBox("Shown on plot")
        self.enabled_check.toggled.connect(self._push)
        form.addRow("", self.enabled_check)

        self.type_combo = QComboBox()
        self.type_combo.addItems(PLOT_TYPES)
        self.type_combo.currentTextChanged.connect(self._push)
        form.addRow("Plot type:", self.type_combo)

        self.color_btn = ColorButton(label="Line / face colour")
        self.color_btn.colorChanged.connect(lambda *_: self._push())
        form.addRow("Colour:", self.color_btn)

        self.edge_btn = ColorButton(color="#000000", label="Edge colour")
        self.edge_btn.colorChanged.connect(lambda *_: self._push())
        form.addRow("Edge colour:", self.edge_btn)

        self.style_combo = QComboBox()
        self.style_combo.addItems(LINE_STYLE_LABELS)
        self.style_combo.currentIndexChanged.connect(self._push)
        form.addRow("Line style:", self.style_combo)

        self.width_spin = QDoubleSpinBox()
        self.width_spin.setRange(0.2, 20.0)
        self.width_spin.setSingleStep(0.2)
        self.width_spin.valueChanged.connect(self._push)
        form.addRow("Line width:", self.width_spin)

        self.marker_combo = QComboBox()
        self.marker_combo.addItems(MARKER_LABELS)
        self.marker_combo.currentIndexChanged.connect(self._push)
        form.addRow("Marker:", self.marker_combo)

        self.marker_size_spin = QDoubleSpinBox()
        self.marker_size_spin.setRange(1.0, 50.0)
        self.marker_size_spin.valueChanged.connect(self._push)
        form.addRow("Marker size:", self.marker_size_spin)

        self.alpha_spin = QDoubleSpinBox()
        self.alpha_spin.setRange(0.05, 1.0)
        self.alpha_spin.setSingleStep(0.05)
        self.alpha_spin.valueChanged.connect(self._push)
        form.addRow("Opacity (alpha):", self.alpha_spin)

        self.transform_combo = QComboBox()
        self.transform_combo.addItems(Y_TRANSFORM_LABELS)
        self.transform_combo.currentIndexChanged.connect(self._push)
        form.addRow("Y transform:", self.transform_combo)

    def set_trace(self, ref):
        self._ref = ref
        self._loading = True
        try:
            if ref is None:
                self.setEnabled(False)
                return
            self.setEnabled(True)
            x_disp = ref.x_col or "row index"
            self.source_label.setText(f"{ref.y_col}  vs  {x_disp}")
            self.label_edit.setText(ref.label)
            self.enabled_check.setChecked(ref.enabled)
            self.type_combo.setCurrentText(ref.plot_type)
            self.color_btn.setColor(ref.color)
            self.edge_btn.setColor(ref.edge_color)
            self.style_combo.setCurrentIndex(
                LINE_STYLES.index(ref.line_style) if ref.line_style in LINE_STYLES else 0)
            self.width_spin.setValue(ref.line_width)
            self.marker_combo.setCurrentIndex(
                MARKERS.index(ref.marker) if ref.marker in MARKERS else 0)
            self.marker_size_spin.setValue(ref.marker_size)
            self.alpha_spin.setValue(ref.alpha)
            self.transform_combo.setCurrentIndex(
                Y_TRANSFORMS.index(ref.scale_factor)
                if ref.scale_factor in Y_TRANSFORMS else 0)
        finally:
            self._loading = False

    def _push(self, *_):
        if self._loading or self._ref is None:
            return
        r = self._ref
        r.label = self.label_edit.text()
        r.enabled = self.enabled_check.isChecked()
        r.plot_type = self.type_combo.currentText()
        r.color = self.color_btn.color()
        r.edge_color = self.edge_btn.color()
        r.line_style = LINE_STYLES[self.style_combo.currentIndex()]
        r.line_width = self.width_spin.value()
        r.marker = MARKERS[self.marker_combo.currentIndex()]
        r.marker_size = self.marker_size_spin.value()
        r.alpha = self.alpha_spin.value()
        r.scale_factor = Y_TRANSFORMS[self.transform_combo.currentIndex()]
        self.changed.emit()


# -------------------------------------------------------------------- axes styling
class AxesStyleWidget(QWidget):
    """Bind to a :class:`~NoorSuite.model.SubplotModel` and edit the panel."""

    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sub = None
        self._loading = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.setSpacing(4)

        labels_body = QWidget()
        form = QFormLayout(labels_body)
        _tighten(form)
        self.title_edit = QLineEdit(); self.title_edit.textEdited.connect(self._push)
        form.addRow("Title:", self.title_edit)
        self.xlabel_edit = QLineEdit(); self.xlabel_edit.textEdited.connect(self._push)
        form.addRow("X label:", self.xlabel_edit)
        self.ylabel_edit = QLineEdit(); self.ylabel_edit.textEdited.connect(self._push)
        form.addRow("Y label:", self.ylabel_edit)
        self.xscale_combo = QComboBox(); self.xscale_combo.addItems(["linear", "log"])
        self.xscale_combo.currentTextChanged.connect(self._push)
        form.addRow("X scale:", self.xscale_combo)
        self.yscale_combo = QComboBox(); self.yscale_combo.addItems(["linear", "log"])
        self.yscale_combo.currentTextChanged.connect(self._push)
        form.addRow("Y scale:", self.yscale_combo)
        self.grid_check = QCheckBox("Show grid lines")
        self.grid_check.toggled.connect(self._push)
        form.addRow("", self.grid_check)
        outer.addWidget(CollapsibleSection("Labels & scale", labels_body, expanded=True))

        limits_body = QWidget()
        lform = QFormLayout(limits_body)
        _tighten(lform)
        self.xmin_edit = QLineEdit(); self.xmin_edit.editingFinished.connect(self._push)
        self.xmax_edit = QLineEdit(); self.xmax_edit.editingFinished.connect(self._push)
        self.ymin_edit = QLineEdit(); self.ymin_edit.editingFinished.connect(self._push)
        self.ymax_edit = QLineEdit(); self.ymax_edit.editingFinished.connect(self._push)
        lform.addRow("X min / max:", _pair(self.xmin_edit, self.xmax_edit))
        lform.addRow("Y min / max:", _pair(self.ymin_edit, self.ymax_edit))
        outer.addWidget(CollapsibleSection("Axis limits (blank = auto)", limits_body,
                                           expanded=True))

        cosmetic_body = QWidget()
        cform = QFormLayout(cosmetic_body)
        _tighten(cform)
        self.tick_spin = _spin(2.0, 40.0); self.tick_spin.valueChanged.connect(self._push)
        cform.addRow("Tick label size:", self.tick_spin)
        self.title_fs_spin = _spin(4.0, 48.0); self.title_fs_spin.valueChanged.connect(self._push)
        cform.addRow("Title font size:", self.title_fs_spin)
        self.label_fs_spin = _spin(4.0, 48.0); self.label_fs_spin.valueChanged.connect(self._push)
        cform.addRow("Axis label font size:", self.label_fs_spin)
        self.face_btn = ColorButton(color="#ffffff", label="Background")
        self.face_btn.colorChanged.connect(lambda *_: self._push())
        cform.addRow("Background:", self.face_btn)
        self.face_alpha_spin = _spin(0.0, 1.0, 0.05)
        self.face_alpha_spin.valueChanged.connect(self._push)
        cform.addRow("Background alpha:", self.face_alpha_spin)
        self.spine_btn = ColorButton(color="#000000", label="Spines")
        self.spine_btn.colorChanged.connect(lambda *_: self._push())
        cform.addRow("Spine colour:", self.spine_btn)
        self.spine_width_spin = _spin(0.0, 12.0, 0.2)
        self.spine_width_spin.valueChanged.connect(self._push)
        cform.addRow("Spine width:", self.spine_width_spin)
        self.spine_style_combo = QComboBox(); self.spine_style_combo.addItems(_LINESTYLE_ITEMS)
        self.spine_style_combo.currentTextChanged.connect(self._push)
        cform.addRow("Spine style:", self.spine_style_combo)
        self.aspect_combo = QComboBox()
        self.aspect_combo.addItem("Auto", "auto")
        self.aspect_combo.addItem("Equal (1:1)", "equal")
        self.aspect_combo.addItem("Custom ratio...", "custom")
        self.aspect_combo.currentIndexChanged.connect(self._on_aspect_mode)
        cform.addRow("Aspect ratio:", self.aspect_combo)
        self.aspect_ratio_spin = _spin(0.001, 1000.0, 0.1)
        self.aspect_ratio_spin.setDecimals(3)
        self.aspect_ratio_spin.setToolTip("Data units of Y displayed per one data unit of X")
        self.aspect_ratio_spin.valueChanged.connect(self._push)
        cform.addRow("  Y : X ratio:", self.aspect_ratio_spin)
        outer.addWidget(CollapsibleSection("Cosmetics", cosmetic_body, expanded=False))

        legend_body = QWidget()
        gform = QFormLayout(legend_body)
        _tighten(gform)
        self.legend_check = QCheckBox("Show legend")
        self.legend_check.toggled.connect(self._push)
        gform.addRow("", self.legend_check)
        self.legend_loc_combo = QComboBox(); self.legend_loc_combo.addItems(LEGEND_LOCS)
        self.legend_loc_combo.currentTextChanged.connect(self._push)
        gform.addRow("Location:", self.legend_loc_combo)
        self.legend_frame_check = QCheckBox("Frame")
        self.legend_frame_check.toggled.connect(self._push)
        gform.addRow("", self.legend_frame_check)
        self.legend_fs_spin = _spin(4.0, 32.0); self.legend_fs_spin.valueChanged.connect(self._push)
        gform.addRow("Font size:", self.legend_fs_spin)
        self.legend_ncol_spin = QSpinBox(); self.legend_ncol_spin.setRange(1, 8)
        self.legend_ncol_spin.valueChanged.connect(self._push)
        gform.addRow("Columns:", self.legend_ncol_spin)
        outer.addWidget(CollapsibleSection("Legend", legend_body, expanded=False))

        grid_body = QWidget()
        grform = QFormLayout(grid_body)
        _tighten(grform)
        self.grid_axis_combo = QComboBox(); self.grid_axis_combo.addItems(["both", "x", "y"])
        self.grid_axis_combo.currentTextChanged.connect(self._push)
        grform.addRow("Axes:", self.grid_axis_combo)
        self.grid_ticks_combo = QComboBox(); self.grid_ticks_combo.addItems(["major", "minor", "both"])
        self.grid_ticks_combo.currentTextChanged.connect(self._push)
        grform.addRow("Ticks:", self.grid_ticks_combo)
        self.grid_style_combo = QComboBox(); self.grid_style_combo.addItems(_LINESTYLE_ITEMS)
        self.grid_style_combo.currentTextChanged.connect(self._push)
        grform.addRow("Line style:", self.grid_style_combo)
        self.grid_width_spin = _spin(0.1, 8.0, 0.1)
        self.grid_width_spin.valueChanged.connect(self._push)
        grform.addRow("Line width:", self.grid_width_spin)
        self.grid_color_btn = ColorButton(color="#b0b0b0", label="Grid colour")
        self.grid_color_btn.colorChanged.connect(lambda *_: self._push())
        grform.addRow("Colour:", self.grid_color_btn)
        self.grid_alpha_spin = _spin(0.0, 1.0, 0.05)
        self.grid_alpha_spin.valueChanged.connect(self._push)
        grform.addRow("Alpha:", self.grid_alpha_spin)
        outer.addWidget(CollapsibleSection("Grid", grid_body, expanded=False))

        self.image_widget = ImageStyleWidget()
        self.image_widget.changed.connect(lambda: self.changed.emit())
        self._image_section = CollapsibleSection("Image", self.image_widget, expanded=True)
        outer.addWidget(self._image_section)

        outer.addStretch(1)

    def set_subplot(self, sub):
        self._sub = sub
        self._loading = True
        try:
            if sub is None:
                self.setEnabled(False)
                return
            self.setEnabled(True)
            self.title_edit.setText(sub.title)
            self.xlabel_edit.setText(sub.x_label)
            self.ylabel_edit.setText(sub.y_label)
            self.xscale_combo.setCurrentText(sub.x_scale)
            self.yscale_combo.setCurrentText(sub.y_scale)
            self.grid_check.setChecked(sub.show_grid)
            self.xmin_edit.setText(_fmt(sub.x_min))
            self.xmax_edit.setText(_fmt(sub.x_max))
            self.ymin_edit.setText(_fmt(sub.y_min))
            self.ymax_edit.setText(_fmt(sub.y_max))
            self.tick_spin.setValue(sub.tick_label_size)
            self.title_fs_spin.setValue(sub.title_fontsize)
            self.label_fs_spin.setValue(sub.xlabel_fontsize)
            self.face_btn.setColor(sub.face_color)
            self.face_alpha_spin.setValue(sub.face_alpha)
            self.spine_btn.setColor(sub.spine_color)
            self.spine_width_spin.setValue(sub.spine_width)
            self.spine_style_combo.setCurrentText(sub.spine_style)
            i = self.aspect_combo.findData(sub.aspect)
            self.aspect_combo.setCurrentIndex(i if i >= 0 else 0)
            self.aspect_ratio_spin.setValue(sub.aspect_ratio)
            self.aspect_ratio_spin.setEnabled(sub.aspect == "custom")
            self.legend_check.setChecked(sub.legend_visible)
            self.legend_loc_combo.setCurrentText(sub.legend_loc)
            self.legend_frame_check.setChecked(sub.legend_frame)
            self.legend_fs_spin.setValue(sub.legend_fontsize)
            self.legend_ncol_spin.setValue(int(sub.legend_ncol))
            self.grid_axis_combo.setCurrentText(sub.grid_axis)
            self.grid_ticks_combo.setCurrentText(sub.grid_ticks)
            self.grid_style_combo.setCurrentText(sub.grid_style)
            self.grid_width_spin.setValue(sub.grid_width)
            self.grid_color_btn.setColor(sub.grid_color)
            self.grid_alpha_spin.setValue(sub.grid_alpha)
        finally:
            self._loading = False
        self._image_section.setVisible(sub.image is not None)
        self.image_widget.set_subplot(sub)

    def _on_aspect_mode(self, _i):
        self.aspect_ratio_spin.setEnabled(self.aspect_combo.currentData() == "custom")
        self._push()

    def _push(self, *_):
        if self._loading or self._sub is None:
            return
        s = self._sub
        s.title = self.title_edit.text()
        s.x_label = self.xlabel_edit.text()
        s.y_label = self.ylabel_edit.text()
        s.x_scale = self.xscale_combo.currentText()
        s.y_scale = self.yscale_combo.currentText()
        s.show_grid = self.grid_check.isChecked()
        s.x_min = _parse_float_or_none(self.xmin_edit.text())
        s.x_max = _parse_float_or_none(self.xmax_edit.text())
        s.y_min = _parse_float_or_none(self.ymin_edit.text())
        s.y_max = _parse_float_or_none(self.ymax_edit.text())
        s.tick_label_size = self.tick_spin.value()
        s.title_fontsize = self.title_fs_spin.value()
        s.xlabel_fontsize = s.ylabel_fontsize = self.label_fs_spin.value()
        s.face_color = self.face_btn.color()
        s.face_alpha = self.face_alpha_spin.value()
        s.spine_color = self.spine_btn.color()
        s.spine_width = self.spine_width_spin.value()
        s.spine_style = self.spine_style_combo.currentText()
        s.aspect = self.aspect_combo.currentData()
        s.aspect_ratio = self.aspect_ratio_spin.value()
        s.legend_visible = self.legend_check.isChecked()
        s.legend_loc = self.legend_loc_combo.currentText()
        s.legend_frame = self.legend_frame_check.isChecked()
        s.legend_fontsize = self.legend_fs_spin.value()
        s.legend_ncol = self.legend_ncol_spin.value()
        s.grid_axis = self.grid_axis_combo.currentText()
        s.grid_ticks = self.grid_ticks_combo.currentText()
        s.grid_style = self.grid_style_combo.currentText()
        s.grid_width = self.grid_width_spin.value()
        s.grid_color = self.grid_color_btn.color()
        s.grid_alpha = self.grid_alpha_spin.value()
        self.changed.emit()


_IMAGE_CMAPS = ["viridis", "plasma", "inferno", "magma", "cividis", "gray",
                "coolwarm", "turbo", "RdBu", "Spectral"]
_INTERP = ["nearest", "antialiased", "bilinear", "bicubic", "none"]


class ImageStyleWidget(QWidget):
    """Bind to a :class:`~NoorSuite.model.SubplotModel` and edit ``sub.image`` (an ImageRef)."""

    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ref = None
        self._loading = False
        form = QFormLayout(self)
        _tighten(form)

        self.cmap_combo = QComboBox(); self.cmap_combo.addItems(_IMAGE_CMAPS)
        self.cmap_combo.setEditable(True)
        self.cmap_combo.currentTextChanged.connect(self._push)
        form.addRow("Colourmap:", self.cmap_combo)
        self.vmin_edit = QLineEdit(); self.vmin_edit.setPlaceholderText("auto")
        self.vmin_edit.editingFinished.connect(self._push)
        self.vmax_edit = QLineEdit(); self.vmax_edit.setPlaceholderText("auto")
        self.vmax_edit.editingFinished.connect(self._push)
        form.addRow("vmin / vmax:", _pair(self.vmin_edit, self.vmax_edit))
        self.interp_combo = QComboBox(); self.interp_combo.addItems(_INTERP)
        self.interp_combo.currentTextChanged.connect(self._push)
        form.addRow("Interpolation:", self.interp_combo)
        self.origin_combo = QComboBox(); self.origin_combo.addItems(["upper", "lower"])
        self.origin_combo.currentTextChanged.connect(self._push)
        form.addRow("Origin:", self.origin_combo)
        self.aspect_combo = QComboBox(); self.aspect_combo.addItems(["auto", "equal"])
        self.aspect_combo.currentTextChanged.connect(self._push)
        form.addRow("Aspect:", self.aspect_combo)
        self.alpha_spin = _spin(0.05, 1.0, 0.05)
        self.alpha_spin.valueChanged.connect(self._push)
        form.addRow("Alpha:", self.alpha_spin)
        self.colorbar_check = QCheckBox("Show colourbar")
        self.colorbar_check.toggled.connect(self._push)
        form.addRow("", self.colorbar_check)

    def set_subplot(self, sub):
        ref = sub.image if sub is not None else None
        self._ref = ref
        self._loading = True
        try:
            self.setEnabled(ref is not None)
            if ref is None:
                return
            self.cmap_combo.setCurrentText(ref.cmap)
            self.vmin_edit.setText(_fmt(ref.vmin))
            self.vmax_edit.setText(_fmt(ref.vmax))
            self.interp_combo.setCurrentText(ref.interpolation)
            self.origin_combo.setCurrentText(ref.origin)
            self.aspect_combo.setCurrentText(ref.aspect)
            self.alpha_spin.setValue(ref.alpha)
            self.colorbar_check.setChecked(ref.colorbar)
        finally:
            self._loading = False

    def _push(self, *_):
        if self._loading or self._ref is None:
            return
        r = self._ref
        r.cmap = self.cmap_combo.currentText().strip() or "viridis"
        r.vmin = _parse_float_or_none(self.vmin_edit.text())
        r.vmax = _parse_float_or_none(self.vmax_edit.text())
        r.interpolation = self.interp_combo.currentText()
        r.origin = self.origin_combo.currentText()
        r.aspect = self.aspect_combo.currentText()
        r.alpha = self.alpha_spin.value()
        r.colorbar = self.colorbar_check.isChecked()
        self.changed.emit()


def _spin(lo, hi, step=1.0):
    box = QDoubleSpinBox()
    box.setRange(lo, hi)
    box.setSingleStep(step)
    return box


def _pair(a, b) -> QWidget:
    from PyQt6.QtWidgets import QHBoxLayout
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(a)
    lay.addWidget(b)
    return w


# -------------------------------------------------------------------------- dialogs
class _BaseEditDialog(QDialog):
    """Live-applies edits; restores a snapshot on Cancel. Content is scrollable."""

    def __init__(self, target, fields, on_change, parent=None, title="Edit"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self._target = target
        self._fields = fields
        self._on_change = on_change
        self._snapshot = {f: getattr(target, f) for f in fields}

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._body = QWidget()
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(4, 4, 4, 4)
        self._scroll.setWidget(self._body)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.addWidget(self._scroll)
        root.addWidget(self._buttons)

        try:
            cap = int(self.screen().availableGeometry().height() * 0.85)
        except Exception:
            cap = 560
        self.resize(400, min(560, cap))
        self.setMaximumHeight(cap)

    # subclasses add their content here
    def _content_layout(self) -> QVBoxLayout:
        return self._body_layout

    def _apply(self):
        if callable(self._on_change):
            self._on_change()

    def reject(self):
        for f, v in self._snapshot.items():
            setattr(self._target, f, v)
        self._apply()
        super().reject()


class TraceStyleDialog(_BaseEditDialog):
    def __init__(self, ref, on_change, parent=None):
        from .model import TraceRef
        fields = TraceRef._STYLE_FIELDS + ("label", "enabled")
        super().__init__(ref, fields, on_change, parent,
                         title=f"Trace style - {ref.display_label}")
        widget = TraceStyleWidget()
        widget.set_trace(ref)
        widget.changed.connect(self._apply)
        self._content_layout().addWidget(widget)


class AxesDialog(_BaseEditDialog):
    def __init__(self, subplot, on_change, parent=None):
        from .model import SubplotModel
        super().__init__(subplot, SubplotModel._FIELDS, on_change, parent,
                         title="Axes / panel settings")
        widget = AxesStyleWidget()
        widget.set_subplot(subplot)
        widget.changed.connect(self._apply)
        self._content_layout().addWidget(widget)


class ImageDialog(_BaseEditDialog):
    def __init__(self, subplot, on_change, parent=None):
        from .model import ImageRef
        ref = subplot.image
        super().__init__(ref, ImageRef._FIELDS, on_change, parent, title="Image settings")
        widget = ImageStyleWidget()
        widget.set_subplot(subplot)
        widget.changed.connect(self._apply)
        self._content_layout().addWidget(widget)


class LegendDialog(_BaseEditDialog):
    _LEGEND_FIELDS = ("legend_visible", "legend_loc", "legend_frame",
                      "legend_fontsize", "legend_ncol")

    def __init__(self, subplot, on_change, parent=None):
        super().__init__(subplot, self._LEGEND_FIELDS, on_change, parent, title="Legend")
        form = QFormLayout()
        _tighten(form)
        self.visible = QCheckBox("Show legend")
        self.visible.setChecked(subplot.legend_visible)
        self.visible.toggled.connect(self._push)
        form.addRow("", self.visible)
        self.loc = QComboBox(); self.loc.addItems(LEGEND_LOCS)
        self.loc.setCurrentText(subplot.legend_loc)
        self.loc.currentTextChanged.connect(self._push)
        form.addRow("Location:", self.loc)
        self.frame = QCheckBox("Frame")
        self.frame.setChecked(subplot.legend_frame)
        self.frame.toggled.connect(self._push)
        form.addRow("", self.frame)
        self.fontsize = _spin(4.0, 32.0)
        self.fontsize.setValue(subplot.legend_fontsize)
        self.fontsize.valueChanged.connect(self._push)
        form.addRow("Font size:", self.fontsize)
        self.ncol = QSpinBox(); self.ncol.setRange(1, 8)
        self.ncol.setValue(int(subplot.legend_ncol))
        self.ncol.valueChanged.connect(self._push)
        form.addRow("Columns:", self.ncol)
        self._content_layout().addLayout(form)

    def _push(self, *_):
        s = self._target
        s.legend_visible = self.visible.isChecked()
        s.legend_loc = self.loc.currentText()
        s.legend_frame = self.frame.isChecked()
        s.legend_fontsize = self.fontsize.value()
        s.legend_ncol = self.ncol.value()
        self._apply()


class TextDialog(_BaseEditDialog):
    """Edit a subplot title or axis label (text, font size, and scale for axes)."""

    _KIND_MAP = {
        "title": ("title", "title_fontsize", None),
        "xlabel": ("x_label", "xlabel_fontsize", "x_scale"),
        "ylabel": ("y_label", "ylabel_fontsize", "y_scale"),
    }

    def __init__(self, subplot, kind, on_change, parent=None):
        self._text_attr, self._fs_attr, self._scale_attr = self._KIND_MAP[kind]
        fields = [self._text_attr, self._fs_attr]
        if self._scale_attr:
            fields.append(self._scale_attr)
        super().__init__(subplot, fields, on_change, parent, title=f"Edit {kind}")

        form = QFormLayout()
        _tighten(form)
        self.text_edit = QLineEdit(getattr(subplot, self._text_attr))
        self.text_edit.textEdited.connect(self._push)
        form.addRow("Text:", self.text_edit)
        self.fs_spin = _spin(4.0, 48.0)
        self.fs_spin.setValue(getattr(subplot, self._fs_attr))
        self.fs_spin.valueChanged.connect(self._push)
        form.addRow("Font size:", self.fs_spin)
        if self._scale_attr:
            self.scale_combo = QComboBox(); self.scale_combo.addItems(["linear", "log"])
            self.scale_combo.setCurrentText(getattr(subplot, self._scale_attr))
            self.scale_combo.currentTextChanged.connect(self._push)
            form.addRow("Scale:", self.scale_combo)
        self._content_layout().addLayout(form)

    def _push(self, *_):
        setattr(self._target, self._text_attr, self.text_edit.text())
        setattr(self._target, self._fs_attr, self.fs_spin.value())
        if self._scale_attr:
            setattr(self._target, self._scale_attr, self.scale_combo.currentText())
        self._apply()


class FigureStyleWidget(QWidget):
    """Bind to a :class:`~NoorSuite.model.SheetModel` and edit its figure background,
    outer frame (border), and size. Embedded both as the right-panel "Figure" tab and
    inside :class:`FigureDialog` (the figure-background double-click editor)."""

    changed = pyqtSignal()

    _FIELDS = ("fig_face_color", "fig_face_alpha", "fig_frame_on",
               "fig_edge_color", "fig_edge_width", "fig_edge_style",
               "fig_width_cm", "fig_height_cm")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sheet = None
        self._loading = False
        form = QFormLayout(self)
        _tighten(form)

        self.face_btn = ColorButton(label="Background")
        self.face_btn.colorChanged.connect(lambda *_: self._push())
        form.addRow("Background:", self.face_btn)
        self.face_alpha = _spin(0.0, 1.0, 0.05)
        self.face_alpha.valueChanged.connect(self._push)
        form.addRow("Background alpha:", self.face_alpha)

        self.frame_on = QCheckBox("Draw outer frame (border)")
        self.frame_on.toggled.connect(self._push)
        form.addRow("", self.frame_on)
        self.edge_btn = ColorButton(label="Border colour")
        self.edge_btn.colorChanged.connect(lambda *_: self._push())
        form.addRow("Border colour:", self.edge_btn)
        self.edge_width = _spin(0.0, 20.0, 0.5)
        self.edge_width.valueChanged.connect(self._push)
        form.addRow("Border width:", self.edge_width)
        self.edge_style = QComboBox(); self.edge_style.addItems(_LINESTYLE_ITEMS)
        self.edge_style.currentTextChanged.connect(self._push)
        form.addRow("Border style:", self.edge_style)

        self.width_cm_edit = QLineEdit(); self.width_cm_edit.setPlaceholderText("auto")
        self.width_cm_edit.editingFinished.connect(self._push)
        self.height_cm_edit = QLineEdit(); self.height_cm_edit.setPlaceholderText("auto")
        self.height_cm_edit.editingFinished.connect(self._push)
        form.addRow("Figure size W x H (cm):", _pair(self.width_cm_edit, self.height_cm_edit))

        size_btns = QHBoxLayout()
        self.square_btn = QPushButton("Make square")
        self.square_btn.setToolTip(
            "Fix the figure's on-screen shape to a square, independent of the data or "
            "axes scaling -- unlike an axes aspect ratio, this never needs retuning "
            "when the plotted columns change.")
        self.square_btn.clicked.connect(self._make_square)
        self.auto_btn = QPushButton("Auto (fill panel)")
        self.auto_btn.clicked.connect(self._make_auto)
        size_btns.addWidget(self.square_btn)
        size_btns.addWidget(self.auto_btn)
        form.addRow("", size_btns)
        hint = QLabel("Both set: the figure keeps that width:height shape on screen "
                      "(letterboxed to fit the tab) and exports at exactly that size. "
                      "Blank: fills the tab like before.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        form.addRow("", hint)

    def set_sheet(self, sheet_model):
        self._sheet = sheet_model
        self._loading = True
        try:
            if sheet_model is None:
                self.setEnabled(False)
                return
            self.setEnabled(True)
            s = sheet_model
            self.face_btn.setColor(s.fig_face_color)
            self.face_alpha.setValue(s.fig_face_alpha)
            self.frame_on.setChecked(s.fig_frame_on)
            self.edge_btn.setColor(s.fig_edge_color)
            self.edge_width.setValue(s.fig_edge_width)
            self.edge_style.setCurrentText(s.fig_edge_style)
            self.width_cm_edit.setText(_fmt(s.fig_width_cm))
            self.height_cm_edit.setText(_fmt(s.fig_height_cm))
        finally:
            self._loading = False

    def _make_square(self):
        side = (_parse_float_or_none(self.width_cm_edit.text())
                or _parse_float_or_none(self.height_cm_edit.text()) or 10.0)
        self.width_cm_edit.setText(_fmt(side))
        self.height_cm_edit.setText(_fmt(side))
        self._push()

    def _make_auto(self):
        self.width_cm_edit.clear()
        self.height_cm_edit.clear()
        self._push()

    def _push(self, *_):
        if self._loading or self._sheet is None:
            return
        s = self._sheet
        s.fig_face_color = self.face_btn.color()
        s.fig_face_alpha = self.face_alpha.value()
        s.fig_frame_on = self.frame_on.isChecked()
        s.fig_edge_color = self.edge_btn.color()
        s.fig_edge_width = self.edge_width.value()
        s.fig_edge_style = self.edge_style.currentText()
        s.fig_width_cm = _parse_float_or_none(self.width_cm_edit.text())
        s.fig_height_cm = _parse_float_or_none(self.height_cm_edit.text())
        self.changed.emit()


class FigureDialog(_BaseEditDialog):
    """Figure background, outer frame (border), and size settings -- the double-click
    entry point to the same controls embedded as the "Figure" inspector tab."""

    _FIELDS = FigureStyleWidget._FIELDS

    def __init__(self, sheet_model, on_change, parent=None):
        super().__init__(sheet_model, self._FIELDS, on_change, parent,
                         title="Figure background, frame & size")
        widget = FigureStyleWidget()
        widget.set_sheet(sheet_model)
        widget.changed.connect(self._apply)
        self._content_layout().addWidget(widget)


# Backwards-compatible alias.
FigureBackgroundDialog = FigureDialog


# ---------------------------------------------------------------- bulk trace edit
class BulkTraceEditWidget(QWidget):
    """Edit several :class:`~NoorSuite.model.TraceRef`s at once (apply-on-button)."""

    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._refs: list = []
        form = QFormLayout(self)
        _tighten(form)

        self.count_label = QLineEdit()
        self.count_label.setReadOnly(True)
        form.addRow("Selection:", self.count_label)

        self.type_combo = _keep_combo(PLOT_TYPES)
        form.addRow("Plot type:", self.type_combo)
        self.style_combo = _keep_combo(LINE_STYLE_LABELS)
        form.addRow("Line style:", self.style_combo)
        self.marker_combo = _keep_combo(MARKER_LABELS)
        form.addRow("Marker:", self.marker_combo)
        self.transform_combo = _keep_combo(Y_TRANSFORM_LABELS)
        form.addRow("Y transform:", self.transform_combo)

        self.color_btn = ColorButton(label="Set all to colour")
        self._color_touched = False
        self.color_btn.colorChanged.connect(self._mark_color)
        form.addRow("Colour:", self.color_btn)

        self.width_edit = QLineEdit(); self.width_edit.setPlaceholderText("e.g. 2  or  *2  or  +0.5")
        form.addRow("Line width:", self.width_edit)
        self.msize_edit = QLineEdit(); self.msize_edit.setPlaceholderText("blank = keep")
        form.addRow("Marker size:", self.msize_edit)
        self.alpha_edit = QLineEdit(); self.alpha_edit.setPlaceholderText("blank = keep")
        form.addRow("Opacity:", self.alpha_edit)

        self.apply_btn = QPushButton("Apply to selection")
        self.apply_btn.clicked.connect(self._apply)
        form.addRow(self.apply_btn)

    def _mark_color(self, *_):
        self._color_touched = True

    def set_traces(self, refs):
        self._refs = list(refs)
        self._color_touched = False
        self.count_label.setText(f"{len(self._refs)} traces")
        for combo in (self.type_combo, self.style_combo, self.marker_combo,
                      self.transform_combo):
            combo.setCurrentIndex(0)
        for edit in (self.width_edit, self.msize_edit, self.alpha_edit):
            edit.clear()
        self.apply_btn.setText(f"Apply to {len(self._refs)} traces")

    def _apply(self):
        if not self._refs:
            return
        pt = _keep_value(self.type_combo)
        ls_i = _keep_index(self.style_combo)
        mk_i = _keep_index(self.marker_combo)
        yt_i = _keep_index(self.transform_combo)
        for r in self._refs:
            if pt is not None:
                r.plot_type = pt
            if ls_i is not None:
                r.line_style = LINE_STYLES[ls_i]
            if mk_i is not None:
                r.marker = MARKERS[mk_i]
            if yt_i is not None:
                r.scale_factor = Y_TRANSFORMS[yt_i]
            if self._color_touched:
                r.color = self.color_btn.color()
            w = apply_numeric_expr(r.line_width, self.width_edit.text())
            if w is not None:
                r.line_width = max(0.0, w)
            ms = apply_numeric_expr(r.marker_size, self.msize_edit.text())
            if ms is not None:
                r.marker_size = max(0.0, ms)
            a = apply_numeric_expr(r.alpha, self.alpha_edit.text())
            if a is not None:
                r.alpha = min(1.0, max(0.01, a))
        self.changed.emit()


def _keep_combo(labels) -> QComboBox:
    box = QComboBox()
    box.addItem(_KEEP)
    box.addItems(labels)
    return box


def _keep_value(box: QComboBox):
    return None if box.currentIndex() == 0 else box.currentText()


def _keep_index(box: QComboBox):
    return None if box.currentIndex() == 0 else box.currentIndex() - 1


# --------------------------------------------------------------------- colormaps
class ColormapPanel(QWidget):
    """Switch / build / manage colormaps for a sheet. Emits ``applied()`` after changes."""

    applied = pyqtSignal()

    def __init__(self, host, parent=None):
        # host must provide: active_sheet_model(), project_colormaps(), selected_subplots(),
        # available_colormap_names(), sample_colors(spec, n), resolve_colormap(name)
        super().__init__(parent)
        self._host = host
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.setSpacing(4)

        row = QHBoxLayout()
        self.combo = QComboBox()
        row.addWidget(QLabel("Colormap:"))
        row.addWidget(self.combo, 1)
        outer.addLayout(row)

        self.scope_group = QButtonGroup(self)
        srow = QHBoxLayout()
        for i, text in enumerate(("Whole sheet", "Active subplot", "Selected subplots")):
            rb = QRadioButton(text)
            if i == 0:
                rb.setChecked(True)
            self.scope_group.addButton(rb, i)
            srow.addWidget(rb)
        outer.addLayout(srow)

        b1 = QHBoxLayout()
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.clicked.connect(self._apply)
        self.save_btn = QPushButton("Save colours as…")
        self.save_btn.clicked.connect(self._save_custom)
        b1.addWidget(self.apply_btn)
        b1.addWidget(self.save_btn)
        outer.addLayout(b1)

        b2 = QHBoxLayout()
        self.delete_btn = QPushButton("Delete")
        self.delete_btn.clicked.connect(self._delete_custom)
        self.tolib_btn = QPushButton("→ Library")
        self.tolib_btn.clicked.connect(self._copy_to_library)
        b2.addWidget(self.delete_btn)
        b2.addWidget(self.tolib_btn)
        outer.addLayout(b2)

        b3 = QHBoxLayout()
        self.export_btn = QPushButton("Export…")
        self.export_btn.clicked.connect(self._export)
        self.import_btn = QPushButton("Import…")
        self.import_btn.clicked.connect(self._import)
        b3.addWidget(self.export_btn)
        b3.addWidget(self.import_btn)
        outer.addLayout(b3)

    def refresh(self):
        sm = self._host.active_sheet_model()
        self.setEnabled(sm is not None)
        current = self.combo.currentText()
        self.combo.blockSignals(True)
        self.combo.clear()
        names = self._host.available_colormap_names()
        self.combo.addItems(names)
        if sm is not None and sm.active_colormap in names:
            self.combo.setCurrentText(sm.active_colormap)
        elif current in names:
            self.combo.setCurrentText(current)
        self.combo.blockSignals(False)

    # -- helpers --
    def _target_subplots(self):
        sm = self._host.active_sheet_model()
        if sm is None:
            return []
        scope = self.scope_group.checkedId()
        if scope == 0:
            return list(sm.subplots)
        if scope == 1:
            return [sm.get_active_subplot()]
        picked = self._host.selected_subplots()
        return picked or [sm.get_active_subplot()]

    def _apply(self):
        sm = self._host.active_sheet_model()
        if sm is None:
            return
        name = self.combo.currentText()
        spec = self._host.resolve_colormap(name)
        if spec is None:
            return
        for sub in self._target_subplots():
            colors = self._host.sample_colors(spec, len(sub.traces))
            for ref, col in zip(sub.traces, colors):
                ref.color = col
        if self.scope_group.checkedId() == 0:
            sm.active_colormap = name
        self.applied.emit()

    def _save_custom(self):
        sm = self._host.active_sheet_model()
        if sm is None:
            return
        name, ok = QInputDialog.getText(self, "Save colormap", "Name:")
        if not ok or not name:
            return
        colors = []
        for sub in self._target_subplots():
            colors += [r.color for r in sub.traces]
        if not colors:
            QMessageBox.information(self, "Save colormap", "No traces in the target scope.")
            return
        sm.colormaps = [c for c in sm.colormaps if c.name != name]
        sm.colormaps.append(ColorMap(name, colors))
        self.refresh()
        self.combo.setCurrentText(name)

    def _delete_custom(self):
        sm = self._host.active_sheet_model()
        if sm is None:
            return
        name = self.combo.currentText()
        before = len(sm.colormaps)
        sm.colormaps = [c for c in sm.colormaps if c.name != name]
        if len(sm.colormaps) == before:
            QMessageBox.information(self, "Delete colormap",
                                   "Only this sheet's custom colormaps can be deleted.")
            return
        if sm.active_colormap == name:
            sm.active_colormap = ""
        self.refresh()

    def _copy_to_library(self):
        name = self.combo.currentText()
        self._host.copy_colormap_to_library(name)
        self.refresh()

    def _export(self):
        cmap = self._host.resolve_colormap(self.combo.currentText())
        if not isinstance(cmap, ColorMap):
            QMessageBox.information(self, "Export", "Pick a custom colormap to export.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export colormap", f"{cmap.name}.scicmap",
                                              "SciSuite colormap (*.scicmap)")
        if path:
            self._host.export_colormap(cmap, path)

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import colormap", "",
                                              "SciSuite colormap (*.scicmap *.json)")
        if path:
            self._host.import_colormap(path)
            self.refresh()
