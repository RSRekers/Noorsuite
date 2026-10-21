"""Combine traces into a mean +- error trace from the client / CLI (the GUI's combine dialogs)."""
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from NoorSuite.client import OfflineClient, SciSuiteClient
from NoorSuite.model import ProjectModel
from tests.test_gui_features import app, win  # noqa: F401  (window fixtures)
from tests.test_remove_api import _Wire


def _df():
    x = np.arange(1.0, 7.0)
    return pd.DataFrame({"x": x, "r1": x, "r2": x + 1.0, "r3": x + 2.0, "other": 5 * x})


def _traces(pm):
    return [t for sm in pm.sheets for sub in sm.subplots for t in sub.traces]


def test_offline_combine_several_traces_band_bars_sem(tmp_path):
    suite = OfflineClient(tmp_path / "p.sciproj")
    suite.push_dataframe(_df(), name="d")
    suite.plot("d", "x", ["r1", "r2", "r3", "other"], new_sheet=True, title="S")
    suite.combine_traces("S", ["r1", "r2", "r3"], display="both", mode="sem", label="rep")
    pm = ProjectModel.load(tmp_path / "p.sciproj")
    ts = _traces(pm)
    assert [t.y_col for t in ts] == ["mean", "other"]                      # originals replaced, in place
    new = ts[0]
    assert new.label == "rep" and new.show_band and new.show_errorbar and new.yerr_mode == "sem"
    obj = next(o for o in pm.data_objects if o.id == new.data_id)
    assert obj.name == "rep (n=3 mean±err)"
    assert np.allclose(obj.columns["mean"], np.arange(1.0, 7.0) + 1.0)     # mean of r1..r3
    assert np.allclose(obj.columns["std"], np.std([0.0, 1.0, 2.0], ddof=1))
    assert np.allclose(obj.columns["sem"], obj.columns["std"] / np.sqrt(3))
    # keep the originals, other display, a scatter
    suite.combine_traces("S", [0, 1], keep_originals=True, display="bars", plot_type="Scatter")
    ts = _traces(ProjectModel.load(tmp_path / "p.sciproj"))
    assert len(ts) == 3 and ts[-1].plot_type == "Scatter" and ts[-1].show_errorbar and not ts[-1].show_band
    with pytest.raises(ValueError, match="at least two"):
        suite.combine_traces("S", ["other"])
    with pytest.raises(ValueError, match="display"):
        suite.combine_traces("S", "all", display="fog")
    r = suite._ipc.send({"action": "edit", "op": "combine_traces", "sheet": "S", "subplot": 0,
                         "trace": ["nonexistent", "also"]})
    assert r["status"] == "error"


def test_offline_split_one_trace_of_repeats_both_layouts(tmp_path):
    suite = OfflineClient(tmp_path / "p.sciproj")
    # 3 conditions x 4 repeats, block layout: [a1..a4, b1..b4, c1..c4]
    y = np.repeat([10.0, 20.0, 30.0], 4) + np.tile([0.0, 1.0, 2.0, 3.0], 3)
    x = np.repeat([1.0, 2.0, 3.0], 4)
    suite.push_dataframe(pd.DataFrame({"x": x, "y": y}), name="blk")
    suite.plot("blk", "x", "y", new_sheet=True, title="B")
    suite.combine_traces("B", 0, split=4, layout="block", display="both")
    pm = ProjectModel.load(tmp_path / "p.sciproj")
    obj = next(o for o in pm.data_objects if o.name.endswith("(n=4 mean±err)"))
    assert np.allclose(obj.columns["x"], [1, 2, 3]) and np.allclose(obj.columns["mean"], [11.5, 21.5, 31.5])
    # sequence: one cycle of 3 conditions repeated 4 times
    ys = np.tile([10.0, 20.0, 30.0], 4) + np.repeat([0.0, 1.0, 2.0, 3.0], 3)
    suite.push_dataframe(pd.DataFrame({"x": np.tile([1.0, 2.0, 3.0], 4), "y": ys}), name="seq")
    suite.plot("seq", "x", "y", new_sheet=True, title="Q")
    suite.combine_traces("Q", 0, split=3, layout="sequence")
    pm = ProjectModel.load(tmp_path / "p.sciproj")
    obj = [o for o in pm.data_objects if o.name.endswith("(n=3 mean±err)")][-1]
    assert np.allclose(obj.columns["mean"], [11.5, 21.5, 31.5])
    suite.push_dataframe(_df(), name="d")
    suite.plot("d", "x", ["r1", "r2"], new_sheet=True, title="Two")
    with pytest.raises(ValueError, match="exactly one"):
        suite.combine_traces("Two", "all", split=3)
    with pytest.raises(ValueError, match="layout"):
        suite.combine_traces("Q", 0, split=3, layout="zigzag")
    with pytest.raises(ValueError, match="multiple|trace|split"):
        suite.combine_traces("B", 0, split=5)                                # 12 values, not a multiple of 5


def test_live_gui_combines(win):
    suite = SciSuiteClient()
    suite._ipc = _Wire(win)
    suite.push_dataframe(_df(), name="d")
    suite.plot("d", "x", ["r1", "r2", "r3"], new_sheet=True, title="S")
    suite.combine_traces("S", "all", display="band")
    sm = win._active_sheet_model()
    (t,) = sm.subplots[0].traces
    assert t.show_band and t.y_col == "mean"
    assert any(o.source == "combined" for o in win.repository.values())
    ps = win.open_tabs[sm.sheet_id]
    ps.render(win.repository, win.images)
    assert ps._axes[0].collections                                        # the shaded band is drawn


def test_cli_combine(tmp_path):
    _df().to_csv(tmp_path / "d.csv", index=False)
    proj = ["--project", str(tmp_path / "p.sciproj"), "--port", "59123"]
    run = lambda *a: subprocess.run([sys.executable, "-m", "NoorSuite", *a], capture_output=True, text=True)  # noqa: E731
    r = run("insert", str(tmp_path / "d.csv"), "--name", "d", "--plot", "x:r1,r2,r3", "--new-sheet",
            "--title", "S", *proj)
    assert r.returncode == 0, r.stdout + r.stderr
    r = run("api", *proj, "combine", "S", "--trace", "r1,r2,r3", "--display", "both", "--mode", "minmax",
            "--label", "mean of r")
    assert r.returncode == 0, r.stdout + r.stderr
    pm = ProjectModel.load(tmp_path / "p.sciproj")
    (t,) = _traces(pm)
    assert t.yerr_mode == "minmax" and t.show_band and t.show_errorbar and t.label == "mean of r"
    bad = run("api", *proj, "combine", "S", "--display", "bars")           # a single trace left
    assert bad.returncode == 1 and "combine failed" in bad.stdout
