"""Big data pools: binary sidecars, skipped saves, thinned lines, lazy re-rendering, no-copy IPC."""
import json
import os
import time

import numpy as np
import pytest

from NoorSuite import model as M
from NoorSuite.model import (AssetModel, DataObject, ImageObject, ProjectModel, SheetModel,
                             thin_line)
from tests.test_gui_features import app, win  # noqa: F401  (window fixtures)


# ------------------------------------------------------------------ thinning
def test_thin_line_keeps_peaks_order_and_ends():
    n = 200_000
    x = np.linspace(0, 10, n)
    y = np.sin(x * 20) * 0.5
    y[123_456] = 7.0                                  # one narrow spike must survive
    y[150_001] = -9.0
    tx, ty = thin_line(x, y, 1500)
    assert len(tx) <= 4 * 1500 + 8 and len(tx) < n // 10
    assert 7.0 in ty and -9.0 in ty
    assert tx[0] == x[0] and tx[-1] == x[-1]
    assert np.all(np.diff(tx) > 0)                    # still a monotonic path
    # every kept point is a real data point
    assert np.all(np.isin(ty, y))
    # descending x works too
    rx, ry = thin_line(x[::-1], y[::-1], 1500)
    assert np.all(np.diff(rx) < 0) and 7.0 in ry


def test_thin_line_leaves_unsafe_data_alone():
    n = 100_000
    x = np.linspace(0, 1, n)
    y = np.random.default_rng(0).normal(size=n)
    short = thin_line(x[:5000], y[:5000], 1500)
    assert len(short[0]) == 5000
    y_nan = y.copy()
    y_nan[10] = np.nan
    assert len(thin_line(x, y_nan, 1500)[0]) == n
    shuffled = np.random.default_rng(1).permutation(x)
    assert len(thin_line(shuffled, y, 1500)[0]) == n
    # zoomed in (explicit limits) -> finer, never coarser
    coarse = len(thin_line(x, y, 1500, 1.0)[0])
    fine = len(thin_line(x, y, 1500, 0.1)[0])
    assert fine > coarse


# ------------------------------------------------------------------ project files
def _project(n=20_000):
    pm = ProjectModel()
    rng = np.random.default_rng(3)
    big = DataObject("big", {"x": np.arange(n, dtype=float), "y": rng.normal(size=n)})
    small = DataObject("small", {"a": [1.0, 2.0, 3.0]})
    pm.data_objects = [big, small]
    pm.assets = [AssetModel("logo", "svg", b"<svg xmlns='http://www.w3.org/2000/svg'/>" * 50)]
    pm.images = [ImageObject("img", np.arange(24, dtype=float).reshape(4, 6))]
    pm.sheets = [SheetModel("S")]
    return pm, big, small


def test_big_data_and_figure_files_are_binary_sidecars(tmp_path):
    pm, big, small = _project()
    path = tmp_path / "p.sciproj"
    cache = {}
    pm.save(path, cache)
    raw = json.loads(path.read_text(encoding="utf-8"))
    entry = {d["name"]: d for d in raw["data_objects"]}
    assert entry["big"]["columns"] == {} and entry["big"]["data_file"].endswith(f"{big.id}.npz")
    assert entry["small"]["columns"]["a"] == [1.0, 2.0, 3.0]            # tiny data stays readable
    assert raw["assets"][0]["data"] == "" and raw["assets"][0]["data_file"]
    assert path.stat().st_size < 20_000                                  # numbers are not in the JSON
    assert (tmp_path / "p" / "_data" / f"{big.id}.npz").is_file()
    assert not list(tmp_path.rglob("*.tmp"))                             # atomic writes leave nothing
    back = ProjectModel.load(path)
    b = next(o for o in back.data_objects if o.name == "big")
    assert np.array_equal(b.columns["y"], big.columns["y"]) and b.column_order == ["x", "y"]
    assert back.assets[0].data == pm.assets[0].data
    assert np.array_equal(back.images[0].data, pm.images[0].data)


def test_unchanged_data_is_not_rewritten_and_orphans_are_swept(tmp_path):
    pm, big, small = _project()
    path = tmp_path / "p.sciproj"
    cache = {}
    pm.save(path, cache)
    npz = tmp_path / "p" / "_data" / f"{big.id}.npz"
    m0 = npz.stat().st_mtime_ns
    time.sleep(0.02)
    assert pm.prepare_save(path, cache).jobs == []                       # nothing to write
    pm.save(path, cache)
    assert npz.stat().st_mtime_ns == m0
    big.update_from({"y": np.zeros(20_000)})                             # a column is replaced
    plan = pm.prepare_save(path, cache)
    assert len(plan.jobs) == 1
    plan.run()
    assert np.array_equal(ProjectModel.load(path).data_objects[0].columns["y"], np.zeros(20_000))
    pm.data_objects = [small]
    pm.assets = []
    pm.save(path, cache)
    assert not npz.exists() and not (tmp_path / "p" / "_files").exists()


def test_older_inline_projects_still_load(tmp_path):
    pm, big, _ = _project(6000)
    path = tmp_path / "old.sciproj"
    path.write_text(pm.to_json(), encoding="utf-8")                      # everything inline (v6 as before)
    back = ProjectModel.load(path)
    assert np.array_equal(back.data_objects[0].columns["x"], big.columns["x"])
    assert back.assets[0].data == pm.assets[0].data


def test_signature_follows_changes_cheaply():
    pm, big, small = _project()
    s0 = pm.signature()
    assert pm.signature() == s0
    pm.sheets[0].tags = ["new"]
    assert pm.signature() != s0
    s1 = pm.signature()
    big.update_from({"x": np.arange(20_000, dtype=float)})
    assert pm.signature() != s1


# ------------------------------------------------------------------ window
def _big_window(win, n=30_000):
    x = np.linspace(0, 10, n)
    win.handle_incoming_ipc({"action": "append_dataframe", "name": "big",
                             "columns": {"x": x, "y": np.sin(x)}, "column_order": ["x", "y"]})
    return x


def test_autosave_writes_only_what_changed(win, tmp_path):
    win.session_file = str(tmp_path / "s.json")
    _big_window(win)
    win._save_now(win.session_file)
    p = tmp_path / "s.json"
    m0 = p.stat().st_mtime_ns
    time.sleep(0.02)
    win._save_now(win.session_file)                                      # identical -> skipped
    assert p.stat().st_mtime_ns == m0
    win._background_save(win.session_file)                               # also skipped, no thread
    th = win._autosave_threads.get(win.session_file)
    assert th is None or not th.is_alive()
    assert p.stat().st_mtime_ns == m0
    win.sheets[next(iter(win.sheets))].tags = ["changed"]
    win._save_now(win.session_file)
    assert p.stat().st_mtime_ns != m0
    assert json.loads(p.read_text(encoding="utf-8"))["sheets"][0]["tags"] == ["changed"]


def test_ipc_snapshot_shares_the_arrays_and_images_stay_bytes(win):
    from NoorSuite.protocol import ACTION_GET_DATA, ACTION_GET_IMAGE
    _big_window(win)
    obj = next(iter(win.repository.values()))
    entry = win._ipc_data_full[obj.id]
    assert entry["columns"]["x"] is obj.columns["x"]                    # no copy, no tolist()
    reply = win.ipc._handle({"action": ACTION_GET_DATA, "key": "big"})
    assert reply["status"] == "success" and len(reply["columns"]["y"]) == 30_000
    arr = np.arange(12, dtype="int16").reshape(3, 4)
    win.handle_incoming_ipc({"action": "append_image", "name": "im", "shape": [3, 4],
                             "dtype": "int16", "bytes": arr.tobytes(), "axis_names": ["r", "c"]})
    rep = win.ipc._handle({"action": ACTION_GET_IMAGE, "key": "im"})
    assert np.frombuffer(rep["bytes"], dtype="int16").reshape(3, 4).tolist() == arr.tolist()
    assert "array" not in rep


def test_pushing_data_does_not_rerender_every_open_sheet(win, monkeypatch):
    from NoorSuite.sheet import PlotSheet
    _big_window(win, 2000)
    for i in range(2):
        win.handle_incoming_ipc({"action": "add_to_sheet", "data_name": "big", "x_col": "x",
                                 "y_cols": ["y"], "sheet": "__new__", "subplot_index": 0,
                                 "organize": {"name": f"S{i}"}})
    assert len(win.open_tabs) >= 2
    calls = []
    real = PlotSheet.render
    monkeypatch.setattr(PlotSheet, "render", lambda self, *a, **k: (calls.append(self), real(self, *a, **k))[1])
    x = np.linspace(0, 1, 100)
    win.handle_incoming_ipc({"action": "append_dataframe", "name": "other",
                             "columns": {"x": x, "y": x}, "column_order": ["x", "y"]})
    assert calls == []                                                   # a new object changes no figure
    win.handle_incoming_ipc({"action": "append_dataframe", "name": "big", "mode": "update",
                             "columns": {"x": np.linspace(0, 10, 2000), "y": np.cos(np.linspace(0, 10, 2000))},
                             "column_order": ["x", "y"]})
    cur = win.current_sheet()
    assert calls.count(cur) == 1                                         # the visible sheet only ...
    big_id = next(o.id for o in win.repository.values() if o.name == "big")
    hidden = [ps for ps in win.open_tabs.values()
              if ps is not cur and win._sheet_uses(ps.model, {big_id})]
    assert hidden and all(getattr(ps, "_stale", False) for ps in hidden)
    calls.clear()
    win.tab_widget.setCurrentWidget(hidden[0])                           # ... the others when shown
    assert hidden[0] in calls and not getattr(hidden[0], "_stale", True)
    calls.clear()
    win.tab_widget.setCurrentWidget(cur)
    win.tab_widget.setCurrentWidget(hidden[0])
    assert calls == [cur] or calls == []                                 # unchanged sheets are not redrawn twice


def test_data_digest_is_cached_until_the_columns_change(win):
    _big_window(win, 5000)
    obj = next(iter(win.repository.values()))
    d1 = win._data_digest(obj)
    assert win._data_digest(obj) is d1                                   # served from the cache
    obj.update_from({"y": np.zeros(5000)})
    assert win._data_digest(obj) != d1


def test_window_save_and_reload_round_trip(win, tmp_path):
    x = _big_window(win, 25_000)
    win.handle_incoming_ipc({"action": "add_to_sheet", "data_name": "big", "x_col": "x", "y_cols": ["y"],
                             "sheet": "__new__", "subplot_index": 0, "organize": {"name": "Fig"}})
    a = win.import_asset(data=b"<svg xmlns='http://www.w3.org/2000/svg' width='4' height='4'/>", ext="svg",
                         name="logo")
    path = str(tmp_path / "proj.sciproj")
    assert win._write_project(path)
    back = ProjectModel.load(path)
    assert not back.missing
    o = next(d for d in back.data_objects if d.name == "big")
    assert np.array_equal(o.columns["x"], x) and np.array_equal(o.columns["y"], np.sin(x))
    assert [sm.name for sm in back.sheets if sm.name == "Fig"] and back.assets[0].asset_id == a.asset_id
    assert back.assets[0].data == a.data
    # opening it in a window restores the same objects and seeds the cache (no rewrite of the npz)
    win._load_project_model(back)
    back.seed_cache(path, win._cache_for(path))
    assert win._cache_for(path)["data"]


def test_autosave_thread_then_change_then_save_stays_consistent(win, tmp_path):
    win.session_file = str(tmp_path / "s.json")
    x = _big_window(win, 30_000)
    win._background_save(win.session_file)
    win._autosave_threads[win.session_file].join()
    obj = next(iter(win.repository.values()))
    obj.update_from({"y": np.cos(x)})                                   # the data changes afterwards
    win._background_save(win.session_file)
    win._autosave_threads[win.session_file].join()
    back = ProjectModel.load(win.session_file)
    assert np.array_equal(back.data_objects[0].columns["y"], np.cos(x))


def test_missing_sidecar_files_are_reported_not_silent(tmp_path):
    pm, big, _ = _project()
    path = tmp_path / "p.sciproj"
    pm.save(path)
    (tmp_path / "p" / "_data" / f"{big.id}.npz").unlink()
    back = ProjectModel.load(path)
    assert back.missing and "big" in back.missing[0]


def _same(a: ProjectModel, b: ProjectModel):
    da = {o.name: o for o in a.data_objects}
    db = {o.name: o for o in b.data_objects}
    assert da.keys() == db.keys()
    for k in da:
        assert da[k].column_order == db[k].column_order and da[k].tags == db[k].tags
        for c in da[k].column_order:
            assert np.array_equal(da[k].columns[c], db[k].columns[c]), (k, c)
    assert {x.name: x.data for x in a.assets} == {x.name: x.data for x in b.assets}
    assert [s_.to_dict() for s_ in a.sheets] == [s_.to_dict() for s_ in b.sheets]
    assert len(a.images) == len(b.images)
    for x, y in zip(a.images, b.images):
        assert np.array_equal(x.data, y.data)


@pytest.mark.parametrize("seed", range(6))
def test_random_edit_save_load_sequences_never_lose_or_mix_data(tmp_path, seed):
    """The save cache skips files that did not change -- fuzz it against a full reload."""
    rng = np.random.default_rng(seed)
    pm = ProjectModel()
    path = tmp_path / "f.sciproj"
    cache = {}
    counter = 0
    for step in range(40):
        op = rng.choice(["add", "update", "remove", "asset", "rm_asset", "sheet", "image", "save_only"])
        if op == "add" or not pm.data_objects:
            counter += 1
            n = int(rng.choice([5, 50, 6000, 9000]))
            pm.data_objects.append(DataObject(f"d{counter}", {"x": rng.normal(size=n), "y": rng.normal(size=n)}))
        elif op == "update":
            o = pm.data_objects[int(rng.integers(len(pm.data_objects)))]
            n = int(rng.choice([10, 5000, 7000]))
            o.update_from({"y": rng.normal(size=n), "x": rng.normal(size=n)})
        elif op == "remove" and pm.data_objects:
            pm.data_objects.pop(int(rng.integers(len(pm.data_objects))))
        elif op == "asset":
            counter += 1
            pm.assets.append(AssetModel(f"a{counter}", "svg", bytes(rng.integers(0, 255, 300).tolist()), version=1))
        elif op == "rm_asset" and pm.assets:
            pm.assets.pop(int(rng.integers(len(pm.assets))))
        elif op == "sheet":
            pm.sheets.append(SheetModel(f"s{step}"))
        elif op == "image":
            counter += 1
            pm.images.append(ImageObject(f"im{counter}", rng.normal(size=(4, 5))))
        if rng.random() < 0.7:
            pm.save(path, cache)
            _same(pm, ProjectModel.load(path))
    pm.save(path, cache)
    _same(pm, ProjectModel.load(path))
    # no orphans: every sidecar belongs to a live object
    folder = tmp_path / "f"
    if folder.exists():
        live = {f"{o.id}.npz" for o in pm.data_objects}
        assert {f.name for f in (folder / "_data").glob("*")} <= live
        liveA = {f"{a.asset_id}.{a.ext}" for a in pm.assets}
        assert {f.name for f in (folder / "_files").glob("*")} <= liveA
