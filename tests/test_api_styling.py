"""Axes / sheet styling from the API: log axes, legend, font sizes, limits, grids that stay readable."""
import numpy as np
import pandas as pd
import pytest

from NoorSuite.client import OfflineClient, SciSuiteClient
from NoorSuite.model import (ProjectModel, SheetModel, apply_subplot_style, check_subplot_style,
                             expand_subplot_style)
from tests.test_gui_features import app, win  # noqa: F401  (window fixtures)
from tests.test_remove_api import _Wire


def _df():
    x = np.linspace(1, 100, 30)
    return pd.DataFrame({"x": x, "a": x ** 2, "b": x})


def test_friendly_options_expand_to_fields():
    f = expand_subplot_style({"yscale": "log", "legend": False, "font_size": 20, "xlim": (1, None),
                              "logx": True, "grid": False})
    assert f["y_scale"] == "log" and f["legend_visible"] is False and f["x_scale"] == "log"
    assert f["tick_label_size"] == 20 and f["xlabel_fontsize"] == 20 and f["title_fontsize"] == 22
    assert f["legend_fontsize"] == 17 and f["x_min"] == 1.0 and f["x_max"] is None
    assert f["show_grid"] is False
    with pytest.raises(ValueError, match="valid"):
        check_subplot_style({"colour": "red"})
    with pytest.raises(ValueError, match="x_scale"):
        check_subplot_style({"x_scale": "cubic"})
    with pytest.raises(ValueError, match="bad value"):
        check_subplot_style({"tick_label_size": "big"})


def _sub(tmp_path, i=0, k=0):
    pm = ProjectModel.load(tmp_path / "p.sciproj")
    return pm.sheets[k].subplots[i], pm.sheets[k]


def test_offline_plot_sets_axes_options_and_sizes_the_grid(tmp_path):
    suite = OfflineClient(tmp_path / "p.sciproj")
    suite.push_dataframe(_df(), name="d")
    suite.plot("d", "x", ["a", "b"], new_sheet=True, yscale="log", legend=False, font_size=14,
               xlim=(1, 50), grid=(3, 4), all_subplots=True, tick_size=12)
    sub, sm = _sub(tmp_path, 5)                                       # any of the 12 panels
    assert (sm.rows, sm.cols, len(sm.subplots)) == (3, 4, 12)
    assert sub.y_scale == "log" and not sub.legend_visible and sub.xlabel_fontsize == 14
    assert sub.tick_label_size == 12 and (sub.x_min, sub.x_max) == (1.0, 50.0)
    assert (sm.fig_width_cm, sm.fig_height_cm) == (39.0, 20.0)         # grows with the grid
    # an explicit size wins; styling later through the dedicated methods
    suite.style_sheet(sm.sheet_id, size_cm=(30, 18), background="#fafafa")
    suite.style_subplot(sm.sheet_id, "all", show_grid=False, xscale="log")
    suite.style_subplot(sm.sheet_id, [0, 1], title_size=9)
    sub, sm = _sub(tmp_path, 1)
    assert (sm.fig_width_cm, sm.fig_height_cm) == (30.0, 18.0) and sm.fig_face_color == "#fafafa"
    assert sub.show_grid is False and sub.x_scale == "log" and sub.title_fontsize == 9
    assert _sub(tmp_path, 7)[0].title_fontsize == 15.4                 # untouched panel
    suite.style_sheet(sm.sheet_id, rows=2)                            # keeps the columns
    assert (_sub(tmp_path)[1].rows, _sub(tmp_path)[1].cols) == (2, 4)
    with pytest.raises(ValueError, match="unknown subplot option"):
        suite.plot("d", "x", "a", yscalee="log")
    with pytest.raises(ValueError, match="rows"):
        suite.style_sheet(sm.sheet_id, rows=20)


def test_live_gui_applies_log_axes_legend_and_fonts(win):
    suite = SciSuiteClient()
    suite._ipc = _Wire(win)
    suite.push_dataframe(_df(), name="d")
    suite.plot("d", "x", ["a", "b"], new_sheet=True, title="S", yscale="log", legend=False,
               font_size=18, grid=(2, 2), all_subplots=True)
    sm = win._active_sheet_model()
    assert (sm.rows, sm.cols) == (2, 2) and win.rows_spin.value() == 2
    assert all(s.y_scale == "log" and not s.legend_visible and s.tick_label_size == 18
               for s in sm.subplots)
    ps = win.open_tabs[sm.sheet_id]
    ps.render(win.repository, win.images)
    assert ps._axes[0].get_yscale() == "log"
    assert ps._axes[0].get_legend() is None
    assert ps._axes[0].xaxis.get_ticklabels()[0].get_fontsize() == 18
    suite.style_subplot("S", 0, legend=True, xlim=(5, 20))
    assert sm.subplots[0].legend_visible and sm.subplots[0].x_min == 5.0
    assert sm.subplots[1].legend_visible is False


def test_a_big_grid_renders_readably_by_default(win):
    """12 panels at the old fixed 12 x 8 cm gave ~1 cm panels; now the figure grows with the grid."""
    from NoorSuite.sheet import logical_size_cm
    sm = SheetModel("x", 3, 4)
    w, h = logical_size_cm(sm)
    assert w / 4 >= 8.0 and h / 3 >= 5.5                               # >= 8 x 5.5 cm per panel
    assert logical_size_cm(SheetModel("y")) == (12.0, 8.0)             # a single panel is unchanged
