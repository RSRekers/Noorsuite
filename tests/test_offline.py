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
