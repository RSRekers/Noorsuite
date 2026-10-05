import numpy as np
import pandas as pd

from NoorSuite.client import OfflineClient
from NoorSuite.model import ProjectModel


def _suite(tmp_path):
    return OfflineClient(tmp_path / "p.sciproj")


def test_offline_insert_plot_organize_roundtrip(tmp_path):
    suite = _suite(tmp_path)
    df = pd.DataFrame({"V": [0.0, 1.0, 2.0], "I": [0.0, 0.5, 2.0], "s": list("abc")})
    suite.push_dataframe(df, name="IV A3", tags=["iv"], notes="Keithley")
    suite.plot("IV A3", x="V", y="I", new_sheet=True, title="IV curve", tags=["a3"],
               notes="hysteresis", folder="Sample A3/Transport", subplot_title="I vs V",
               x_label="Voltage (V)", y_label="Current (mA)")
    suite.annotate_data("IV A3", tags=["IV", "verified"], notes="checked")

    pm = ProjectModel.load(tmp_path / "p.sciproj")
    obj = pm.data_objects[0]
    assert obj.columns.keys() == {"V", "I"} and obj.tags == ["iv", "verified"]
    assert obj.notes == "Keithley\n\nchecked"
    sm = pm.sheets[0]
    assert (sm.name, sm.tags, sm.notes) == ("IV curve", ["a3"], "hysteresis")
    sub = sm.subplots[0]
    assert (sub.title, sub.x_label, sub.y_label) == ("I vs V", "Voltage (V)", "Current (mA)")
    assert len(sub.traces) == 1
    folder = pm.tree[0]
    assert folder["name"] == "Sample A3" and folder["children"][0]["children"][0] == \
        {"type": "sheet", "sheet_id": sm.sheet_id}
    assert suite.list_sheets().iloc[0]["folder"] == "Sample A3/Transport"


def test_organize_moves_sheet_and_reuses_folder(tmp_path):
    suite = _suite(tmp_path)
    suite.push_dataframe(pd.DataFrame({"x": [1.0, 2.0], "y": [1.0, 2.0]}), name="d")
    suite.plot("d", "x", "y", new_sheet=True, folder="A")
    suite.plot("d", "x", "y", new_sheet=True, folder="A", title="second")
    suite.organize_sheet("second", folder="B/C")
    pm = ProjectModel.load(tmp_path / "p.sciproj")
    assert [n["name"] for n in pm.tree] == ["A", "B"]
    assert len(pm.tree[0]["children"]) == 1


def test_offline_errors_and_images(tmp_path):
    suite = _suite(tmp_path)
    assert suite.plot("nope", "x", "y")["status"] == "error"
    suite.push_image(np.zeros((2, 3, 4)), name="stack", axis_names=["z", "y", "x"])
    suite.show_image("stack", new_sheet=True)
    assert suite.get_image("stack").shape == (2, 3, 4)
    suite.remove_data("stack")
    assert suite.list_images().empty


def test_plot_type_style_and_sort_options(tmp_path):
    import pytest
    suite = _suite(tmp_path)
    suite.push_dataframe(pd.DataFrame({"x": [3.0, 1.0, 2.0], "y": [9.0, 1.0, 4.0]}), name="d")
    suite.plot("d", "x", "y", new_sheet=True, plot_type="Scatter", sort=True,
               style={"color": "#ff0000", "marker_size": 9})
    pm = ProjectModel.load(tmp_path / "p.sciproj")
    ref = pm.sheets[0].subplots[0].traces[0]
    assert (ref.plot_type, ref.sort_x, ref.color, ref.marker_size) == \
        ("Scatter", True, "#ff0000", 9)
    x, y = ref.resolve(pm.data_objects[0])
    assert list(x) == [1.0, 2.0, 3.0] and list(y) == [1.0, 4.0, 9.0]
    with pytest.raises(ValueError):
        suite.plot("d", "x", "y", plot_type="Bubble")
    with pytest.raises(ValueError):
        suite.plot("d", "x", "y", style={"colour": "red"})


def test_heatmap_offline_roundtrip_and_options(tmp_path):
    import pytest
    suite = _suite(tmp_path)
    df = pd.DataFrame([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]], index=[10.0, 20.0],
                      columns=[0.1, 0.2, 0.3])
    suite.heatmap(df, name="T map", new_sheet=True, title="T vs f", cmap="RdBu", reverse_cmap=True,
                  bins=4, isolines=[2.0, 5.0], iso_above=3, xticks=2, yticks=[10, 20],
                  x_label="f", folder="Maps")
    pm = ProjectModel.load(tmp_path / "p.sciproj")
    im = pm.images[0]
    assert im.axis_coords == {0: [10.0, 20.0], 1: [0.1, 0.2, 0.3]} and im.is_heatmap
    ref = pm.sheets[0].subplots[0].image
    assert (ref.aspect, ref.cmap, ref.cmap_reverse, ref.cmap_bins) == ("auto", "RdBu", True, 4)
    assert (ref.iso_show, ref.iso_levels, ref.iso_above) == (True, "2, 5", 3.0)
    assert (ref.x_tick_mode, ref.x_tick_every, ref.y_tick_mode, ref.y_tick_values) == \
        ("data", 2, "custom", "10, 20")
    assert pm.sheets[0].name == "T vs f" and pm.sheets[0].subplots[0].x_label == "f"
    assert bool(suite.list_images().iloc[0]["heatmap"])

    long = pd.DataFrame({"a": [1, 2, 1, 2], "b": [3, 3, 4, 4], "v": [1.0, 2.0, 3.0, 4.0]})
    suite.push_heatmap_xyz(long, "a", "b", "v", name="pivot")
    assert suite.get_image("pivot").tolist() == [[1.0, 2.0], [3.0, 4.0]]
    cat = pd.DataFrame([[1.0, 0.5], [0.5, 1.0]], index=["u", "v"], columns=["u", "v"])
    suite.push_heatmap(cat, name="corr")
    assert ProjectModel.load(tmp_path / "p.sciproj").images[2].axis_coords[1] == ["u", "v"]
    with pytest.raises(ValueError):
        suite.push_heatmap(np.zeros((2, 2)), x=[1, 2, 3], name="bad")
    with pytest.raises(ValueError):
        suite.show_image("corr", style={"colour": "red"})


def test_tick_label_options_reach_the_subplot(tmp_path):
    suite = _suite(tmp_path)
    suite.push_dataframe(pd.DataFrame({"x": [0.0, 1.0, 2.0], "y": [1.0, 2.0, 3.0]}), name="d")
    suite.plot("d", "x", "y", new_sheet=True, x_tick_labels=["RT", "4 K", "1.5 K"],
               y_tick_labels={1: "low", 3: "high"})
    cat = pd.DataFrame([[1.0, 0.5], [0.5, 1.0]], index=["a", "b"], columns=["a", "b"])
    suite.heatmap(cat, name="corr", new_sheet=True, x_tick_labels={"a": "Alpha"})
    pm = ProjectModel.load(tmp_path / "p.sciproj")
    sub = pm.sheets[0].subplots[0]
    assert (sub.x_tick_labels, sub.y_tick_labels) == ("0=RT; 1=4 K; 2=1.5 K", "1=low; 3=high")
    assert pm.sheets[1].subplots[0].x_tick_labels == "a=Alpha"
