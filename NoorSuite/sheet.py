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
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from .model import SheetModel

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

    def __init__(self, model: SheetModel | None = None, parent=None):
        super().__init__(parent)
        self.model = model or SheetModel()

        self.fig = Figure(figsize=(8, 6), dpi=100)
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        self.canvas.mpl_connect("button_press_event", self.on_canvas_click)
        self.canvas.mpl_connect("draw_event", self._on_draw)

        # Render-pass bookkeeping for hit-testing.
        self.artist_map = {}          # Artist -> TraceRef
        self._axes = []               # Axes in subplot order
        self._text_targets = []       # (Text, kind, subplot_index)
        self._highlight_patch = None  # figure-level Rectangle around the active subplot
        self._in_highlight = False    # reentrancy guard for the draw_event handler

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.canvas)

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
    def render(self, repository: dict):
        m = self.model
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
            ax.grid(sub.show_grid, linestyle="--", alpha=0.5)

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
        self.canvas.draw_idle()

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
            self.canvas.draw_idle()

        if getattr(event, "dblclick", False):
            self.element_double_clicked.emit(self._hit_test(event))

    def _hit_test(self, event) -> dict:
        for art, tref in self.artist_map.items():
            if _contains(art, event):
                return {"kind": "trace", "ref": tref}

        for text_art, kind, idx in self._text_targets:
            if text_art.get_text() and _contains(text_art, event):
                return {"kind": kind, "subplot_index": idx}

        for i, ax in enumerate(self._axes):
            legend = ax.get_legend()
            if legend is not None and _contains(legend, event):
                return {"kind": "legend", "subplot_index": i}

        if event.inaxes is not None and event.inaxes in self._axes:
            return {"kind": "axes", "subplot_index": self._axes.index(event.inaxes)}
        return {"kind": "figure"}


def _contains(artist, event) -> bool:
    try:
        hit, _ = artist.contains(event)
        return bool(hit)
    except Exception:
        return False
