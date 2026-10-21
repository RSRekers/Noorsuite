"""One legend for a whole multi-panel figure: model, render, GUI widget, client and CLI."""
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from NoorSuite.client import OfflineClient, SciSuiteClient
from NoorSuite.model import ProjectModel, SheetModel, check_sheet_style
from tests.test_gui_features import app, win  # noqa: F401  (window fixtures)
from tests.test_remove_api import _Wire


def _df():
    x = np.linspace(1, 9, 9)
    return pd.DataFrame({"x": x, "a": x, "b": 2 * x})


def test_sheet_option_validation_and_roundtrip():
    assert check_sheet_style({"joint_legend": "Bottom", "joint_legend_size": 9,
                              "joint_legend_ncol": 2})["joint_legend"] == "bottom"
    assert check_sheet_style({"joint_legend": False})["joint_legend"] == ""
    assert check_sheet_style({"joint_legend": "none"})["joint_legend"] == ""
    with pytest.raises(ValueError, match="joint_legend"):
        check_sheet_style({"joint_legend": "diagonal"})
    sm = SheetModel("S", 2, 2)
    sm.joint_legend, sm.joint_legend_ncol = "right", 2
    back = SheetModel.from_dict(sm.to_dict())
    assert (back.joint_legend, back.joint_legend_ncol, back.joint_legend_fontsize) == ("right", 2, 11.0)
    assert SheetModel.from_dict({"name": "old", "rows": 1, "cols": 1}).joint_legend == ""   # old files


@pytest.mark.parametrize("pos", ["bottom", "top", "right", "left"])
def test_joint_legend_replaces_panel_legends_and_keeps_its_strip_free(win, pos):
    suite = SciSuiteClient()
    suite._ipc = _Wire(win)
    suite.push_dataframe(_df(), name="d")
    suite.plot("d", "x", ["a", "b"], new_sheet=True, title="J", grid=(2, 2), all_subplots=True,
               joint_legend=pos)
    for i in (1, 2, 3):
        suite.plot("d", "x", ["a", "b"], sheet="J", subplot=i)
    sm = win._active_sheet_model()
    assert sm.joint_legend == pos
    ps = win.open_tabs[sm.sheet_id]
    ps.render(win.repository, win.images)
    ps.canvas.draw()
    assert all(ax.get_legend() is None for ax in ps._axes)             # no per-panel legends
    (leg,) = ps.fig.legends
    assert [t.get_text() for t in leg.get_texts()] == ["a", "b"]        # each label once
    lb = leg.get_window_extent()
    for ax in ps._axes:                                                  # the panels stay clear of it
        ab = ax.get_tightbbox(ps.canvas.get_renderer())
        assert not lb.overlaps(ab) or (lb.width < 2 or lb.height < 2), (pos, lb, ab)
    suite.organize_sheet("J", joint_legend="none")                       # back to panel legends
    ps.render(win.repository, win.images)
    assert not ps.fig.legends and all(ax.get_legend() is not None for ax in ps._axes)


def test_widget_edits_the_joint_legend(win):
    suite = SciSuiteClient()
    suite._ipc = _Wire(win)
    suite.push_dataframe(_df(), name="d")
    suite.plot("d", "x", "a", new_sheet=True, title="W", grid=(1, 2))
    sm = win._active_sheet_model()
    w = win.figure_widget
    w.set_sheet(sm)
    w.joint_combo.setCurrentIndex(2)                                     # "top"
    w.joint_ncol.setValue(3)
    w.joint_frame.setChecked(True)
    assert (sm.joint_legend, sm.joint_legend_ncol, sm.joint_legend_frame) == ("top", 3, True)


def test_offline_and_cli(tmp_path):
    suite = OfflineClient(tmp_path / "p.sciproj")
    suite.push_dataframe(_df(), name="d")
    suite.plot("d", "x", ["a", "b"], new_sheet=True, title="S", grid=(1, 3), joint_legend="bottom",
               joint_legend_ncol=2, joint_legend_size=9, all_subplots=True, legend=False)
    sm = ProjectModel.load(tmp_path / "p.sciproj").sheets[0]
    assert (sm.joint_legend, sm.joint_legend_ncol, sm.joint_legend_fontsize) == ("bottom", 2, 9.0)
    pd.DataFrame(_df()).to_csv(tmp_path / "d.csv", index=False)
    proj = ["--project", str(tmp_path / "q.sciproj"), "--port", "59123"]
    r = subprocess.run([sys.executable, "-m", "NoorSuite", "insert", str(tmp_path / "d.csv"), "--name", "d",
                        "--plot", "x:a,b", "--new-sheet", "--title", "T", "--panels", "2x2",
                        "--joint-legend", "right", "--joint-legend-frame", *proj],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    sm = ProjectModel.load(tmp_path / "q.sciproj").sheets[0]
    assert (sm.joint_legend, sm.joint_legend_frame) == ("right", True)
    r = subprocess.run([sys.executable, "-m", "NoorSuite", "api", *proj, "style", "T",
                        "--joint-legend", "none"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert ProjectModel.load(tmp_path / "q.sciproj").sheets[0].joint_legend == ""
