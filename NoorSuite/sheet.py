"""The interactive plot sheet widget: a Figure + canvas holding an NxM grid of subplots.

Rendering is full-clear-and-rebuild (:meth:`PlotSheet.render`). Every render pass
repopulates :attr:`PlotSheet.artist_map` (matplotlib ``Artist`` -> :class:`~NoorSuite.model.TraceRef`)
plus the per-subplot text / legend handles, so :meth:`PlotSheet.on_canvas_click` can
hit-test a double click and report which element was hit via the ``element_double_clicked``
signal.
"""
from __future__ import annotations

import matplotlib as mpl
import numpy as np
from matplotlib.backends.backend_qtagg import (FigureCanvasQTAgg,
                                               NavigationToolbar2QT)
from matplotlib.colors import to_rgb
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle
from matplotlib.transforms import Affine2D, Affine2DBase
from matplotlib.ticker import FormatStrFormatter
from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel, QPlainTextEdit,
                             QScrollArea, QSlider, QSplitter, QVBoxLayout, QWidget)

from .model import (INDEX_COL, ImageRef, ShapeRef, SheetModel, apply_tick_labels,
                    gradient_rgba, heatmap_band_edges, heatmap_iso_levels, resolve_ticks)
from .widgets import CollapsibleSection

class _DynRot(Affine2DBase):
    """Rotation by ``angle`` degrees *in display space* about the display position of a data
    point -- so a rotated object turns exactly as the mouse does, whatever the axes scales.
    Composed as ``tf + _DynRot(tf, cx, cy, angle)``; follows layout / limit changes."""

    def __init__(self, tf, cx, cy, angle):
        super().__init__()
        self._tf, self._c, self._a = tf, (float(cx), float(cy)), float(angle)
        self._mtx = None
        self.set_children(tf)
        self._invalid = 1

    def get_matrix(self):
        if self._invalid or self._mtx is None:
            c = self._tf.transform(self._c)
            self._mtx = Affine2D().rotate_deg_around(c[0], c[1], self._a).get_matrix()
            self._invalid = 0
        return self._mtx


_ACTIVE_ACCENT = "#ff7f0e"
_HL_PAD_PX = 6.0
# Diagonal (cm) of the default 8x6in figure -- text sizes (title/label/tick/legend) are
# stored in points, an absolute unit, so a much smaller physical figure (fig_width_cm /
# fig_height_cm) would otherwise keep the same fixed-size text and tight_layout() would
# squeeze the actual axes box down to make room for it. render() scales those fontsizes
# by (this figure's diagonal / _REFERENCE_DIAG_CM) so the text-to-figure proportion holds
# regardless of the requested physical size.
_REFERENCE_DIAG_CM = (8.0 ** 2 + 6.0 ** 2) ** 0.5 * 2.54


def rgba(color, alpha):
    try:
        r, g, b = to_rgb(color)
    except (ValueError, TypeError):
        r, g, b = 1.0, 1.0, 1.0
    return (r, g, b, float(alpha))


class _AspectCanvasHost(QScrollArea):
    """Hosts the figure canvas; fills its space normally, or -- when given a
    width:height ratio -- keeps the canvas letterboxed at that fixed shape
    (centered) regardless of the panel's own size. Independent of the axes'
    own data aspect: this fixes the *figure's* shape, not any data units.

    Ctrl + mouse wheel over the canvas zooms the whole figure (``zoom`` > 1 grows
    the canvas past the viewport, which then scrolls); Ctrl+0 resets. The owning
    :class:`PlotSheet` keeps the figure's *size in inches* fixed while zoomed by scaling
    its dpi, so text and lines zoom with the plot (see ``PlotSheet._sync_zoom_dpi``)."""

    zoom_changed = pyqtSignal(float)
    ZOOM_MIN, ZOOM_MAX, ZOOM_STEP = 0.3, 6.0, 1.15

    def __init__(self, canvas, parent=None):
        super().__init__(parent)
        # set before anything below: Qt calls eventFilter/resizeEvent during setup
        self._canvas = canvas
        self._ratio = None       # None -> fill; else width / height to maintain
        self._zoom = 1.0
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setWidgetResizable(False)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._inner = QWidget()
        self.setWidget(self._inner)
        canvas.setParent(self._inner)
        canvas.installEventFilter(self)

    @property
    def zoom(self) -> float:
        return self._zoom

    def set_zoom(self, zoom: float):
        zoom = min(max(float(zoom), self.ZOOM_MIN), self.ZOOM_MAX)
        if abs(zoom - self._zoom) < 1e-9:
            return
        self._zoom = zoom
        self.zoom_changed.emit(zoom)   # the sheet re-scales fig dpi *before* we resize
        self._relayout()

    def eventFilter(self, obj, event):
        if obj is self._canvas:
            if event.type() == QEvent.Type.Wheel and \
                    event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                steps = event.angleDelta().y() / 120.0
                if steps:
                    self.set_zoom(self._zoom * self.ZOOM_STEP ** steps)
                event.accept()
                return True
            if event.type() == QEvent.Type.KeyPress and \
                    event.modifiers() & Qt.KeyboardModifier.ControlModifier and \
                    event.key() == Qt.Key.Key_0:
                self.set_zoom(1.0)
                return True
        return super().eventFilter(obj, event)

    def set_ratio(self, ratio: "float | None"):
        ratio = float(ratio) if ratio else None
        if ratio == self._ratio:
            return
        self._ratio = ratio
        self._relayout()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout()

    def _relayout(self):
        # Base the canvas size on the host's full size, not the viewport: scrollbars that
        # appear when zoomed in shrink the viewport and would nudge the figure's size.
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0 or not hasattr(self, "_inner"):
            return
        vp = self.viewport()
        if not self._ratio:
            base_w, base_h = w, h
        elif w / h > self._ratio:
            base_h, base_w = h, max(1, round(h * self._ratio))
        else:
            base_w, base_h = w, max(1, round(w / self._ratio))
        cw = max(1, round(base_w * self._zoom))
        ch = max(1, round(base_h * self._zoom))
        iw, ih = max(vp.width(), cw), max(vp.height(), ch)
        self._inner.resize(iw, ih)
        self._canvas.setGeometry((iw - cw) // 2, (ih - ch) // 2, cw, ch)


class PlotSheet(QWidget):
    active_subplot_changed = pyqtSignal(int)
    element_double_clicked = pyqtSignal(object)   # dict: {"kind": ..., ...}
    element_right_clicked = pyqtSignal(object, object)   # (hit dict, QMouseEvent | None)
    shape_selected = pyqtSignal(object)           # a ShapeRef was clicked on the canvas
    shape_edited = pyqtSignal(object)             # a ShapeRef was moved / scaled / rotated
    notes_changed = pyqtSignal()
    image_changed = pyqtSignal()                  # slider moved / axis switched

    def __init__(self, model: SheetModel | None = None, parent=None):
        super().__init__(parent)
        self.model = model or SheetModel()
        self._images: dict = {}       # id -> ImageObject, set on render()

        self.fig = Figure(figsize=(8, 6), dpi=100)
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        self.canvas.mpl_connect("button_press_event", self.on_canvas_click)
        self.canvas.mpl_connect("motion_notify_event", self._on_canvas_motion)
        self.canvas.mpl_connect("button_release_event", self._on_canvas_release)
        self.canvas.mpl_connect("draw_event", self._on_draw)
        self.canvas.mpl_connect("resize_event", self._on_canvas_resize)

        # Render-pass bookkeeping for hit-testing.
        self.artist_map = {}          # Artist -> TraceRef / ImageRef
        self._axes = []               # Axes in subplot order
        self._text_targets = []       # (Text, kind, subplot_index)
        self._image_axes = {}         # Axes -> subplot_index (has an image)
        self._image_geom = {}         # Axes -> (xpos, xlabels, ypos, ylabels) for tick control
        self._highlight_patch = None  # figure-level Rectangle around the active subplot
        self._sel_shape = None        # ShapeRef whose handles are shown
        self._handle_artists: list = []
        self._handle_pts: dict = {}   # handle name -> display (x, y) from the last draw
        self._handle_sig = None
        self._shape_artist: dict = {} # id(ShapeRef) -> artist (rebuilt every render)
        self._drag = None             # active move / scale / rotate gesture
        self._last_drag_render = 0.0
        self._in_highlight = False    # reentrancy guard for the draw_event handler

        self.canvas_host = _AspectCanvasHost(self.canvas)
        self.canvas_host.zoom_changed.connect(lambda _z: self._sync_zoom_dpi())
        self._exporting = False       # suppress the active-subplot cue while exporting

        plot_page = QWidget()
        plot_layout = QVBoxLayout(plot_page)
        plot_layout.setContentsMargins(0, 0, 0, 0)
        plot_layout.addWidget(self.toolbar)
        plot_layout.addWidget(self.canvas_host, 1)

        # --- image navigation bar (between plot and notes; hidden unless ndim > 2) ---
        self._img_loading = False
        self.slider_bar = QWidget()
        sl = QHBoxLayout(self.slider_bar)
        sl.setContentsMargins(6, 2, 6, 2)
        sl.addWidget(QLabel("Row (y):"))
        self.row_combo = QComboBox()
        self.row_combo.currentIndexChanged.connect(self._on_display_combo)
        sl.addWidget(self.row_combo)
        sl.addWidget(QLabel("Col (x):"))
        self.col_combo = QComboBox()
        self.col_combo.currentIndexChanged.connect(self._on_display_combo)
        sl.addWidget(self.col_combo)
        sl.addSpacing(8)
        sl.addWidget(QLabel("Slice:"))
        self.axis_combo = QComboBox()
        self.axis_combo.currentIndexChanged.connect(self._on_axis_combo)
        sl.addWidget(self.axis_combo)
        self.slice_slider = QSlider(Qt.Orientation.Horizontal)
        self.slice_slider.valueChanged.connect(self._on_slice_slider)
        sl.addWidget(self.slice_slider, 1)
        self.slice_label = QLabel("-")
        self.slice_label.setMinimumWidth(90)
        sl.addWidget(self.slice_label)
        self.slider_bar.setVisible(False)

        self.notes_edit = QPlainTextEdit()
        self.notes_edit.setPlaceholderText(
            "Notes for this sheet - observations, interpretation, TODOs. "
            "Saved with the project; searchable from the project tree.")
        self.notes_edit.setPlainText(self.model.notes)
        self.notes_edit.setMinimumHeight(90)
        self.notes_edit.textChanged.connect(self._on_notes_changed)

        # Notes live *below* the plot in a collapsible pane, so you can write while
        # still seeing the figure. Drag the splitter handle to size it.
        self.notes_section = CollapsibleSection("Notes", self.notes_edit,
                                                expanded=bool(self.model.notes.strip()))
        self.notes_section.toggled.connect(self._on_notes_toggled)

        self._splitter = QSplitter(Qt.Orientation.Vertical)
        self._splitter.addWidget(plot_page)
        self._splitter.addWidget(self.slider_bar)
        self._splitter.addWidget(self.notes_section)
        self._splitter.setStretchFactor(0, 1)
        self._splitter.setStretchFactor(1, 0)
        self._splitter.setStretchFactor(2, 0)
        for i in range(3):
            self._splitter.setCollapsible(i, False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._splitter)
        self._sync_notes_header()

    # ------------------------------------------------------------- image slider
    def _active_image_ref(self):
        sub = self.model.get_active_subplot()
        return sub.image if (sub and sub.image) else None

    def _active_image(self):
        ref = self._active_image_ref()
        obj = self._images.get(ref.data_id) if ref else None
        return (ref, obj) if (ref and obj) else (None, None)

    @staticmethod
    def _extra_axes(ref, obj):
        return [a for a in range(obj.ndim) if a not in ref.display_axes]

    @classmethod
    def _effective_slice_axis(cls, ref, obj):
        extra = cls._extra_axes(ref, obj)
        if ref.slice_axis is not None and ref.slice_axis in extra:
            return ref.slice_axis
        return extra[0] if extra else None

    def _fill_display_index(self, ref, obj):
        for a in self._extra_axes(ref, obj):
            ref.index.setdefault(a, obj.shape[a] // 2)

    def _rebuild_slider_bar(self):
        ref, obj = self._active_image()
        if ref is None or obj.ndim <= 2:
            self.slider_bar.setVisible(False)
            return
        self.slider_bar.setVisible(True)
        self._img_loading = True
        try:
            self._fill_display_index(ref, obj)
            names = [f"{obj.axis_names[a]}  ({obj.shape[a]})" for a in range(obj.ndim)]
            for combo, cur in ((self.row_combo, ref.display_axes[0]),
                               (self.col_combo, ref.display_axes[1])):
                combo.clear()
                for a, nm in enumerate(names):
                    combo.addItem(nm, a)
                combo.setCurrentIndex(int(cur))

            extra = self._extra_axes(ref, obj)
            s = self._effective_slice_axis(ref, obj)
            ref.slice_axis = s
            self.axis_combo.clear()
            for a in extra:
                self.axis_combo.addItem(names[a], a)
            self.axis_combo.setCurrentIndex(extra.index(s))
        finally:
            self._img_loading = False
        self._sync_slider_to_axis(ref, obj)

    def _sync_slider_to_axis(self, ref, obj):
        a = ref.slice_axis
        if a is None:
            return
        n = obj.shape[a]
        cur = int(ref.index.get(a, n // 2))
        self.slice_slider.blockSignals(True)
        self.slice_slider.setRange(0, max(0, n - 1))
        self.slice_slider.setValue(min(max(cur, 0), n - 1))
        self.slice_slider.blockSignals(False)
        self.slice_label.setText(f"{obj.axis_names[a]} = {self.slice_slider.value()} / {n - 1}")

    def _on_display_combo(self, _i):
        if self._img_loading:
            return
        ref, obj = self._active_image()
        if ref is None:
            return
        r = self.row_combo.currentData()
        c = self.col_combo.currentData()
        if r is None or c is None or r == c:
            self._rebuild_slider_bar()       # revert to a valid state
            return
        ref.display_axes = [int(r), int(c)]
        if ref.slice_axis in (r, c):
            ref.slice_axis = next((a for a in range(obj.ndim) if a not in (r, c)), None)
        self._fill_display_index(ref, obj)
        self.render(self._repository, self._images)
        self.image_changed.emit()

    def _on_axis_combo(self, _i):
        if self._img_loading:
            return
        ref, obj = self._active_image()
        if ref is None:
            return
        s = self.axis_combo.currentData()
        if s is None:
            return
        s = int(s)
        if obj.ndim == 3:
            ref.display_axes = sorted(a for a in range(3) if a != s)
        elif s in ref.display_axes:
            free = self._extra_axes(ref, obj)
            if free:
                ref.display_axes = [free[0] if d == s else d for d in ref.display_axes]
        ref.slice_axis = s
        self._fill_display_index(ref, obj)
        self.render(self._repository, self._images)
        self.image_changed.emit()

    def _on_slice_slider(self, value):
        ref, obj = self._active_image()
        if ref is None or ref.slice_axis is None:
            return
        a = ref.slice_axis
        ref.index[a] = int(value)
        self.slice_label.setText(f"{obj.axis_names[a]} = {value} / {obj.shape[a] - 1}")
        self.render(self._repository, self._images)
        self.image_changed.emit()

    def _on_notes_changed(self):
        self.model.notes = self.notes_edit.toPlainText()
        self._sync_notes_header()
        self.notes_changed.emit()

    def _on_notes_toggled(self, expanded: bool):
        if expanded and self._splitter.sizes()[1] < 60:
            total = sum(self._splitter.sizes()) or 600
            self._splitter.setSizes([int(total * 0.72), int(total * 0.28)])

    def _sync_notes_header(self):
        has = bool(self.model.notes.strip())
        self.notes_section.set_title("Notes ●" if has else "Notes")

    def toggle_notes(self):
        self.notes_section.set_expanded(not self.notes_section.is_expanded())

    def refresh_notes(self):
        """Re-pull notes from the model (e.g. after a project load reused this widget)."""
        if self.notes_edit.toPlainText() != self.model.notes:
            self.notes_edit.blockSignals(True)
            self.notes_edit.setPlainText(self.model.notes)
            self.notes_edit.blockSignals(False)
        self._sync_notes_header()

    # ---------------------------------------------------------------- accessors
    @property
    def sheet_id(self):
        return self.model.sheet_id

    @property
    def name(self):
        return self.model.name

    @property
    def subplots(self):
        return self.model.subplots

    @property
    def active_index(self):
        return self.model.active_index

    @active_index.setter
    def active_index(self, value):
        self.model.active_index = value

    def set_grid(self, rows, cols):
        self.model.set_grid(rows, cols)
        self.active_subplot_changed.emit(self.model.active_index)

    def get_active_subplot(self):
        return self.model.get_active_subplot()

    # ------------------------------------------------------------------ render
    def render(self, repository: dict, images: dict | None = None):
        m = self.model
        self._repository = repository
        self._images = images or {}
        self.fig.clf()
        self.fig.set_facecolor(rgba(m.fig_face_color, m.fig_face_alpha))
        self.fig.set_frameon(bool(m.fig_frame_on))
        self.fig.patch.set_edgecolor(m.fig_edge_color)
        self.fig.patch.set_linewidth(float(m.fig_edge_width))
        try:
            self.fig.patch.set_linestyle(m.fig_edge_style)
        except Exception:
            pass

        self.artist_map = {}
        self._shape_artist = {}
        self._handle_sig = None
        self._axes = []
        self._text_targets = []
        if getattr(self, "assets", None) is None:
            self.assets = {}
        self._image_axes = {}
        self._image_geom = {}

        rows, cols = m.rows, m.cols
        if m.fig_width_cm and m.fig_height_cm:
            diag_cm = (float(m.fig_width_cm) ** 2 + float(m.fig_height_cm) ** 2) ** 0.5
            size_scale = diag_cm / _REFERENCE_DIAG_CM
        else:
            size_scale = 1.0
        # each subplot only gets 1/rows x 1/cols of the figure -- shrink text so a
        # bigger grid doesn't just keep the same absolute (point) sizes and squeeze
        # every cell's axes box to fit them (1x1 grid -> unchanged, matches before).
        grid_scale = 1.0 / max(1, max(rows, cols))
        text_scale = max(0.3, min(size_scale * grid_scale, 3.0))

        for idx in range(rows * cols):
            sub = m.subplots[idx]
            ax = self.fig.add_subplot(rows, cols, idx + 1)
            self._axes.append(ax)

            ax.set_facecolor(rgba(sub.face_color, sub.face_alpha))
            ax.tick_params(labelsize=sub.tick_label_size * text_scale)
            visible_spine = sub.spine_width > 0
            for spine in ax.spines.values():
                spine.set_visible(visible_spine)
                spine.set_edgecolor(sub.spine_color)
                spine.set_linewidth(sub.spine_width)
                try:
                    spine.set_linestyle(sub.spine_style)
                except Exception:
                    pass

            # auto-label the axes while the user hasn't set one: from the image's
            # axis names, else from the plotted column names.
            x_label, y_label = sub.x_label, sub.y_label
            if sub.image is not None:
                iobj = self._images.get(sub.image.data_id)
                if iobj is not None and iobj.ndim >= 2:
                    names = iobj.axis_names
                    r, c = (int(sub.image.display_axes[0]),
                            int(sub.image.display_axes[1]))
                    if not x_label and 0 <= c < len(names):
                        x_label = names[c]
                    if not y_label and 0 <= r < len(names):
                        y_label = names[r]
            if not x_label or not y_label:
                auto_x, auto_y = self._auto_trace_labels(sub, repository)
                x_label = x_label or auto_x
                y_label = y_label or auto_y

            title = ax.set_title(sub.title, fontsize=sub.title_fontsize * text_scale)
            xlab = ax.set_xlabel(x_label, fontsize=sub.xlabel_fontsize * text_scale)
            ylab = ax.set_ylabel(y_label, fontsize=sub.ylabel_fontsize * text_scale)
            self._text_targets += [(title, "title", idx),
                                   (xlab, "xlabel", idx),
                                   (ylab, "ylabel", idx)]

            for axis_name, scale in (("x", sub.x_scale), ("y", sub.y_scale)):
                try:
                    getattr(ax, f"set_{axis_name}scale")(scale)
                except Exception:
                    pass

            # data aspect ratio (an image, drawn below, sets its own and wins there)
            if sub.aspect == "equal":
                ax.set_aspect("equal", adjustable="box")
            elif sub.aspect == "custom":
                try:
                    ax.set_aspect(max(1e-9, float(sub.aspect_ratio)), adjustable="box")
                except (TypeError, ValueError):
                    ax.set_aspect("auto")
            else:
                ax.set_aspect("auto")

            self._apply_tick_format(ax, "x", sub.x_tick_format, sub.x_tick_digits)
            self._apply_tick_format(ax, "y", sub.y_tick_format, sub.y_tick_digits)

            ax.grid(False)
            if sub.show_grid:
                if sub.grid_ticks in ("minor", "both"):
                    ax.minorticks_on()
                ax.grid(True, which=sub.grid_ticks, axis=sub.grid_axis,
                        linestyle=sub.grid_style, linewidth=sub.grid_width,
                        color=sub.grid_color, alpha=sub.grid_alpha)

            # image layer (drawn under the traces)
            if sub.image is not None:
                self._draw_image(ax, sub.image, idx)

            n_drawn = 0
            stack = sub.stack()                 # traces + shapes, back to front
            for si, tref in enumerate(stack):
                if isinstance(tref, ShapeRef) or not tref.enabled:
                    continue
                data = repository.get(tref.data_id)
                if data is None or tref.y_col not in data.columns:
                    continue
                try:
                    x, y = tref.resolve(data)
                except Exception:
                    continue
                before = set(ax.get_children())
                self._draw_trace(ax, tref, x, y)
                if tref.show_errorbar:
                    try:
                        yerr = tref.resolve_yerr(data)
                    except Exception:
                        yerr = None
                    if yerr is not None:
                        self._draw_error_overlay(ax, tref, x, y, yerr)
                for art in set(ax.get_children()) - before:   # layer order = list order
                    art.set_zorder(2.0 + 0.01 * si)
                n_drawn += 1

            if sub.x_min is not None or sub.x_max is not None:
                ax.set_xlim(left=sub.x_min, right=sub.x_max)
            if sub.y_min is not None or sub.y_max is not None:
                ax.set_ylim(bottom=sub.y_min, top=sub.y_max)
            self._apply_axis_ticks(ax, sub)

            # shapes: drawn last so they never move the (already final) view limits
            if any(isinstance(it, ShapeRef) and it.enabled for it in stack):
                xl, yl = ax.get_xlim(), ax.get_ylim()
                for si, it in enumerate(stack):
                    if isinstance(it, ShapeRef) and it.enabled:
                        try:
                            self._draw_shape(ax, it, 2.0 + 0.01 * si, text_scale)
                        except Exception:
                            pass
                ax.set_xlim(xl)
                ax.set_ylim(yl)

            if sub.bare:                        # content only: nothing but what is drawn
                ax.set_axis_off()
                ax.set_title("")
                ax.patch.set_visible(False)
            if n_drawn and sub.legend_visible and not sub.bare:
                legend = ax.legend(loc=sub.legend_loc, frameon=sub.legend_frame,
                                   fontsize=sub.legend_fontsize * text_scale,
                                   ncol=max(1, int(sub.legend_ncol)),
                                   facecolor="white", framealpha=0.85)
                if legend is not None:
                    legend.set_picker(True)

        try:
            self._layout()
        except Exception:
            pass
        self._highlight_patch = None   # stale after clf(); redrawn by _on_draw
        self._handle_artists = []      # removed by clf() as well
        self._handle_sig = None
        self._rebuild_slider_bar()
        ratio = (m.fig_width_cm / m.fig_height_cm
                 if m.fig_width_cm and m.fig_height_cm else None)
        self.canvas_host.set_ratio(ratio)
        self.canvas.draw_idle()

    def _layout(self):
        """``tight_layout`` -- or, when every subplot is content-only, no margins at all so the
        picture / image fills its cell."""
        subs = self.model.subplots[:self.model.rows * self.model.cols]
        if subs and all(getattr(s, "bare", False) for s in subs):
            self.fig.subplots_adjust(left=0, right=1, bottom=0, top=1, wspace=0.02, hspace=0.02)
        else:
            self.fig.tight_layout()

    @staticmethod
    def _auto_trace_labels(sub, repository) -> tuple[str, str]:
        """Default (x_label, y_label) from the plotted columns.

        x: the shared x column name across the enabled traces (blank if they
        disagree); ``"index"`` for the row index. y: the y column name, but only
        when a single trace is shown (a legend disambiguates the rest).
        """
        x_names, y_names = [], []
        for tref in sub.traces:
            if not tref.enabled:
                continue
            data = repository.get(tref.data_id)
            if data is None or tref.y_col not in data.columns:
                continue
            x_names.append("index" if tref.x_col in ("", INDEX_COL) else tref.x_col)
            y_names.append(tref.y_col)
        x_label = x_names[0] if x_names and len(set(x_names)) == 1 else ""
        y_label = y_names[0] if len(y_names) == 1 else ""
        return x_label, y_label

    @staticmethod
    def _apply_tick_format(ax, axis_name: str, fmt: str, digits) -> None:
        """Tick-label number representation for one axis: display only, independent
        of any data rescaling (TraceRef.scale_factor) or the axis's data aspect."""
        axis_obj = ax.xaxis if axis_name == "x" else ax.yaxis
        try:
            if fmt == "plain":
                ax.ticklabel_format(style="plain", axis=axis_name, useOffset=False)
            elif fmt == "scientific":
                ax.ticklabel_format(style="sci", axis=axis_name, scilimits=(0, 0),
                                    useOffset=False)
            elif fmt == "fixed":
                axis_obj.set_major_formatter(FormatStrFormatter(f"%.{max(0, int(digits))}f"))
            # "auto" (or anything else) -> leave matplotlib's default formatter
        except (AttributeError, ValueError):
            pass   # e.g. ticklabel_format doesn't support a log-scaled axis

    @staticmethod
    def _image_cmap_norm(ref, sl):
        """Colormap + norm for an image/heatmap: optional reversal, and either a continuous
        ``Normalize(vmin, vmax)`` or -- with colour bands set -- a ``BoundaryNorm`` over
        a colormap resampled to one colour per band."""
        try:
            base = mpl.colormaps[ref.cmap]
        except (KeyError, ValueError):
            base = mpl.colormaps["viridis"]
        if ref.cmap_reverse:
            base = base.reversed()
        finite = sl[np.isfinite(sl)] if np.issubdtype(sl.dtype, np.number) else np.array([])
        vmin = ref.vmin if ref.vmin is not None else (float(finite.min()) if finite.size else 0.0)
        vmax = ref.vmax if ref.vmax is not None else (float(finite.max()) if finite.size else 1.0)
        edges = heatmap_band_edges(ref, vmin, vmax)
        if edges is not None:
            n = len(edges) - 1
            return base.resampled(n), mpl.colors.BoundaryNorm(edges, n, clip=True), edges, vmin, vmax
        return base, mpl.colors.Normalize(vmin=vmin, vmax=vmax), None, vmin, vmax

    def _draw_image(self, ax, ref, subplot_index):
        obj = self._images.get(ref.data_id)
        if obj is None or obj.ndim < 2:
            return
        try:
            sl = np.asarray(obj.slice(ref.display_axes, ref.index), dtype=float)
        except Exception:
            return
        nrows, ncols = sl.shape
        r, c = int(ref.display_axes[0]), int(ref.display_axes[1])
        ypos = obj.axis_positions(r) if ref.use_coords else None
        xpos = obj.axis_positions(c) if ref.use_coords else None
        cmap, norm, edges, vmin, vmax = self._image_cmap_norm(ref, sl)

        if xpos is None and ypos is None:
            # plain image: pixel grid, extent 0..n in index units
            im = ax.imshow(sl, cmap=cmap, norm=norm, interpolation=ref.interpolation,
                           origin=ref.origin, aspect=ref.aspect, alpha=ref.alpha,
                           extent=[0, ncols, 0, nrows], zorder=0)
            xc = np.arange(ncols) + 0.5
            yc = (nrows - 0.5 - np.arange(nrows)) if ref.origin == "upper" \
                else np.arange(nrows) + 0.5
            xlab = ylab = None
            tick_x, tick_y = (xc, [str(i) for i in range(ncols)]), \
                (yc, [str(i) for i in range(nrows)])
        else:
            # heatmap: cells sit at their real coordinates (index centres where an axis has none)
            xc, xlab = xpos if xpos is not None else (np.arange(ncols) + 0.5, None)
            yc, ylab = ypos if ypos is not None else (np.arange(nrows) + 0.5, None)
            smooth = ref.interpolation in ("bilinear", "bicubic", "antialiased") \
                and np.isfinite(sl).all()
            im = ax.pcolormesh(xc, yc, np.ma.masked_invalid(sl), cmap=cmap, norm=norm,
                               shading="gouraud" if smooth else "nearest",
                               alpha=ref.alpha, zorder=0)
            ax.set_aspect("equal" if ref.aspect == "equal" else "auto", adjustable="box")
            tick_x, tick_y = (xc, xlab), (yc, ylab)
        self.artist_map[im] = ref
        self._image_axes[ax] = subplot_index
        self._image_geom[ax] = (tick_x, tick_y)

        if ref.iso_show:
            self._draw_isolines(ax, ref, xc, yc, sl, edges, vmin, vmax)
        if ref.colorbar:
            try:
                self.fig.colorbar(im, ax=ax, fraction=0.046, pad=0.02)
            except Exception:
                pass

    def _draw_isolines(self, ax, ref, xc, yc, sl, edges, vmin, vmax):
        finite = sl[np.isfinite(sl)]
        if finite.size < 4 or min(sl.shape) < 2:
            return
        levels = heatmap_iso_levels(ref, vmin if ref.vmin is not None else float(finite.min()),
                                    vmax if ref.vmax is not None else float(finite.max()), edges)
        if not levels:
            return
        try:
            cs = ax.contour(xc, yc, np.ma.masked_invalid(sl), levels=levels,
                            colors=ref.iso_color, linewidths=ref.iso_width,
                            linestyles=ref.iso_style, zorder=1)
            if ref.iso_labels and cs.levels.size:
                ax.clabel(cs, fmt=ref.iso_label_fmt or "%g", fontsize=7)
        except Exception:
            pass

    def _apply_axis_ticks(self, ax, sub):
        """Tick positions / labels, after the axis limits are final: a heatmap's own tick mode
        (every data coordinate, every Nth, custom values; categorical axes default to one tick
        per label), then the subplot's custom value->text mapping on top (any subplot)."""
        ref, geom = sub.image, self._image_geom.get(ax)
        for axis, i in (("x", 0), ("y", 1)):
            res = None
            if ref is not None and geom is not None:
                pos, labels = geom[i]
                mode = getattr(ref, f"{axis}_tick_mode")
                if mode == "auto" and labels:
                    mode = "data"
                res = resolve_ticks(mode, getattr(ref, f"{axis}_tick_values"),
                                    getattr(ref, f"{axis}_tick_every"), pos, labels)
            mapped = apply_tick_labels(getattr(sub, f"{axis}_tick_labels"),
                                       res[0] if res else None, res[1] if res else None)
            res = mapped or res
            if res is None:
                continue
            ticks, names = res
            if axis == "x":
                ax.set_xticks(ticks)
                ax.set_xticklabels(names)
            else:
                ax.set_yticks(ticks)
                ax.set_yticklabels(names)

    def _draw_trace(self, ax, tref, x, y):
        marker = None if tref.marker == "None" else tref.marker
        label = tref.display_label

        if tref.plot_type == "Line":
            (art,) = ax.plot(x, y, label=label, color=tref.color,
                             linestyle=tref.line_style, lw=tref.line_width, alpha=tref.alpha)
            self.artist_map[art] = tref
        elif tref.plot_type == "Scatter":
            art = ax.scatter(x, y, label=label, c=tref.color, edgecolors=tref.edge_color,
                             s=tref.marker_size ** 2, marker=marker or "o", alpha=tref.alpha)
            self.artist_map[art] = tref
        elif tref.plot_type == "Line+Scatter":
            (art,) = ax.plot(x, y, label=label, color=tref.color,
                             linestyle=tref.line_style, lw=tref.line_width,
                             marker=marker or "o", markersize=tref.marker_size,
                             markerfacecolor=tref.color, markeredgecolor=tref.edge_color,
                             alpha=tref.alpha)
            self.artist_map[art] = tref
        elif tref.plot_type == "Step":
            (art,) = ax.step(x, y, label=label, color=tref.color,
                             lw=tref.line_width, alpha=tref.alpha)
            self.artist_map[art] = tref
        elif tref.plot_type == "Bar":
            width = np.min(np.diff(x)) * 0.8 if len(x) > 1 else 0.8
            container = ax.bar(x, y, width=width, label=label, color=tref.color,
                               edgecolor=tref.edge_color, alpha=tref.alpha)
            for art in container:
                self.artist_map[art] = tref

    def _draw_shape(self, ax, sh, z, text_scale=1.0):
        """One :class:`~NoorSuite.model.ShapeRef` as matplotlib artists. Gradient fills are
        an RGBA image clipped to the shape's own outline (so rotation, ellipses and bands
        all work the same way); the outline artist is what the canvas hit-tests."""
        from matplotlib import transforms as mtf
        from matplotlib.image import AxesImage
        from matplotlib.lines import Line2D
        from matplotlib.patches import Ellipse, FancyArrowPatch

        axes_frac = sh.coords == "axes"
        tf = ax.transAxes if axes_frac else ax.transData
        edge = rgba(sh.edge_color, sh.alpha)
        lw = max(0.0, float(sh.line_width))
        l, b, r, t = sh.bounds()

        if sh.kind == "picture":
            self._draw_picture(ax, sh, tf, z)
            return

        if sh.kind == "text":
            label = str(sh.text)
            if "$" in label:        # a half-typed $...$ must not break the whole canvas
                try:
                    from matplotlib.mathtext import MathTextParser
                    MathTextParser("path").parse(label)
                except Exception:
                    label = label.replace("$", r"\$")
            art = ax.text(sh.x0, sh.y0, label, transform=tf, color=edge, ha="center",
                          va="center", rotation=float(sh.angle), zorder=z,
                          fontsize=float(sh.fontsize) * text_scale)
            self.artist_map[art] = sh
            self._shape_artist[id(sh)] = art
            return
        if sh.kind in ("line", "arrow"):
            if sh.kind == "line":
                art = Line2D([sh.x0, sh.x1], [sh.y0, sh.y1], transform=tf, color=edge,
                             lw=lw, linestyle=sh.line_style, zorder=z)
                ax.add_line(art)
            else:
                art = FancyArrowPatch((sh.x0, sh.y0), (sh.x1, sh.y1), transform=tf,
                                      arrowstyle=sh.arrow_head or "->", color=edge, lw=lw,
                                      linestyle=sh.line_style, mutation_scale=8 + 2 * lw,
                                      zorder=z)
                ax.add_patch(art)
            self.artist_map[art] = sh
            self._shape_artist[id(sh)] = art
            return

        # --- closed shapes -------------------------------------------------------
        if sh.kind == "xspan":
            tf = (mtf.blended_transform_factory(ax.transData, ax.transAxes)
                  if not axes_frac else ax.transAxes)
            box = (l, 0.0 if not axes_frac else 0.0, r, 1.0)
        elif sh.kind == "yspan":
            tf = (mtf.blended_transform_factory(ax.transAxes, ax.transData)
                  if not axes_frac else ax.transAxes)
            box = (0.0, b, 1.0, t)
        else:
            box = (l, b, r, t)
        bl, bb, br, bt = box
        w, h = br - bl, bt - bb
        cx, cy = bl + w / 2.0, bb + h / 2.0
        rot = float(sh.angle) if sh.kind in ("rect", "ellipse") else 0.0
        rtf = tf + _DynRot(tf, cx, cy, rot) if rot else tf     # rotation in display space

        fill_rgba = (0, 0, 0, 0)
        gradient = sh.is_filled and sh.fill_mode in ("linear", "radial")
        if sh.is_filled and not gradient:
            fill_rgba = rgba(sh.fill_color, sh.fill_alpha)
        kw = dict(transform=rtf, facecolor=fill_rgba, edgecolor=edge, linewidth=lw,
                  linestyle=sh.line_style, zorder=z)
        if sh.kind == "ellipse":
            patch = Ellipse((cx, cy), w, h, **kw)
        else:
            patch = Rectangle((bl, bb), w, h, **kw)
        ax.add_patch(patch)
        self.artist_map[patch] = sh
        self._shape_artist[id(sh)] = patch

        if gradient and w != 0 and h != 0:
            img = gradient_rgba(sh.grad_stops, sh.fill_mode, sh.grad_angle, sh.grad_cx,
                                sh.grad_cy, sh.grad_radius, reverse=sh.grad_reverse)
            img[..., 3] *= max(0.0, min(1.0, float(sh.fill_alpha)))
            im = AxesImage(ax, origin="lower", interpolation="bilinear",
                           extent=(bl, br, bb, bt), zorder=z - 0.001)
            im.set_data(img)
            im.set_transform(rtf)
            im.set_clip_path(patch)
            ax.add_artist(im)

    def _draw_picture(self, ax, sh, tf, z):
        """An imported figure (SVG / PNG / ...) placed in a box. SVG is rasterised at high
        resolution (it stays vector in LaTeX documents)."""
        from matplotlib.image import AxesImage
        from . import assets as assets_mod
        asset = self.assets.get(sh.asset_id)
        if asset is None:
            return
        arr = assets_mod.raster(asset)
        if arr is None:
            return
        l, b, r, t = sh.bounds()
        ratio = arr.shape[1] / max(1, arr.shape[0])
        if sh.alpha < 1.0:
            arr = arr.copy()
            arr[..., 3] = (arr[..., 3] * float(sh.alpha)).astype(arr.dtype)

        class _Fitted(AxesImage):
            """Re-fits itself into the box at draw time, so the aspect ratio survives
            layout changes (window resize, tight_layout)."""

            def draw(self_, renderer):                      # noqa: N805
                if sh.keep_aspect:
                    try:
                        (px0, py0), (px1, py1) = tf.transform([(l, b), (r, t)])
                        w, h = abs(px1 - px0), abs(py1 - py0)
                        if w > 0 and h > 0:
                            if w / h > ratio:
                                nw, nh = h * ratio, h
                            else:
                                nw, nh = w, w / ratio
                            cx, cy = (px0 + px1) / 2.0, (py0 + py1) / 2.0
                            inv = tf.inverted()
                            (x0, y0), (x1, y1) = inv.transform(
                                [(cx - nw / 2, cy - nh / 2), (cx + nw / 2, cy + nh / 2)])
                            self_.set_extent((min(x0, x1), max(x0, x1),
                                              min(y0, y1), max(y0, y1)))
                    except Exception:
                        pass
                super().draw(renderer)

        xl, yl = ax.get_xlim(), ax.get_ylim()
        im = _Fitted(ax, origin="upper", interpolation="antialiased",
                     extent=(l, r, b, t), zorder=z)
        im.set_data(arr)
        rot = float(sh.angle)
        im.set_transform(tf + _DynRot(tf, (l + r) / 2.0, (b + t) / 2.0, rot) if rot else tf)
        ax.add_artist(im)
        ax.set_xlim(xl)
        ax.set_ylim(yl)
        self.artist_map[im] = sh
        self._shape_artist[id(sh)] = im

    def _draw_error_overlay(self, ax, tref, x, y, yerr):
        """Error-bar whiskers on top of whatever :meth:`_draw_trace` already drew --
        independent of ``plot_type``, so "Scatter + errorbar" and "Line + errorbar" are
        both just this on top of their normal trace (see TraceRef.show_errorbar).
        ``fmt="none"`` so this call draws only the whiskers, not another line/marker."""
        container = ax.errorbar(
            x, y, yerr=yerr, fmt="none", ecolor=tref.color,
            elinewidth=max(0.6, tref.line_width * 0.6), capsize=3, alpha=tref.alpha,
            zorder=2.5)
        for lc in container.lines[2]:
            self.artist_map[lc] = tref

    def _on_canvas_resize(self, event):
        """Re-run tight_layout() whenever the canvas actually changes pixel size --
        letterboxing (fixed figure aspect), a splitter drag, undocking, maximizing, or
        just resizing the window all resize the underlying FigureCanvasQTAgg without
        recomputing the subplot layout on their own. Without this, the axes rect stays
        at the fraction-of-figure tight_layout() picked for the *previous* size, so
        labels/ticks sized for that size can end up clipped (invisible) at the new one
        -- toggling something that forces a full render() (e.g. border width) was the
        only thing that "fixed" it, because render() itself ends with tight_layout()."""
        self._sync_zoom_dpi(draw=False)
        try:
            self._layout()
        except Exception:
            pass
        self.canvas.draw_idle()

    def _sync_zoom_dpi(self, draw=True):
        """Keep the figure's size in *inches* constant while the canvas is zoomed: the
        canvas grows by ``zoom`` in pixels, so the figure dpi grows by ``zoom`` too and
        everything (text, lines) scales with it instead of just adding empty space."""
        dpr = float(getattr(self.canvas, "device_pixel_ratio", 1.0) or 1.0)
        target = 100.0 * self.canvas_host.zoom * dpr
        if abs(self.fig.dpi - target) < 1e-6:
            return
        self.fig.dpi = target
        w_px, h_px = self.canvas.width() * dpr, self.canvas.height() * dpr
        if w_px > 0 and h_px > 0:
            self.fig.set_size_inches(w_px / target, h_px / target, forward=False)
        if draw:
            try:
                self._layout()
            except Exception:
                pass
            self.canvas.draw_idle()

    def savefig_export(self, dest, fmt: str, size_in=None, dpi=300):
        """``savefig`` for an export / clipboard copy.

        * the dashed active-subplot cue is hidden first (it is on-screen UI, not plot);
        * ``size_in=None`` -> WYSIWYG: the figure's current size and layout, no
          ``bbox_inches="tight"`` re-crop, so proportions match the display exactly;
        * ``size_in=(w, h)`` inches -> that exact size, with the layout recomputed for it.
        The figure is restored afterwards."""
        self._exporting = True
        patch = self._highlight_patch
        old_size = self.fig.get_size_inches().copy()
        try:
            if patch is not None:
                patch.set_visible(False)
            for a in self._handle_artists:
                a.set_visible(False)
            if size_in:
                self.fig.set_size_inches(*size_in, forward=False)
                try:
                    self._layout()
                except Exception:
                    pass
            self.fig.savefig(dest, format=fmt, dpi=dpi,
                             facecolor=self.fig.get_facecolor(),
                             edgecolor=self.fig.patch.get_edgecolor())
        finally:
            if patch is not None:
                patch.set_visible(True)
            for a in self._handle_artists:
                a.set_visible(True)
            if size_in:
                self.fig.set_size_inches(*old_size, forward=False)
                try:
                    self._layout()
                except Exception:
                    pass
            self._exporting = False
            self.canvas.draw_idle()

    # ----------------------------------------------------- active-subplot cue
    def _on_draw(self, event):
        """Reposition the highlight rectangle after every draw (needs a renderer)."""
        if self._in_highlight or self._exporting:
            return
        renderer = getattr(event, "renderer", None)
        self._in_highlight = True
        try:
            changed = self._place_active_highlight(renderer)
            changed = self._place_shape_handles(renderer) or changed
        finally:
            self._in_highlight = False
        if changed:
            self.canvas.draw_idle()

    def _place_active_highlight(self, renderer=None) -> bool:
        if not self._axes:
            return False
        idx = min(self.model.active_index, len(self._axes) - 1)
        ax = self._axes[idx]
        try:
            bbox = ax.get_tightbbox(renderer)
            if bbox is None:
                return False
            fx = self.fig.transFigure.inverted().transform(bbox.get_points())
        except Exception:
            return False
        (x0, y0), (x1, y1) = fx
        w_in, h_in = self.fig.get_size_inches()
        pad_x = _HL_PAD_PX / (w_in * self.fig.dpi)
        pad_y = _HL_PAD_PX / (h_in * self.fig.dpi)
        x0, y0 = x0 - pad_x, y0 - pad_y
        w, h = (x1 - x0) + pad_x, (y1 - y0) + pad_y

        target = (round(x0, 4), round(y0, 4), round(w, 4), round(h, 4))
        if self._highlight_patch is not None:
            p = self._highlight_patch
            cur = (round(p.get_x(), 4), round(p.get_y(), 4),
                   round(p.get_width(), 4), round(p.get_height(), 4))
            if cur == target:
                return False
            p.remove()

        patch = Rectangle((x0, y0), w, h, transform=self.fig.transFigure,
                          fill=False, edgecolor=_ACTIVE_ACCENT, linewidth=1.4,
                          linestyle="--", zorder=1_000_000, clip_on=False)
        patch.set_in_layout(False)
        self.fig.add_artist(patch)
        self._highlight_patch = patch
        return True

    # ------------------------------------------------- object handles (move/scale/rotate)
    def set_selected_shape(self, ref):
        """Show the transform handles of ``ref`` (a ShapeRef of this sheet, or ``None``)."""
        self._sel_shape = ref
        self._handle_sig = None
        self.canvas.draw_idle()

    def _shape_context(self, sh):
        """(axes, subplot) holding ``sh`` and the transform its coordinates live in."""
        for i, sub in enumerate(self.model.subplots[:len(self._axes)]):
            if any(s is sh for s in sub.shapes):
                return self._axes[i], sub
        return None, None

    @staticmethod
    def _coord_tf(ax, sh):
        return ax.transAxes if sh.coords == "axes" else ax.transData

    def shape_display_fns(self, sh):
        """``(fwd, inv)``: the shape's coordinates <-> display pixels (for group moves)."""
        ax, _ = self._shape_context(sh)
        if ax is None:
            return None, None
        tf = self._coord_tf(ax, sh)
        inv = tf.inverted()
        return (lambda p: tuple(tf.transform((p[0], p[1])))), \
               (lambda p: tuple(inv.transform((p[0], p[1]))))

    def _handles_for(self, sh):
        """``{name: (x_px, y_px)}`` handle positions of ``sh`` for the current layout."""
        import math
        ax, _ = self._shape_context(sh)
        if ax is None or not sh.enabled:
            return {}
        tf = self._coord_tf(ax, sh)
        k = sh.kind
        if k in ("line", "arrow"):
            return {"p0": tuple(tf.transform((sh.x0, sh.y0))),
                    "p1": tuple(tf.transform((sh.x1, sh.y1)))}
        if k == "xspan":
            l, _, r, _ = sh.bounds()
            mid = ax.transAxes.transform((0, 0.5))[1]
            fx = (lambda x: ax.transAxes.transform((x, 0))[0]) if sh.coords == "axes" \
                else (lambda x: ax.transData.transform((x, 0))[0])
            return {"left": (fx(l), mid), "right": (fx(r), mid)}
        if k == "yspan":
            _, b, _, t = sh.bounds()
            mid = ax.transAxes.transform((0.5, 0))[0]
            fy = (lambda y: ax.transAxes.transform((0, y))[1]) if sh.coords == "axes" \
                else (lambda y: ax.transData.transform((0, y))[1])
            return {"bottom": (mid, fy(b)), "top": (mid, fy(t))}
        l, b, r, t = sh.bounds()
        cd = tf.transform(((l + r) / 2.0, (b + t) / 2.0))
        a = math.radians(float(sh.angle))
        ca, sa = math.cos(a), math.sin(a)

        def rot(p):
            dx, dy = p[0] - cd[0], p[1] - cd[1]
            return (cd[0] + dx * ca - dy * sa, cd[1] + dx * sa + dy * ca)

        if k == "text":
            top = (cd[0], cd[1] + 28)
            return {"rotate": rot(top)}
        out = {"c_bl": rot(tf.transform((l, b))), "c_br": rot(tf.transform((r, b))),
               "c_tr": rot(tf.transform((r, t))), "c_tl": rot(tf.transform((l, t)))}
        tm = tf.transform(((l + r) / 2.0, t))
        out["rotate"] = rot((tm[0], tm[1] + 24))
        return out

    def _place_shape_handles(self, renderer=None) -> bool:
        sh = self._sel_shape
        pts = self._handles_for(sh) if sh is not None else {}
        sig = tuple((n, round(p[0]), round(p[1])) for n, p in sorted(pts.items()))
        if sig == self._handle_sig:
            return False
        self._handle_sig = sig
        for a in self._handle_artists:
            try:
                a.remove()
            except Exception:
                pass
        self._handle_artists = []
        self._handle_pts = pts
        if not pts:
            return True
        from matplotlib.lines import Line2D
        from matplotlib.transforms import IdentityTransform
        ident = IdentityTransform()
        corners = [pts[n] for n in ("c_bl", "c_br", "c_tr", "c_tl") if n in pts]
        if len(corners) == 4:
            xs = [p[0] for p in corners] + [corners[0][0]]
            ys = [p[1] for p in corners] + [corners[0][1]]
            outline = Line2D(xs, ys, transform=ident, color=_ACTIVE_ACCENT, lw=1.0,
                             linestyle="--", zorder=2_000_000)
            outline.set_in_layout(False)
            self.fig.add_artist(outline)
            self._handle_artists.append(outline)
            if "rotate" in pts:
                tm = ((corners[2][0] + corners[3][0]) / 2, (corners[2][1] + corners[3][1]) / 2)
                stem = Line2D([tm[0], pts["rotate"][0]], [tm[1], pts["rotate"][1]],
                              transform=ident, color=_ACTIVE_ACCENT, lw=1.0, zorder=2_000_000)
                stem.set_in_layout(False)
                self.fig.add_artist(stem)
                self._handle_artists.append(stem)
        for name, (x, y) in pts.items():
            dot = Line2D([x], [y], transform=ident, marker="o" if name == "rotate" else "s",
                         markersize=7, markerfacecolor="white", markeredgecolor=_ACTIVE_ACCENT,
                         markeredgewidth=1.4, linestyle="none", zorder=2_000_001)
            dot.set_in_layout(False)
            self.fig.add_artist(dot)
            self._handle_artists.append(dot)
        return True

    def _handle_at(self, event):
        if self._sel_shape is None:
            return None
        best, bd = None, 10.0
        for name, (x, y) in self._handle_pts.items():
            d = ((x - event.x) ** 2 + (y - event.y) ** 2) ** 0.5
            if d < bd:
                best, bd = name, d
        return best

    def _shape_at(self, event):
        """Top-most object under the pointer in the clicked subplot (None if none)."""
        if event.inaxes is None or event.inaxes not in self._axes:
            return None
        sub = self.model.subplots[self._axes.index(event.inaxes)]
        stack = sub.stack()
        if self._sel_shape is not None and any(s is self._sel_shape for s in sub.shapes):
            stack = [self._sel_shape] + [s for s in reversed(stack) if s is not self._sel_shape]
        else:
            stack = list(reversed(stack))
        for it in stack:
            if getattr(it, "kind", None) is None or not hasattr(it, "shape_id") or not it.enabled:
                continue
            art = self._shape_artist.get(id(it))
            if art is not None and _contains(art, event):
                return it
        return None

    def _begin_drag(self, sh, mode, handle, event):
        import math
        ax, sub = self._shape_context(sh)
        if ax is None:
            return
        tf = self._coord_tf(ax, sh)
        l, b, r, t = sh.bounds()
        cd = tf.transform(((l + r) / 2.0, (b + t) / 2.0))
        corners = [tf.transform(p) for p in ((l, b), (r, b), (r, t), (l, t))]
        hw = abs(corners[1][0] - corners[0][0]) / 2.0
        hh = abs(corners[3][1] - corners[0][1]) / 2.0
        self._drag = {
            "sh": sh, "mode": mode, "handle": handle, "ax": ax, "tf": tf,
            "start": sh.to_dict(), "geom0": sh.geom(), "cd": tuple(cd), "hw": max(hw, 1e-6),
            "hh": max(hh, 1e-6), "m0": (event.x, event.y), "moved": False,
            "kids": [(c, c.to_dict()) for c in sub.descendants(sh)],
            "uniform": sh.kind == "picture" and bool(sh.keep_aspect),
        }

    def _restore(self, sh, d):
        for f, v in d.items():
            if f in ("grad_stops",):
                v = [list(x) for x in v]
            setattr(sh, f, v)

    def _on_canvas_motion(self, event):
        g = self._drag
        if g is None or event.x is None:
            return
        import math
        import time
        from .model import apply_group_transform
        sh, tf = g["sh"], g["tf"]
        dx, dy = event.x - g["m0"][0], event.y - g["m0"][1]
        if not g["moved"] and (abs(dx) + abs(dy)) < 3:
            return
        g["moved"] = True
        self._restore(sh, g["start"])
        inv = tf.inverted()
        mode, k = g["mode"], sh.kind
        if mode == "move":
            c0 = g["cd"]
            n = inv.transform((c0[0] + dx, c0[1] + dy))
            o = inv.transform(c0)
            ddx, ddy = float(n[0] - o[0]), float(n[1] - o[1])
            if k != "yspan":
                sh.x0 += ddx
                sh.x1 += ddx
            if k != "xspan":
                sh.y0 += ddy
                sh.y1 += ddy
        elif mode == "rotate":
            c = g["cd"]
            ang = math.degrees(math.atan2(event.y - c[1], event.x - c[0])) - 90.0
            if getattr(event, "key", None) == "shift":
                ang = round(ang / 15.0) * 15.0
            sh.angle = ((ang + 180.0) % 360.0) - 180.0
        elif mode == "point":
            p = inv.transform((event.x, event.y))
            nm = g["handle"]
            if nm == "p0":
                sh.x0, sh.y0 = float(p[0]), float(p[1])
            elif nm == "p1":
                sh.x1, sh.y1 = float(p[0]), float(p[1])
            elif nm == "left":
                sh.x0 = float(p[0]) if sh.x0 <= sh.x1 else sh.x0
                sh.x1 = float(p[0]) if sh.x0 > sh.x1 else sh.x1
            elif nm == "right":
                if sh.x1 >= sh.x0:
                    sh.x1 = float(p[0])
                else:
                    sh.x0 = float(p[0])
            elif nm == "bottom":
                if sh.y0 <= sh.y1:
                    sh.y0 = float(p[1])
                else:
                    sh.y1 = float(p[1])
            elif nm == "top":
                if sh.y1 >= sh.y0:
                    sh.y1 = float(p[1])
                else:
                    sh.y0 = float(p[1])
        elif mode == "scale":
            c = g["cd"]
            a = math.radians(float(sh.angle))
            ca, sa = math.cos(-a), math.sin(-a)
            mx, my = event.x - c[0], event.y - c[1]
            lx, ly = mx * ca - my * sa, mx * sa + my * ca            # pointer, unrotated frame
            sgx = 1.0 if g["handle"] in ("c_br", "c_tr") else -1.0
            sgy = 1.0 if g["handle"] in ("c_tl", "c_tr") else -1.0
            ax_, ay_ = -sgx * g["hw"], -sgy * g["hh"]               # the fixed (opposite) corner
            nhw = max(3.0, abs(lx - ax_) / 2.0)
            nhh = max(3.0, abs(ly - ay_) / 2.0)
            if g["uniform"] or getattr(event, "key", None) == "shift":
                f = max(nhw / g["hw"], nhh / g["hh"])
                nhw, nhh = g["hw"] * f, g["hh"] * f
            ox, oy = (ax_ + sgx * nhw), (ay_ + sgy * nhh)          # new centre, unrotated frame
            cxn = c[0] + ox * math.cos(a) - oy * math.sin(a)
            cyn = c[1] + ox * math.sin(a) + oy * math.cos(a)
            nc = inv.transform((cxn, cyn))
            w0 = abs(sh.x1 - sh.x0)
            h0 = abs(sh.y1 - sh.y0)
            sh.scale_x = float(g["start"]["scale_x"]) * nhw / g["hw"]
            sh.scale_y = float(g["start"]["scale_y"]) * nhh / g["hh"]
            sh.x0, sh.x1 = float(nc[0]) - w0 / 2.0, float(nc[0]) + w0 / 2.0
            sh.y0, sh.y1 = float(nc[1]) - h0 / 2.0, float(nc[1]) + h0 / 2.0
        # the children follow their root
        if g["kids"]:
            fwd = lambda p: tuple(tf.transform((p[0], p[1])))             # noqa: E731
            bwd = lambda p: tuple(inv.transform((p[0], p[1])))            # noqa: E731
            new = sh.geom()
            for child, d0 in g["kids"]:
                self._restore(child, d0)
                apply_group_transform(child, g["geom0"], new, fwd, bwd)
        now = time.monotonic()
        if now - self._last_drag_render > 0.04:
            self._last_drag_render = now
            self._redraw_after_edit()

    def _redraw_after_edit(self):
        if getattr(self, "_repository", None) is not None:
            self.render(self._repository, self._images)
            self.canvas.draw_idle()

    def _on_canvas_release(self, event):
        g, self._drag = self._drag, None
        if g is None:
            return
        if g["moved"]:
            self._redraw_after_edit()
            self.shape_edited.emit(g["sh"])

    # -------------------------------------------------------------- interaction
    def on_canvas_click(self, event):
        if getattr(event, "button", None) == 1 and not getattr(event, "dblclick", False) \
                and getattr(self.toolbar, "mode", "") in ("", None):
            hname = self._handle_at(event)
            if hname is not None and self._sel_shape is not None:
                mode = "rotate" if hname == "rotate" else \
                    "scale" if hname.startswith("c_") else "point"
                self._begin_drag(self._sel_shape, mode, hname, event)
                return
            hit_shape = self._shape_at(event)
            if hit_shape is not None:
                if event.inaxes in self._axes:
                    self.model.active_index = self._axes.index(event.inaxes)
                    self.active_subplot_changed.emit(self.model.active_index)
                self.shape_selected.emit(hit_shape)
                self._sel_shape = hit_shape
                self._handle_sig = None
                self._begin_drag(hit_shape, "move", None, event)
                self.canvas.draw_idle()
                return
        if event.inaxes is not None and event.inaxes in self._axes:
            self.model.active_index = self._axes.index(event.inaxes)
            self.active_subplot_changed.emit(self.model.active_index)
            self._rebuild_slider_bar()
            self.canvas.draw_idle()

        if getattr(event, "dblclick", False):
            self.element_double_clicked.emit(self._hit_test(event))
        elif getattr(event, "button", None) == 3:      # right-click: e.g. delete a trace
            self.element_right_clicked.emit(self._hit_test(event), getattr(event, "guiEvent", None))

    def _hit_test(self, event) -> dict:
        for art, ref in self.artist_map.items():
            if isinstance(ref, ImageRef):
                continue
            if _contains(art, event):
                if isinstance(ref, ShapeRef):
                    return {"kind": "shape", "ref": ref}
                return {"kind": "trace", "ref": ref}

        for text_art, kind, idx in self._text_targets:
            if text_art.get_text() and _contains(text_art, event):
                return {"kind": kind, "subplot_index": idx}

        for i, ax in enumerate(self._axes):
            legend = ax.get_legend()
            if legend is not None and _contains(legend, event):
                return {"kind": "legend", "subplot_index": i}

        if event.inaxes is not None and event.inaxes in self._image_axes:
            return {"kind": "image", "subplot_index": self._image_axes[event.inaxes]}

        if event.inaxes is not None and event.inaxes in self._axes:
            return {"kind": "axes", "subplot_index": self._axes.index(event.inaxes)}
        return {"kind": "figure"}


def _contains(artist, event) -> bool:
    try:
        hit, _ = artist.contains(event)
        return bool(hit)
    except Exception:
        return False


def export_sheet_pdf(sm, repository, images, path, default_cm=None, assets=None):
    """Render a sheet model as a vector PDF at its own figure size (``latex.DEFAULT_FIG_CM``
    when it has none), on a throw-away copy -- open tabs / zoom are untouched. Needs a
    ``QApplication``; works offscreen."""
    import matplotlib
    from .latex import DEFAULT_FIG_CM
    from .model import duplicate_sheet_model
    sm = duplicate_sheet_model(sm)
    if not (sm.fig_width_cm and sm.fig_height_cm):
        sm.fig_width_cm, sm.fig_height_cm = default_cm or DEFAULT_FIG_CM
    ps = PlotSheet(sm)
    ps.assets = assets or {}
    try:
        ps.render(repository, images)
        with matplotlib.rc_context({"pdf.fonttype": 42}):          # real, embeddable text
            ps.savefig_export(path, "pdf",
                              size_in=(sm.fig_width_cm / 2.54, sm.fig_height_cm / 2.54))
    finally:
        ps.deleteLater()


def export_sheet_raster(sm, repository, images, path, dpi=300, assets=None):
    """A PNG of a sheet at its own figure size (``latex.DEFAULT_FIG_CM`` when unset) and
    ``dpi`` -- what Word / LibreOffice documents embed."""
    from .latex import DEFAULT_FIG_CM
    w_cm = sm.fig_width_cm if (sm.fig_width_cm and sm.fig_height_cm) else DEFAULT_FIG_CM[0]
    export_sheet_png(sm, repository, images, path, width_px=int(w_cm / 2.54 * dpi),
                     assets=assets)


def export_sheet_png(sm, repository, images, path, width_px=360, assets=None):
    """A small PNG of a sheet (figure browser thumbnails), at the document figure size."""
    from .latex import DEFAULT_FIG_CM
    from .model import duplicate_sheet_model
    sm = duplicate_sheet_model(sm)
    if not (sm.fig_width_cm and sm.fig_height_cm):
        sm.fig_width_cm, sm.fig_height_cm = DEFAULT_FIG_CM
    ps = PlotSheet(sm)
    ps.assets = assets or {}
    try:
        ps.render(repository, images)
        w_in, h_in = sm.fig_width_cm / 2.54, sm.fig_height_cm / 2.54
        ps.savefig_export(path, "png", size_in=(w_in, h_in), dpi=max(20, width_px / w_in))
    finally:
        ps.deleteLater()
