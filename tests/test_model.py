import numpy as np
import pytest

from NoorSuite.model import (INDEX_COL, PROJECT_VERSION, ColorMap, DataObject,
                            ProjectModel, SheetModel, SubplotModel, TraceRef)


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


def test_subplotmodel_roundtrip_includes_traces_and_new_fields():
    s = SubplotModel("Panel")
    s.y_min, s.y_max = -1.0, 2.5
    s.spine_width = 2.0
    s.spine_style = "--"
    s.traces = [TraceRef("d1", "t", "a"), TraceRef("d1", INDEX_COL, "b", enabled=False)]

    restored = SubplotModel.from_dict(s.to_dict())
    assert restored.y_min == -1.0 and restored.y_max == 2.5
    assert restored.spine_width == 2.0 and restored.spine_style == "--"
    assert [t.y_col for t in restored.traces] == ["a", "b"]
    assert restored.traces[1].enabled is False


def test_sheetmodel_roundtrip_keeps_id_frame_tags_and_colormaps():
    sh = SheetModel("S", 1, 2)
    sh.fig_frame_on = True
    sh.fig_edge_width = 2.5
    sh.fig_edge_style = ":"
    sh.tags = ["overview", "PL"]
    sh.active_colormap = "viridis"
    sh.colormaps = [ColorMap("mine", ["#111111", "#222222"])]
    restored = SheetModel.from_dict(sh.to_dict())
    assert restored.sheet_id == sh.sheet_id
    assert restored.fig_frame_on is True
    assert restored.fig_edge_width == 2.5
    assert restored.fig_edge_style == ":"
    assert restored.tags == ["overview", "PL"]
    assert restored.active_colormap == "viridis"
    assert [c.name for c in restored.colormaps] == ["mine"]
    assert restored.colormaps[0].colors == ["#111111", "#222222"]
    assert restored.rows == 1 and restored.cols == 2 and len(restored.subplots) == 2


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
    payload["version"] = 3
    with pytest.raises(ValueError):
        ProjectModel.from_dict(payload)


def test_current_version_is_four():
    assert PROJECT_VERSION == 4
