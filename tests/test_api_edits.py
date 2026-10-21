"""Editing what exists (trace styles, sheets, folders, colormaps, save, export) from the client and CLI."""
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from NoorSuite.client import OfflineClient, SciSuiteClient
from NoorSuite.model import ProjectModel
from tests.test_gui_features import _wait, app, win  # noqa: F401  (window fixtures)
from tests.test_remove_api import _Wire


def _df():
    x = np.linspace(1, 50, 20)
    return pd.DataFrame({"x": x, "a": x ** 2, "b": x, "c": 2 * x})


def _traces(pm):
    return [t for sm in pm.sheets for sub in sm.subplots for t in sub.traces]


def test_offline_trace_edits_sheets_folders_colormaps_save(tmp_path):
    suite = OfflineClient(tmp_path / "p.sciproj")
    suite.push_dataframe(_df(), name="d")
    suite.plot("d", "x", ["a", "b", "c"], new_sheet=True, title="S")
    suite.restyle_traces("S", "a", color="#ff0000", line_width=3, scale_factor=0.5)
    suite.restyle_traces("S", 1, enabled=False, label="bee", marker="o")
    pm = ProjectModel.load(tmp_path / "p.sciproj")
    a, b, c = _traces(pm)
    assert (a.color, a.line_width, a.scale_factor) == ("#ff0000", 3.0, "custom:0.5")
    assert (b.enabled, b.label, b.marker) == (False, "bee", "o") and c.enabled
    suite.move_trace("S", 0, 2)
    assert [t.y_col for t in _traces(ProjectModel.load(tmp_path / "p.sciproj"))] == ["b", "c", "a"]
    suite.remove_traces("S", "bee")
    assert [t.y_col for t in _traces(ProjectModel.load(tmp_path / "p.sciproj"))] == ["c", "a"]
    with pytest.raises(ValueError, match="unknown trace option"):
        suite.restyle_traces("S", colour="red")
    with pytest.raises(ValueError, match="plot_type"):
        suite.restyle_traces("S", plot_type="Blob")
    r = suite._ipc.send({"action": "edit", "op": "trace_style", "sheet": "nope", "style": {}})
    assert r["status"] == "error"
    # sheets and folders
    suite.new_sheet("Grid", grid=(3, 4), folder="Paper/Figs")
    suite.duplicate_sheet("S", "S2")
    suite.folder("Paper/Figs", "rename", new_name="Figures")
    suite.folder("Empty/Deep")
    pm = ProjectModel.load(tmp_path / "p.sciproj")
    names = {s.name: s for s in pm.sheets}
    assert (names["Grid"].rows, names["Grid"].cols) == (3, 4) and "S2" in names
    assert [t.y_col for sub in names["S2"].subplots for t in sub.traces] == ["c", "a"]
    tree = pm.tree
    assert any(n.get("name") == "Paper" and n["children"][0]["name"] == "Figures" for n in tree)
    assert any(n.get("name") == "Empty" for n in tree)
    suite.folder("Paper/Figures", "delete")
    assert not any(n.get("name") == "Paper" and n["children"] and n["children"][0].get("name") == "Figures"
                   for n in ProjectModel.load(tmp_path / "p.sciproj").tree)
    # colormaps
    suite.add_colormap("warm", ["#400000", "#ff8800", "#ffff88"])
    suite.use_colormap("S", "warm")
    pm = ProjectModel.load(tmp_path / "p.sciproj")
    assert [c.name for c in pm.colormaps] == ["warm"] and pm.sheets[0].active_colormap == "warm"
    with pytest.raises(ValueError, match="two colours"):
        suite.add_colormap("bad", ["#fff"])
    suite.remove_colormap("warm")
    assert ProjectModel.load(tmp_path / "p.sciproj").colormaps == []
    # save a copy elsewhere; export a figure headlessly
    suite.save_project(tmp_path / "copy.sciproj")
    assert len(ProjectModel.load(tmp_path / "copy.sciproj").sheets) == 3
    for ext in ("png", "pdf", "svg"):
        out = suite.export_figure("S", tmp_path / f"fig.{ext}", dpi=100)
        assert out.endswith(ext) and (tmp_path / f"fig.{ext}").stat().st_size > 500
    with pytest.raises(ValueError, match="must end in"):
        suite.export_figure("S", tmp_path / "fig.bmp")
    with pytest.raises(ValueError, match="no sheet"):
        suite.export_figure("ghost", tmp_path / "x.png")


def test_live_gui_edits_and_exports(win, tmp_path):
    suite = SciSuiteClient()
    suite._ipc = _Wire(win)
    suite.push_dataframe(_df(), name="d")
    suite.plot("d", "x", ["a", "b"], new_sheet=True, title="S")
    sm = win._active_sheet_model()
    suite.restyle_traces("S", "b", color="#00aa00", enabled=False)
    ta, tb = sm.subplots[0].traces
    assert tb.color == "#00aa00" and not tb.enabled and ta.enabled
    suite.remove_traces("S", 0)
    assert [t.y_col for t in sm.subplots[0].traces] == ["b"]
    suite.new_sheet("Wide", grid=(2, 3), size_cm=(30, 16), folder="F/G")
    wide = next(s for s in win.sheets.values() if s.name == "Wide")
    assert (wide.rows, wide.cols, wide.fig_width_cm) == (2, 3, 30.0)
    suite.duplicate_sheet("S", "S copy")
    assert any(s.name == "S copy" for s in win.sheets.values())
    suite.add_colormap("duo", ["#000000", "#ffffff"])
    assert [c.name for c in win.colormaps] == ["duo"]
    suite.use_colormap("S", "duo")
    assert sm.active_colormap == "duo"
    suite.folder("F/G", "rename", new_name="H")
    assert win._folder_item(["F", "H"]) is not None
    out = suite.export_figure("S", tmp_path / "live.png", dpi=80)
    assert (tmp_path / "live.png").stat().st_size > 500
    suite.save_project(tmp_path / "live.sciproj")
    assert (tmp_path / "live.sciproj").is_file() and win.project_path.endswith("live.sciproj")


def _cli(tmp_path, *args):
    return subprocess.run([sys.executable, "-m", "NoorSuite", *args], capture_output=True, text=True)


def test_cli_axes_options_and_every_new_command(tmp_path):
    pd.DataFrame({"x": np.linspace(1, 9, 9), "a": np.linspace(1, 9, 9) ** 2,
                  "b": np.linspace(1, 9, 9)}).to_csv(tmp_path / "d.csv", index=False)
    proj = ["--project", str(tmp_path / "p.sciproj"), "--port", "59123"]
    r = _cli(tmp_path, "insert", str(tmp_path / "d.csv"), "--name", "d", "--plot", "x:a,b", "--new-sheet",
             "--title", "S", "--yscale", "log", "--no-legend", "--font-size", "15", "--xlim", "2,",
             "--panels", "2x2", "--all-panels", "--set", "spine_width=2", *proj)
    assert r.returncode == 0, r.stdout + r.stderr
    pm = ProjectModel.load(tmp_path / "p.sciproj")
    sm = pm.sheets[0]
    assert (sm.rows, sm.cols) == (2, 2)
    assert all(s.y_scale == "log" and not s.legend_visible and s.tick_label_size == 15
               and s.spine_width == 2.0 for s in sm.subplots)
    assert sm.subplots[1].x_min == 2.0 and sm.subplots[1].x_max is None
    steps = [
        ("api", *proj, "style", "S", "--subplot", "0,1", "--yscale", "linear", "--title-size", "9"),
        ("api", *proj, "trace-style", "S", "--trace", "a", "--color", "#123456", "--hide"),
        ("api", *proj, "trace-move", "S", "0", "--to", "1"),
        ("api", *proj, "sheet", "new", "Blank", "--panels", "1x2"),
        ("api", *proj, "sheet", "duplicate", "S", "--as", "S copy"),
        ("api", *proj, "folder", "create", "A/B"),
        ("api", *proj, "colormap", "add", "duo", "--colors", "#000000,#ff0000"),
        ("api", *proj, "colormap", "use", "duo", "--sheet", "S"),
        ("api", *proj, "plot", "d", "--y", "b", "--sheet", "Blank", "--yscale", "log", "--color", "#00ff00"),
        ("api", *proj, "save", str(tmp_path / "copy.sciproj")),
        ("api", *proj, "export", "S", str(tmp_path / "s.svg")),
        ("api", *proj, "info"),
    ]
    for s in steps:
        r = _cli(tmp_path, *s)
        assert r.returncode == 0, (s, r.stdout, r.stderr[-400:])
    pm = ProjectModel.load(tmp_path / "p.sciproj")
    s = next(x for x in pm.sheets if x.name == "S")
    assert s.subplots[0].y_scale == "linear" and s.subplots[0].title_fontsize == 9
    assert s.subplots[2].y_scale == "log" and s.active_colormap == "duo"
    ta = next(t for t in s.subplots[0].traces if t.y_col == "a")
    assert ta.color == "#123456" and not ta.enabled
    assert next(x for x in pm.sheets if x.name == "Blank").subplots[0].traces[0].y_col == "b"
    assert (tmp_path / "copy.sciproj").is_file() and (tmp_path / "s.svg").stat().st_size > 500
    bad = _cli(tmp_path, "api", *proj, "style", "S", "--set", "nonsense=1")
    assert bad.returncode == 1 and "unknown subplot option" in bad.stdout
    bad = _cli(tmp_path, "api", *proj, "trace-style", "S", "--set", "colour=red")
    assert bad.returncode == 1 and "unknown trace option" in bad.stdout
