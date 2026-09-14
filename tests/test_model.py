import numpy as np
import pytest

from NoorSuite.model import (INDEX_COL, PROJECT_VERSION, REL_INDEX_PREFIX,
                            REL_VALUE_PREFIX, ColorMap, DataObject, ImageObject,
                            ImageRef, ProjectModel, SheetModel, SubplotModel,
                            TraceRef, aggregate_series, apply_y_transform,
                            common_label, resolve_sidecar_names)


def _obj(name="run", n=6):
    t = np.linspace(0, 1, n)
    return DataObject(name, {"t": t, "a": t ** 2, "b": t * 10},
                      column_order=["t", "a", "b"],
                      units={"t": "s", "a": "V"}, tags=["x"], source="dataframe")


def test_dataobject_roundtrip_and_head():
    obj = _obj(n=5)
    restored = DataObject.from_dict(obj.to_dict())
    assert restored.name == "run"
    assert restored.column_order == ["t", "a", "b"]
    assert restored.nrows == 5
    assert restored.ncols == 3
    np.testing.assert_allclose(restored.columns["a"], obj.columns["a"])
    headers, rows = restored.head(3)
    assert headers == ["t", "a", "b"]
    assert len(rows) == 3 and len(rows[0]) == 3


def test_dataobject_get_index_and_update_in_place():
    obj = _obj(n=4)
    np.testing.assert_array_equal(obj.get(INDEX_COL), [0, 1, 2, 3])
    np.testing.assert_array_equal(obj.get(""), [0, 1, 2, 3])

    obj.update_from({"a": [9, 9, 9, 9], "c": [1, 2, 3, 4]})
    assert obj.column_order == ["t", "a", "b", "c"]      # existing kept, new appended
    np.testing.assert_allclose(obj.columns["a"], [9, 9, 9, 9])
    np.testing.assert_allclose(obj.columns["c"], [1, 2, 3, 4])


def test_traceref_roundtrip_and_resolve():
    obj = _obj(n=5)
    ref = TraceRef(obj.id, "t", "a")
    ref.plot_type = "Scatter"
    ref.color = "#abcdef"
    ref.scale_factor = "1e3"

    restored = TraceRef.from_dict(ref.to_dict())
    assert restored.plot_type == "Scatter"
    assert restored.color == "#abcdef"
    assert restored.display_label == "a"

    x, y = restored.resolve(obj)
    np.testing.assert_allclose(x, obj.columns["t"])
    np.testing.assert_allclose(y, obj.columns["a"] * 1e3)


def test_traceref_resolve_with_index_x():
    obj = _obj(n=4)
    ref = TraceRef(obj.id, INDEX_COL, "b")
    x, y = ref.resolve(obj)
    np.testing.assert_array_equal(x, [0, 1, 2, 3])
    np.testing.assert_allclose(y, obj.columns["b"])


def test_aggregate_series_mean_std_minmax():
    agg = aggregate_series([[1.0, 10.0], [2.0, 20.0], [3.0, 30.0]])
    np.testing.assert_allclose(agg["mean"], [2.0, 20.0])
    np.testing.assert_allclose(agg["std"], [np.std([1.0, 2.0, 3.0], ddof=1),
                                            np.std([10.0, 20.0, 30.0], ddof=1)])
    np.testing.assert_allclose(agg["err_min"], [1.0, 10.0])   # mean - min
    np.testing.assert_allclose(agg["err_max"], [1.0, 10.0])   # max - mean


def test_aggregate_series_rejects_bad_input():
    with pytest.raises(ValueError):
        aggregate_series([[1.0, 2.0]])                     # only one series
    with pytest.raises(ValueError):
        aggregate_series([[1.0, 2.0], [1.0, 2.0, 3.0]])     # mismatched lengths


def test_common_label_prefix_and_suffix_and_fallback():
    assert common_label(["a_1", "a_2", "a_3"]) == "a"
    assert common_label(["run1_x", "run2_x"]) == "run"
    assert common_label(["foo", "bar"]) == "foo"


def test_traceref_resolve_yerr_std_and_minmax_modes_independent_of_show_errorbar():
    obj = DataObject("agg", {
        "t": [0.0, 1.0], "mean": [5.0, 6.0], "std": [0.5, 0.6],
        "err_min": [1.0, 1.2], "err_max": [2.0, 2.4],
    }, column_order=["t", "mean", "std", "err_min", "err_max"])
    ref = TraceRef(obj.id, "t", "mean")
    ref.plot_type = "Line"          # errorbars are an overlay, not a plot type
    ref.yerr_col = "std"
    ref.yerr_low_col = "err_min"
    ref.yerr_high_col = "err_max"
    assert ref.has_error_data

    ref.yerr_mode = "std"
    np.testing.assert_allclose(ref.resolve_yerr(obj), [0.5, 0.6])

    ref.yerr_mode = "minmax"
    lo_hi = ref.resolve_yerr(obj)
    np.testing.assert_allclose(lo_hi[0], [1.0, 1.2])
    np.testing.assert_allclose(lo_hi[1], [2.0, 2.4])

    # resolve_yerr is available regardless of show_errorbar -- that flag only gates
    # whether rendering draws it
    assert ref.show_errorbar is False
    assert ref.resolve_yerr(obj) is not None


def test_traceref_no_error_columns_has_no_error_data():
    ref = TraceRef("d1", "t", "a")
    assert not ref.has_error_data
    obj = DataObject("d", {"t": [0.0], "a": [1.0]}, column_order=["t", "a"])
    assert ref.resolve_yerr(obj) is None


def test_traceref_resolve_yerr_scales_with_linear_scale_factor():
    obj = DataObject("agg", {"t": [0.0], "mean": [1.0], "std": [0.1]},
                     column_order=["t", "mean", "std"])
    ref = TraceRef(obj.id, "t", "mean")
    ref.yerr_col = "std"
    ref.scale_factor = "1e3"
    np.testing.assert_allclose(ref.resolve_yerr(obj), [100.0])


def test_traceref_roundtrip_preserves_yerr_and_show_errorbar_fields():
    obj = _obj(n=3)
    ref = TraceRef(obj.id, "t", "a")
    ref.show_errorbar = True
    ref.yerr_col = "err"
    ref.yerr_low_col = "lo"
    ref.yerr_high_col = "hi"
    ref.yerr_mode = "minmax"
    restored = TraceRef.from_dict(ref.to_dict())
    assert restored.show_errorbar is True
    assert restored.yerr_col == "err"
    assert restored.yerr_low_col == "lo"
    assert restored.yerr_high_col == "hi"
    assert restored.yerr_mode == "minmax"


def test_relative_change_value_reference_fraction_and_percent():
    y = [50.0, 100.0, 150.0]
    frac = apply_y_transform(y, f"{REL_VALUE_PREFIX}frac:100")
    np.testing.assert_allclose(frac, [-0.5, 0.0, 0.5])
    pct = apply_y_transform(y, f"{REL_VALUE_PREFIX}pct:100")
    np.testing.assert_allclose(pct, [-50.0, 0.0, 50.0])


def test_relative_change_index_reference_is_per_series():
    # each series normalized to ITS OWN value at row 0 -- different baselines
    a = apply_y_transform([10.0, 20.0, 30.0], f"{REL_INDEX_PREFIX}frac:0")
    b = apply_y_transform([5.0, 10.0, 15.0], f"{REL_INDEX_PREFIX}frac:0")
    np.testing.assert_allclose(a, [0.0, 1.0, 2.0])
    np.testing.assert_allclose(b, [0.0, 1.0, 2.0])   # same relative growth, different abs values


def test_relative_change_handles_zero_reference_gracefully():
    y = [0.0, 1.0, 2.0]
    out = apply_y_transform(y, f"{REL_VALUE_PREFIX}frac:0")
    np.testing.assert_allclose(out, [0.0, 1.0, 2.0])   # denom falls back to 1.0, no crash


def test_yerr_scale_linear_for_rel_value_not_for_rel_index():
    obj = DataObject("agg", {"t": [0.0], "mean": [50.0], "std": [10.0]},
                     column_order=["t", "mean", "std"])
    ref = TraceRef(obj.id, "t", "mean")
    ref.yerr_col = "std"

    ref.scale_factor = f"{REL_VALUE_PREFIX}pct:50"    # (y-ref)/ref*100 -> multiplier 100/50=2
    np.testing.assert_allclose(ref.resolve_yerr(obj), [20.0])

    ref.scale_factor = f"{REL_VALUE_PREFIX}frac:50"   # (y-ref)/ref -> multiplier 1/50
    np.testing.assert_allclose(ref.resolve_yerr(obj), [0.2])

    # REL_INDEX's reference comes from the data, not the token -- no fixed multiplier
    # can be derived from scale_factor alone, so the error is left unscaled (1.0)
    ref.scale_factor = f"{REL_INDEX_PREFIX}frac:0"
    np.testing.assert_allclose(ref.resolve_yerr(obj), [10.0])


def test_subplotmodel_roundtrip_includes_traces_grid_and_image():
    s = SubplotModel("Panel")
    s.y_min, s.y_max = -1.0, 2.5
    s.spine_width = 2.0
    s.spine_style = "--"
    s.grid_axis = "x"
    s.grid_ticks = "both"
    s.grid_style = ":"
    s.grid_color = "#123456"
    s.grid_alpha = 0.25
    s.traces = [TraceRef("d1", "t", "a"), TraceRef("d1", INDEX_COL, "b", enabled=False)]
    s.image = ImageRef("img1", [1, 2], {0: 3})
    s.image.cmap = "magma"
    s.image.colorbar = True

    restored = SubplotModel.from_dict(s.to_dict())
    assert restored.y_min == -1.0 and restored.y_max == 2.5
    assert restored.spine_width == 2.0 and restored.spine_style == "--"
    assert restored.grid_axis == "x" and restored.grid_ticks == "both"
    assert restored.grid_style == ":" and restored.grid_color == "#123456"
    assert restored.grid_alpha == 0.25
    assert [t.y_col for t in restored.traces] == ["a", "b"]
    assert restored.traces[1].enabled is False
    assert restored.image.data_id == "img1"
    assert restored.image.display_axes == [1, 2]
    assert restored.image.index == {0: 3}
    assert restored.image.cmap == "magma" and restored.image.colorbar is True


def test_subplotmodel_defaults_have_no_image_and_look_unchanged():
    s = SubplotModel()
    assert s.image is None
    assert s.grid_axis == "both" and s.grid_ticks == "major"
    assert s.grid_style == "--" and abs(s.grid_alpha - 0.5) < 1e-9
    assert s.aspect == "auto" and s.aspect_ratio == 1.0


def test_subplotmodel_custom_aspect_ratio_roundtrips():
    s = SubplotModel()
    s.aspect = "custom"
    s.aspect_ratio = 2.5
    restored = SubplotModel.from_dict(s.to_dict())
    assert restored.aspect == "custom" and restored.aspect_ratio == 2.5


def test_subplotmodel_tick_format_defaults_and_roundtrips():
    s = SubplotModel()
    assert s.x_tick_format == "auto" and s.y_tick_format == "auto"
    assert s.x_tick_digits == 2 and s.y_tick_digits == 2
    s.x_tick_format, s.x_tick_digits = "scientific", 3
    s.y_tick_format, s.y_tick_digits = "fixed", 0
    restored = SubplotModel.from_dict(s.to_dict())
    assert restored.x_tick_format == "scientific" and restored.x_tick_digits == 3
    assert restored.y_tick_format == "fixed" and restored.y_tick_digits == 0


def test_imageobject_slice_3d_and_4d():
    a3 = np.arange(5 * 4 * 6).reshape(5, 4, 6)
    obj3 = ImageObject("stack", a3, axis_names=["z", "y", "x"])
    sl = obj3.slice((1, 2), {0: 3})
    assert sl.shape == (4, 6)
    np.testing.assert_array_equal(sl, a3[3])

    # swapped display order -> transposed
    sl_t = obj3.slice((2, 1), {0: 3})
    assert sl_t.shape == (6, 4)
    np.testing.assert_array_equal(sl_t, a3[3].T)

    a4 = np.arange(2 * 3 * 4 * 5).reshape(2, 3, 4, 5)
    obj4 = ImageObject("stack4", a4)
    sl4 = obj4.slice((2, 3), {0: 1, 1: 2})
    assert sl4.shape == (4, 5)
    np.testing.assert_array_equal(sl4, a4[1, 2])


def test_imageobject_metadata_roundtrip():
    obj = ImageObject("img", np.zeros((3, 4, 5), dtype="float32"),
                      axis_names=["z", "y", "x"], tags=["t"])
    d = obj.to_dict()
    assert d["shape"] == [3, 4, 5] and d["dtype"] == "float32"
    restored = ImageObject.from_dict(d)
    assert restored.name == "img" and restored.axis_names == ["z", "y", "x"]
    assert restored.shape == (3, 4, 5)


def test_sheetmodel_roundtrip_keeps_id_frame_tags_notes_and_colormaps():
    sh = SheetModel("S", 1, 2)
    sh.fig_frame_on = True
    sh.fig_edge_width = 2.5
    sh.fig_edge_style = ":"
    sh.tags = ["overview", "PL"]
    sh.notes = "Measured 2024-06-01.\nPeak shifts red with anneal temp."
    sh.active_colormap = "viridis"
    sh.colormaps = [ColorMap("mine", ["#111111", "#222222"])]
    restored = SheetModel.from_dict(sh.to_dict())
    assert restored.sheet_id == sh.sheet_id
    assert restored.fig_frame_on is True
    assert restored.fig_edge_width == 2.5
    assert restored.fig_edge_style == ":"
    assert restored.tags == ["overview", "PL"]
    assert restored.notes == sh.notes
    assert restored.active_colormap == "viridis"
    assert [c.name for c in restored.colormaps] == ["mine"]
    assert restored.colormaps[0].colors == ["#111111", "#222222"]
    assert restored.rows == 1 and restored.cols == 2 and len(restored.subplots) == 2


def test_sheetmodel_export_size_defaults_to_auto_and_roundtrips():
    sh = SheetModel("S")
    assert sh.fig_width_cm is None and sh.fig_height_cm is None   # auto by default
    sh.fig_width_cm, sh.fig_height_cm = 12.0, 8.5
    restored = SheetModel.from_dict(sh.to_dict())
    assert restored.fig_width_cm == 12.0 and restored.fig_height_cm == 8.5


def test_project_roundtrip_with_tree_and_colormaps():
    project = ProjectModel()
    obj = _obj()
    project.data_objects = [obj]
    sh = SheetModel("Sheet 1", 2, 1)
    sh.subplots[0].traces = [TraceRef(obj.id, "t", "a")]
    project.sheets = [sh]
    project.tree = [{"type": "folder", "id": "f1", "name": "Group",
                     "children": [{"type": "sheet", "sheet_id": sh.sheet_id}]}]
    project.colormaps = [ColorMap("libmap", ["#aaaaaa", "#bbbbbb"])]
    project.active_sheet_id = sh.sheet_id

    restored = ProjectModel.from_json(project.to_json())
    assert [d.name for d in restored.data_objects] == ["run"]
    assert restored.sheets[0].subplots[0].traces[0].y_col == "a"
    assert restored.tree[0]["children"][0]["sheet_id"] == sh.sheet_id
    assert [c.name for c in restored.colormaps] == ["libmap"]
    assert restored.active_sheet_id == sh.sheet_id


def test_project_rejects_wrong_version():
    payload = ProjectModel().to_dict()
    payload["version"] = 5
    with pytest.raises(ValueError):
        ProjectModel.from_dict(payload)


def test_current_version_is_six():
    assert PROJECT_VERSION == 6


def test_project_save_load_roundtrips_image_array_via_sidecar(tmp_path):
    project = ProjectModel()
    arr = (np.random.default_rng(0).random((4, 5, 6)) * 100).astype("float32")
    img = ImageObject("stack", arr, axis_names=["z", "y", "x"])
    project.images = [img]
    sh = SheetModel("S", 1, 1)
    sh.subplots[0].image = ImageRef(img.id, [1, 2], {0: 2})
    project.sheets = [sh]

    path = tmp_path / "proj.sciproj"
    project.save(path)
    sidecar = tmp_path / "proj" / "stack.npy"
    assert sidecar.is_file()                     # named after the image, no id noise

    restored = ProjectModel.load(path)
    assert restored.images[0].shape == (4, 5, 6)
    np.testing.assert_allclose(restored.images[0].data, arr)
    assert restored.sheets[0].subplots[0].image.data_id == img.id

    # to_json alone (no sidecar) keeps metadata but drops the array
    meta = ProjectModel.from_json(project.to_json())
    assert meta.images[0].shape == (4, 5, 6)
    assert meta.images[0].data.sum() == 0


def test_project_save_sweeps_orphaned_and_renamed_image_sidecars(tmp_path):
    a = ImageObject("scan a", np.zeros((3, 3)))
    b = ImageObject("scan b", np.zeros((3, 3)))
    project = ProjectModel()
    project.images = [a, b]
    path = tmp_path / "proj.sciproj"
    project.save(path)
    assets = tmp_path / "proj"
    assert (assets / "scan_a.npy").is_file()
    assert (assets / "scan_b.npy").is_file()

    # a legacy id-named sidecar from an older build is also swept
    (assets / f"img_{a.id}.npy").write_bytes(b"stale")

    # rename one, drop the other -> save rewrites the renamed sidecar, sweeps the rest
    a.name = "scan a renamed"
    a._dirty = True
    project.images = [a]
    project.save(path)
    assert sorted(p.name for p in assets.glob("*.npy")) == ["scan_a_renamed.npy"]

    # last image gone -> the whole sidecar folder is removed
    project.images = []
    project.save(path)
    assert not assets.exists()


def test_resolve_sidecar_names_disambiguates_only_on_collision():
    a = ImageObject("Scan #3 / raw (2026)", np.zeros((2, 2)))
    b = ImageObject("scan", np.zeros((2, 2)))
    c = ImageObject("scan", np.zeros((2, 2)))
    names = resolve_sidecar_names([a, b, c])
    assert names[a.id] == "Scan_3_raw_2026.npy"     # unsafe chars stripped, spaces -> _
    assert names[b.id] == f"scan_{b.id}.npy"        # b and c collide -> both get the id
    assert names[c.id] == f"scan_{c.id}.npy"
    assert ImageObject("", np.zeros((2, 2))).sidecar_name() == "image.npy"
