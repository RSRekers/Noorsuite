"""plot(..., yerr=...) from the client: error bars / bands for live GUI and offline."""
import numpy as np
import pandas as pd
import pytest

from NoorSuite.client import OfflineClient, SciSuiteClient
from NoorSuite.model import ProjectModel
from tests.test_gui_features import app, win  # noqa: F401  (window fixtures)
from tests.test_remove_api import _Wire


def _df():
    x = np.linspace(0, 5, 8)
    return pd.DataFrame({"x": x, "y": x ** 2, "z": x ** 3, "e": 0.3 + 0 * x, "ez": 0.5 + 0 * x,
                         "lo": 0.2 + 0 * x, "hi": 0.4 + 0 * x})


def _traces(pm):
    return [t for sm in pm.sheets for sub in sm.subplots for t in sub.traces]


def test_offline_symmetric_asymmetric_and_band(tmp_path):
    suite = OfflineClient(tmp_path / "p.sciproj")
    suite.push_dataframe(_df(), name="d")
    suite.plot("d", "x", "y", new_sheet=True, yerr="e", plot_type="Scatter")
    suite.plot("d", "x", "z", new_sheet=True, yerr_low="lo", yerr_high="hi")
    suite.plot("d", "x", "y", new_sheet=True, yerr="e", band=True, band_alpha=0.4, errorbars=False,
               yerr_mode="sem")
    ts = _traces(ProjectModel.load(tmp_path / "p.sciproj"))
    a, b, c = ts
    assert (a.show_errorbar, a.yerr_col, a.yerr_mode, a.plot_type, a.show_band) == (True, "e", "std", "Scatter", False)
    assert (b.show_errorbar, b.yerr_low_col, b.yerr_high_col, b.yerr_mode) == (True, "lo", "hi", "minmax")
    assert (c.show_errorbar, c.show_band, c.band_alpha, c.yerr_mode) == (False, True, 0.4, "sem")


def test_offline_several_y_columns_arrays_and_mistakes(tmp_path):
    suite = OfflineClient(tmp_path / "p.sciproj")
    df = _df()
    suite.push_dataframe(df, name="d")
    suite.plot("d", "x", ["y", "z"], new_sheet=True, yerr={"y": "e", "z": "ez"})
    suite.plot("d", "x", ["y", "z"], sheet="Sheet 1", yerr=["ez", "e"], band=True)    # list in y order
    pm = ProjectModel.load(tmp_path / "p.sciproj")
    t = {(i, tr.y_col): tr.yerr_col for i, tr in enumerate(_traces(pm))}
    assert list(t.values()) == ["e", "ez", "ez", "e"]
    with pytest.raises(ValueError, match="several y columns"):
        suite.plot("d", "x", ["y", "z"], yerr="e")                       # ambiguous
    with pytest.raises(ValueError, match="not found"):
        suite.plot("d", "x", "y", yerr="nope")                           # lists the columns that exist
    with pytest.raises(ValueError, match="together"):
        suite.plot("d", "x", "y", yerr_low="lo")
    with pytest.raises(ValueError, match="band=True needs"):
        suite.plot("d", "x", "y", band=True)
    with pytest.raises(ValueError, match="DataFrame"):
        suite.plot("d", "x", "y", yerr=[0.1] * 8)                        # array + data object name
    with pytest.raises(ValueError, match="yerr_mode"):
        suite.plot("d", "x", "y", yerr="e", yerr_mode="variance")
    # an array with a DataFrame: stored as a column next to the data
    suite.plot(df[["x", "y"]], "x", "y", name="arr", new_sheet=True, yerr=np.full(8, 0.25))
    pm = ProjectModel.load(tmp_path / "p.sciproj")
    obj = next(o for o in pm.data_objects if o.name == "arr")
    assert "y_err" in obj.column_order and obj.columns["y_err"][0] == 0.25
    assert _traces(pm)[-1].yerr_col == "y_err"


def test_live_gui_draws_the_errorbars_and_survives_a_bad_column(win):
    suite = SciSuiteClient()
    suite._ipc = _Wire(win)
    suite.push_dataframe(_df(), name="d")
    suite.plot("d", "x", "y", new_sheet=True, title="E", yerr="e")
    sm = win._active_sheet_model()
    tr = sm.subplots[0].traces[0]
    assert tr.show_errorbar and tr.yerr_col == "e" and tr.has_error_data
    ps = win.open_tabs[sm.sheet_id]
    ps.render(win.repository, win.images)
    assert len(ps._axes[0].containers) == 1                              # the ErrorbarContainer
    # a column that is not in the data cannot slip through the client; if it does reach the GUI
    # (older client) the trace is added without errors and a status message explains it
    win.handle_incoming_ipc({"action": "add_to_sheet", "data_name": "d", "x_col": "x", "y_cols": ["z"],
                             "sheet": "E", "subplot_index": 0,
                             "trace_styles": {"z": {"show_errorbar": True, "yerr_col": "ghost"}}})
    z = sm.subplots[0].traces[-1]
    assert z.y_col == "z" and z.yerr_col == "" and not z.show_errorbar
    with pytest.raises(ValueError, match="not found"):
        suite.plot("d", "x", "y", yerr="ghost")
    suite.plot("d", "x", "z", sheet="E", yerr_low="lo", yerr_high="hi", band=True)
    t2 = sm.subplots[0].traces[-1]
    assert (t2.yerr_mode, t2.show_band, t2.show_errorbar) == ("minmax", True, True)
    ps.render(win.repository, win.images)
    assert len(ps._axes[0].containers) == 2
