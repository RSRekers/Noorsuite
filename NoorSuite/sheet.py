"""The interactive plot sheet widget: a Figure + canvas holding an NxM grid of subplots.

Rendering is full-clear-and-rebuild (:meth:`PlotSheet.render`). Every render pass
repopulates :attr:`PlotSheet.artist_map` (matplotlib ``Artist`` -> :class:`~NoorSuite.model.TraceRef`)
plus the per-subplot text / legend handles, so :meth:`PlotSheet.on_canvas_click` can
hit-test a double click and report which element was hit via the ``element_double_clicked``
signal.
"""
from __future__ import annotations

import numpy as np
from matplotlib.backends.backend_qtagg import (FigureCanvasQTAgg,
                                               NavigationToolbar2QT)
from matplotlib.colors import to_rgb
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QPlainTextEdit,
                             QSlider, QSplitter, QVBoxLayout, QWidget)

from .model import ImageRef, SheetModel
from .widgets import CollapsibleSection

_ACTIVE_ACCENT = "#ff7f0e"
_HL_PAD_PX = 6.0


def rgba(color, alpha):
    try:
        r, g, b = to_rgb(color)
    except (ValueError, TypeError):
        r, g, b = 1.0, 1.0, 1.0
    return (r, g, b, float(alpha))


class PlotSheet(QWidget):
    active_subplot_changed = pyqtSignal(int)
    element_double_clicked = pyqtSignal(object)   # dict: {"kind": ..., ...}
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
        self.canvas.mpl_connect("draw_event", self._on_draw)

        # Render-pass bookkeeping for hit-testing.
        self.artist_map = {}          # Artist -> TraceRef / ImageRef
        self._axes = []               # Axes in subplot order
        self._text_targets = []       # (Text, kind, subplot_index)
        self._image_axes = {}         # Axes -> subplot_index (has an image)
        self._highlight_patch = None  # figure-level Rectangle around the active subplot
        self._in_highlight = False    # reentrancy guard for the draw_event handler

        plot_page = QWidget()
        plot_layout = QVBoxLayout(plot_page)
        plot_layout.setContentsMargins(0, 0, 0, 0)
        plot_layout.addWidget(self.toolbar)
        plot_layout.addWidget(self.canvas)

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
        self._axes = []
        self._text_targets = []
        self._image_axes = {}

        rows, cols = m.rows, m.cols
        for idx in range(rows * cols):
            sub = m.subplots[idx]
            ax = self.fig.add_subplot(rows, cols, idx + 1)
            self._axes.append(ax)

            ax.set_facecolor(rgba(sub.face_color, sub.face_alpha))
            ax.tick_params(labelsize=sub.tick_label_size)
            visible_spine = sub.spine_width > 0
            for spine in ax.spines.values():
                spine.set_visible(visible_spine)
                spine.set_edgecolor(sub.spine_color)
                spine.set_linewidth(sub.spine_width)
                try:
                    spine.set_linestyle(sub.spine_style)
                except Exception:
                    pass

            title = ax.set_title(sub.title, fontsize=sub.title_fontsize)
            xlab = ax.set_xlabel(sub.x_label, fontsize=sub.xlabel_fontsize)
            ylab = ax.set_ylabel(sub.y_label, fontsize=sub.ylabel_fontsize)
            self._text_targets += [(title, "title", idx),
                                   (xlab, "xlabel", idx),
                                   (ylab, "ylabel", idx)]

            for axis_name, scale in (("x", sub.x_scale), ("y", sub.y_scale)):
                try:
                    getattr(ax, f"set_{axis_name}scale")(scale)
                except Exception:
                    pass

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
            for tref in sub.traces:
                if not tref.enabled:
                    continue
                data = repository.get(tref.data_id)
                if data is None or tref.y_col not in data.columns:
                    continue
                try:
                    x, y = tref.resolve(data)
                except Exception:
                    continue
                self._draw_trace(ax, tref, x, y)
                n_drawn += 1

            if sub.x_min is not None or sub.x_max is not None:
                ax.set_xlim(left=sub.x_min, right=sub.x_max)
            if sub.y_min is not None or sub.y_max is not None:
                ax.set_ylim(bottom=sub.y_min, top=sub.y_max)

            if n_drawn and sub.legend_visible:
                legend = ax.legend(loc=sub.legend_loc, frameon=sub.legend_frame,
                                   fontsize=sub.legend_fontsize,
                                   ncol=max(1, int(sub.legend_ncol)),
                                   facecolor="white", framealpha=0.85)
                if legend is not None:
                    legend.set_picker(True)

        try:
            self.fig.tight_layout()
        except Exception:
            pass
        self._highlight_patch = None   # stale after clf(); redrawn by _on_draw
        self._rebuild_slider_bar()
        self.canvas.draw_idle()

    def _draw_image(self, ax, ref, subplot_index):
        obj = self._images.get(ref.data_id)
        if obj is None or obj.ndim < 2:
            return
        try:
            sl = obj.slice(ref.display_axes, ref.index)
        except Exception:
            return
        nrows, ncols = sl.shape
        im = ax.imshow(sl, cmap=ref.cmap, vmin=ref.vmin, vmax=ref.vmax,
                       interpolation=ref.interpolation, origin=ref.origin,
                       aspect=ref.aspect, alpha=ref.alpha,
                       extent=[0, ncols, 0, nrows], zorder=0)
        self.artist_map[im] = ref
        self._image_axes[ax] = subplot_index
        if ref.colorbar:
            try:
                self.fig.colorbar(im, ax=ax, fraction=0.046, pad=0.02)
            except Exception:
                pass

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

    # ----------------------------------------------------- active-subplot cue
    def _on_draw(self, event):
        """Reposition the highlight rectangle after every draw (needs a renderer)."""
        if self._in_highlight:
            return
        renderer = getattr(event, "renderer", None)
        self._in_highlight = True
        try:
            changed = self._place_active_highlight(renderer)
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

    # -------------------------------------------------------------- interaction
    def on_canvas_click(self, event):
        if event.inaxes is not None and event.inaxes in self._axes:
            self.model.active_index = self._axes.index(event.inaxes)
            self.active_subplot_changed.emit(self.model.active_index)
            self._rebuild_slider_bar()
            self.canvas.draw_idle()

        if getattr(event, "dblclick", False):
            self.element_double_clicked.emit(self._hit_test(event))

    def _hit_test(self, event) -> dict:
        for art, ref in self.artist_map.items():
            if isinstance(ref, ImageRef):
                continue
            if _contains(art, event):
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
