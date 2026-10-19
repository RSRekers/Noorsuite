"""Mean with a shaded band: model, rendering, the combine dialogs and the client API."""
import numpy as np
import pandas as pd
import pytest

from NoorSuite.client import OfflineClient
from NoorSuite.model import (ERROR_MODES, DataObject, ProjectModel, SheetModel, TraceRef,
                             aggregate_series, split_into_repeats)
from tests.test_gui_features import app, win  # noqa: F401  (window fixtures)


def _derived(n=5):
    x = np.linspace(0, 1, n)
    agg = aggregate_series([x + 1, x + 2, x + 4])
    return DataObject("m", {"x": x, "mean": agg["mean"], "std": agg["std"], "sem": agg["sem"],
                            "err_min": agg["err_min"], "err_max": agg["err_max"]})


def test_sem_is_std_over_root_n_for_both_aggregations():
    agg = aggregate_series([[1.0, 10.0], [2.0, 20.0], [4.0, 30.0]])
    np.testing.assert_allclose(agg["sem"], agg["std"] / np.sqrt(3))
    sp = split_into_repeats([1.0, 2.0, 3.0, 4.0, 5.0, 6.0], 3, layout="block")
    np.testing.assert_allclose(sp["sem"], sp["std"] / np.sqrt(3))
    sq = split_into_repeats([1.0, 2.0, 3.0, 4.0, 5.0, 6.0], 3, layout="sequence")     # 2 repeats of 3 positions
    np.testing.assert_allclose(sq["sem"], sq["std"] / np.sqrt(2))


def test_error_source_modes_pick_the_right_columns_and_round_trip():
    obj = _derived()
    t = TraceRef(obj.id, "x", "mean")
    t.yerr_col, t.yerr_sem_col, t.yerr_low_col, t.yerr_high_col = "std", "sem", "err_min", "err_max"
    assert ERROR_MODES == ["std", "sem", "minmax"]
    t.yerr_mode = "std"
    np.testing.assert_allclose(t.resolve_yerr(obj), obj.columns["std"])
    t.yerr_mode = "sem"
    np.testing.assert_allclose(t.resolve_yerr(obj), obj.columns["sem"])
    t.yerr_mode = "minmax"
    assert t.resolve_yerr(obj).shape == (2, 5)
    t.show_band, t.band_alpha = True, 0.4
    back = TraceRef.from_dict(t.to_dict())
    assert (back.show_band, back.band_alpha, back.yerr_sem_col, back.yerr_mode) == (True, 0.4, "sem", "minmax")
    old = TraceRef.from_dict({"data_id": "d", "y_col": "y", "show_errorbar": True, "yerr_col": "s"})
    assert old.show_band is False and old.band_alpha == 0.25 and old.yerr_sem_col == ""
    assert old.has_error_data


def test_band_is_drawn_between_mean_minus_and_plus_error(app):
    from NoorSuite.sheet import PlotSheet
    obj = _derived(40)
    sm = SheetModel("S")
    t = TraceRef(obj.id, "x", "mean")
    t.yerr_col, t.yerr_sem_col, t.yerr_low_col, t.yerr_high_col = "std", "sem", "err_min", "err_max"
    t.show_band, t.yerr_mode, t.band_alpha = True, "std", 0.3
    sm.subplots[0].traces.append(t)
    ps = PlotSheet(sm)
    ps.render({obj.id: obj}, {})
    ax = ps._axes[0]
    polys = [c for c in ax.collections if c.get_alpha() == pytest.approx(0.3)]
    assert len(polys) == 1
    ys = polys[0].get_paths()[0].vertices[:, 1]
    mean, std = obj.columns["mean"], obj.columns["std"]
    assert ys.max() == pytest.approx((mean + std).max()) and ys.min() == pytest.approx((mean - std).min())
    line = ax.lines[0]
    assert line.get_zorder() >= polys[0].get_zorder()                 # the line stays on top
    # switching the source to min / max changes the band, switching it off removes it
    t.yerr_mode = "minmax"
    ps.render({obj.id: obj}, {})
    lo = (mean - obj.columns["err_min"]).min()
    assert [c for c in ps._axes[0].collections if c.get_alpha() == pytest.approx(0.3)][0] \
        .get_paths()[0].vertices[:, 1].min() == pytest.approx(lo)
    t.show_band = False
    ps.render({obj.id: obj}, {})
    assert not [c for c in ps._axes[0].collections if c.get_alpha() == pytest.approx(0.3)]
    ps.close()


def test_style_widget_has_band_controls(app):
    from NoorSuite.dialogs import TraceStyleWidget
    obj = _derived()
    t = TraceRef(obj.id, "x", "mean")
    plain = TraceRef(obj.id, "x", "mean")
    t.yerr_col = "std"
    w = TraceStyleWidget()
    w.set_trace(plain)
    assert not w.band_check.isEnabled()                       # nothing to show without error data
    w.set_trace(t)
    assert w.band_check.isEnabled()
    w.band_check.setChecked(True)
    w.band_alpha_spin.setValue(0.5)
    assert t.show_band is True and t.band_alpha == 0.5
    w.errbar_mode_combo.setCurrentIndex(1)                     # std. error of the mean
    assert t.yerr_mode == "sem"


def _three_traces(win):
    x = np.linspace(0, 10, 30)
    refs, ys = [], []
    for k in range(3):
        win.handle_incoming_ipc({"action": "append_dataframe", "name": f"r{k}", "columns": {
            "x": x, "y": np.sin(x) + 0.1 * k}, "column_order": ["x", "y"]})
        win.handle_incoming_ipc({"action": "add_to_sheet", "data_name": f"r{k}", "x_col": "x",
                                 "y_cols": ["y"], "sheet": "Mix" if k else "__new__",
                                 "subplot_index": 0, "organize": {"name": "Mix"} if not k else {}})
        ys.append(np.sin(x) + 0.1 * k)
    sm = win._active_sheet_model()
    return sm, list(sm.subplots[0].traces), x, ys


def test_combine_defaults_to_a_band_and_offers_bars_and_sem(win):
    from NoorSuite.app import CombineTracesDialog, _error_choice
    sm, refs, x, ys = _three_traces(win)
    assert len(refs) == 3
    dlg = CombineTracesDialog(refs)
    assert _error_choice(dlg) == ("band", "std")               # the new default
    dlg.display_combo.setCurrentIndex(2)
    dlg.error_combo.setCurrentIndex(1)
    assert _error_choice(dlg) == ("both", "sem")
    win._combine_selected_traces(sm, refs, x, ys, "Line", True, "band", "sem")
    new = sm.subplots[0].traces[-1] if sm.subplots[0].traces[-1].y_col == "mean" else \
        next(t for t in sm.subplots[0].traces if t.y_col == "mean")
    assert (new.show_band, new.show_errorbar, new.yerr_mode) == (True, False, "sem")
    assert new.yerr_sem_col == "sem" and new.has_error_data
    obj = win.repository[new.data_id]
    assert "sem" in obj.column_order
    np.testing.assert_allclose(obj.columns["sem"], obj.columns["std"] / np.sqrt(3))
    # the sheet renders it (band + line) without errors
    ps = win.open_tabs[sm.sheet_id]
    ps.render(win.repository, win.images)
    assert any(c.get_alpha() == pytest.approx(new.band_alpha) for c in ps._axes[0].collections)


def test_split_one_trace_into_repeats_gets_the_band_too(win):
    from NoorSuite.app import SplitAggregateDialog, _error_choice
    win.handle_incoming_ipc({"action": "append_dataframe", "name": "rep", "columns": {
        "x": np.repeat([1.0, 2.0, 3.0], 3), "y": np.array([1, 2, 3, 2, 3, 4, 3, 4, 5], float)},
        "column_order": ["x", "y"]})
    win.handle_incoming_ipc({"action": "add_to_sheet", "data_name": "rep", "x_col": "x", "y_cols": ["y"],
                             "sheet": "__new__", "subplot_index": 0})
    sm = win._active_sheet_model()
    ref = sm.subplots[0].traces[0]
    obj = win.repository[ref.data_id]
    dlg = SplitAggregateDialog(ref, len(obj.get("y")))
    assert _error_choice(dlg)[0] == "band"
    win._split_and_aggregate_trace(sm, ref, obj.get("x"), obj.get("y"), 3, "block", "Line", True,
                                   "both", "minmax")
    new = sm.subplots[0].traces[0]
    assert (new.show_band, new.show_errorbar, new.yerr_mode) == (True, True, "minmax")


def test_client_plot_mean_over_objects_and_over_columns(tmp_path):
    suite = OfflineClient(tmp_path / "p.sciproj")
    x = np.linspace(0, 5, 20)
    for k in range(4):
        suite.push_dataframe(pd.DataFrame({"x": x, "y": np.sin(x) + 0.2 * k}), name=f"run{k}")
    name = suite.plot_mean([f"run{k}" for k in range(4)], "x", "y", band="sem", new_sheet=True,
                           title="Mean", label="sin", style={"color": "#d62728"})
    pm = ProjectModel.load(tmp_path / "p.sciproj")
    obj = next(o for o in pm.data_objects if o.name == name)
    assert obj.column_order == ["x", "mean", "std", "sem", "err_min", "err_max"] and "n=4" in name
    t = next(t for sm in pm.sheets for sub in sm.subplots for t in sub.traces if t.data_id == obj.id)
    assert (t.show_band, t.show_errorbar, t.yerr_mode, t.color, t.label) == (True, False, "sem", "#d62728", "sin")
    np.testing.assert_allclose(obj.columns["sem"], obj.columns["std"] / 2.0)
    # one object, several columns, with whiskers as well
    suite.push_dataframe(pd.DataFrame({"t": x, "a": x, "b": x + 1, "c": x + 3}), name="wide")
    n2 = suite.plot_mean("wide", "t", ["a", "b", "c"], band="minmax", errorbars=True, sheet="Mean")
    pm = ProjectModel.load(tmp_path / "p.sciproj")
    t2 = next(t for sm in pm.sheets for sub in sm.subplots for t in sub.traces
              if pm and any(o.id == t.data_id and o.name == n2 for o in pm.data_objects))
    assert (t2.show_band, t2.show_errorbar, t2.yerr_mode) == (True, True, "minmax")
    # no band at all: just the mean (+ optional whiskers)
    n3 = suite.plot_mean("wide", "t", ["a", "b"], band=None, sheet="Mean")
    assert n3 != n2
    with pytest.raises(ValueError):
        suite.plot_mean("wide", "t", ["a"], sheet="Mean")             # one series is no mean
    with pytest.raises(ValueError):
        suite.plot_mean([f"run0", "wide"], "x", "y", sheet="Mean")    # no shared x / column
    with pytest.raises(ValueError):
        suite.plot_mean("wide", "t", ["a", "b"], band="nonsense")
