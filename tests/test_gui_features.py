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
