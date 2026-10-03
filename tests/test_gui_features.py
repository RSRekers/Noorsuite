"""Headless GUI checks (skipped without PyQt6): zoom, export, duplicate, plot options."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")
pytest.importorskip("PyQt6")

from io import BytesIO

from PyQt6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(app, monkeypatch):
    from NoorSuite import app as appmod
    # never touch the real ~/.scisuite_session.json (the user's live session)
    monkeypatch.setattr(appmod.SciSuiteWindow, "load_session", lambda self: None)
    monkeypatch.setattr(appmod.SciSuiteWindow, "save_session", lambda self: None)
    w = appmod.SciSuiteWindow(port=55590 + os.getpid() % 300)
    w._autosave_timer.stop()
    yield w
    w.ipc.stop()


def _plot(win, **extra):
    win.handle_incoming_ipc({"action": "append_dataframe", "name": "d",
                             "columns": {"x": [3., 1., 2.], "y": [9., 1., 4.]},
                             "column_order": ["x", "y"]})
    win.handle_incoming_ipc({"action": "add_to_sheet", "data_name": "d", "x_col": "x",
                             "y_cols": ["y"], "sheet": "__new__", "subplot_index": 0, **extra})
    return next(reversed(win.sheets.values()))


def test_plot_options_reach_the_trace(win):
    sm = _plot(win, trace_style={"plot_type": "Step", "sort_x": True, "color": "#123456"})
    t = sm.subplots[0].traces[0]
    assert (t.plot_type, t.sort_x, t.color) == ("Step", True, "#123456")


def test_duplicate_sheet_and_folder(win):
    sm = _plot(win, organize={"name": "Orig", "folder": "F"})
    folder = next(win.tree.topLevelItem(i) for i in range(win.tree.topLevelItemCount())
                  if win.tree.topLevelItem(i).text(0) == "F")
    sheet_item = folder.child(0)
    n = len(win.sheets)
    new = win._duplicate_tree_item(sheet_item)
    assert len(win.sheets) == n + 1 and new.text(0) == "Orig (copy)"
    assert folder.indexOfChild(new) == 1
    assert win.sheets[new.data(0, 0x100 + 2)].subplots[0].traces[0].data_id == \
        sm.subplots[0].traces[0].data_id
    fcopy = win._duplicate_tree_item(folder)
    assert fcopy.text(0) == "F (copy)" and fcopy.childCount() == 2
    assert len(win.sheets) == n + 1 + 2
    ids = {fcopy.child(i).data(0, 0x100 + 2) for i in range(2)}
    assert ids.isdisjoint({sheet_item.data(0, 0x100 + 2), new.data(0, 0x100 + 2)})


def test_zoom_keeps_inches_and_scales_dpi(win):
    sm = _plot(win)
    ps = win._open_sheet_tab(sm.sheet_id)
    win.show()
    QApplication.processEvents()
    inches = tuple(ps.fig.get_size_inches())
    base_dpi = ps.fig.dpi
    ps.canvas_host.set_zoom(2.0)
    QApplication.processEvents()
    assert ps.fig.dpi == pytest.approx(base_dpi * 2)
    assert tuple(ps.fig.get_size_inches()) == pytest.approx(inches, rel=0.02)
    ps.canvas_host.set_zoom(1.0)
    QApplication.processEvents()
    assert ps.fig.dpi == pytest.approx(base_dpi)


def test_clipboard_export_matches_display_proportions(win):
    from PIL import Image
    sm = _plot(win)
    ps = win._open_sheet_tab(sm.sheet_id)
    win.show()
    QApplication.processEvents()
    ps.render(win.repository, win.images)
    QApplication.processEvents()
    w_in, h_in = ps.fig.get_size_inches()
    buf = BytesIO()
    win._savefig_at_export_size(ps, buf, "png", wysiwyg=True)
    w, h = Image.open(BytesIO(buf.getvalue())).size
    assert (w, h) == (round(w_in * 300), round(h_in * 300))      # no tight re-crop
    assert tuple(ps.fig.get_size_inches()) == (w_in, h_in)       # figure restored


def test_ctrl_wheel_zooms_and_ctrl_0_resets(win):
    from PyQt6.QtCore import QPoint, QPointF, Qt
    from PyQt6.QtGui import QWheelEvent
    sm = _plot(win)
    ps = win._open_sheet_tab(sm.sheet_id)
    win.show()
    QApplication.processEvents()

    def wheel(mod):
        ev = QWheelEvent(QPointF(10, 10), QPointF(10, 10), QPoint(0, 0), QPoint(0, 120),
                         Qt.MouseButton.NoButton, mod, Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(ps.canvas, ev)

    wheel(Qt.KeyboardModifier.NoModifier)
    assert ps.canvas_host.zoom == 1.0                       # plain wheel does not zoom
    wheel(Qt.KeyboardModifier.ControlModifier)
    assert ps.canvas_host.zoom == pytest.approx(1.15)
    ps.canvas_host.set_zoom(1.0)
    assert ps.canvas_host.zoom == 1.0


def _heatmap_sheet(win, **style):
    import numpy as np
    z = np.add.outer(np.linspace(0, 10, 6), np.linspace(0, 40, 5))      # 6 rows (y) x 5 cols (x)
    win.handle_incoming_ipc({
        "action": "append_image", "name": "hm", "bytes": z.tobytes(), "shape": list(z.shape),
        "dtype": str(z.dtype), "axis_names": ["T", "f"],
        "axis_coords": {0: [100, 200, 300, 400, 500, 600], 1: [1, 2, 4, 8, 16]}})
    win.handle_incoming_ipc({"action": "add_image_to_sheet", "name": "hm", "sheet": "__new__",
                             "subplot_index": 0, "display_axes": [0, 1], "image_style": style})
    sm = next(reversed(win.sheets.values()))
    ps = win._open_sheet_tab(sm.sheet_id)
    win.show()
    QApplication.processEvents()
    return sm, ps


def test_heatmap_renders_at_coordinates_with_bands_isolines_and_ticks(win):
    from matplotlib.collections import QuadMesh
    from matplotlib.colors import BoundaryNorm
    sm, ps = _heatmap_sheet(win, cmap_bins=5, iso_show=True, iso_above=20, colorbar=True,
                            x_tick_mode="data", x_tick_every=2,
                            y_tick_mode="custom", y_tick_values="100, 300")
    ax = ps.fig.axes[0]
    mesh = next(c for c in ax.collections if isinstance(c, QuadMesh))
    assert isinstance(mesh.norm, BoundaryNorm) and len(mesh.norm.boundaries) == 6
    x0, x1 = ax.get_xlim()
    assert x0 < 1 and x1 > 16                                   # real x coordinates, not 0..5
    assert [t.get_text() for t in ax.get_xticklabels()] == ["1", "4", "16"]
    assert list(ax.get_yticks()) == [100.0, 300.0]
    assert len(ps.fig.axes) == 2                                # + colourbar axes
    iso_levels = [lv for c in ax.collections if hasattr(c, "levels") for lv in c.levels]
    assert iso_levels and min(iso_levels) >= 20                 # nothing below the threshold
    assert sm.subplots[0].image.aspect == "auto"                # heatmap default


def test_plain_image_still_renders_in_index_space(win):
    import numpy as np
    z = np.arange(12.0).reshape(3, 4)
    win.handle_incoming_ipc({"action": "append_image", "name": "im", "bytes": z.tobytes(),
                             "shape": [3, 4], "dtype": "float64"})
    win.handle_incoming_ipc({"action": "add_image_to_sheet", "name": "im", "sheet": "__new__",
                             "subplot_index": 0, "display_axes": [0, 1]})
    sm = next(reversed(win.sheets.values()))
    ps = win._open_sheet_tab(sm.sheet_id)
    win.show()
    QApplication.processEvents()
    ax = ps.fig.axes[0]
    assert ax.get_xlim() == (0.0, 4.0) and len(ax.images) == 1


def test_image_style_widget_roundtrips_heatmap_controls(win):
    sm, ps = _heatmap_sheet(win, iso_show=True, cmap_bins=4, x_tick_mode="data")
    sub = sm.subplots[0]
    w = win.axes_widget.image_widget
    win.sync_active_subplot_inspector()
    assert w.bins_spin.value() == 4 and w.iso_check.isChecked()
    w.iso_above_edit.setText("12.5")
    w.bounds_edit.setText("0, 20, 60")
    w.reverse_check.setChecked(True)
    w._tick_widgets["y"][0].setCurrentIndex(2)
    w._tick_widgets["y"][1].setText("100, 500")
    w._push()
    r = sub.image
    assert (r.iso_above, r.cmap_boundaries, r.cmap_reverse) == (12.5, "0, 20, 60", True)
    assert (r.y_tick_mode, r.y_tick_values, r.x_tick_mode) == ("custom", "100, 500", "data")
    from NoorSuite.model import ImageRef
    assert ImageRef.from_dict(r.to_dict()).iso_above == 12.5
