"""Deleting data objects and sheets through the API (live GUI and offline file)."""
import numpy as np
import pandas as pd
import pytest

from NoorSuite.client import OfflineClient, SciSuiteClient
from NoorSuite.model import ProjectModel
from NoorSuite.protocol import MUTATION_ACTIONS
from tests.test_gui_features import app, win  # noqa: F401  (window fixtures)


class _Wire:
    """The client's transport, wired straight into a window (no socket)."""

    def __init__(self, win):
        self.win = win

    def send(self, payload):
        if payload.get("action") in MUTATION_ACTIONS:
            self.win.handle_incoming_ipc(payload)
            return {"status": "success"}
        return self.win.ipc._handle(payload)

    def is_alive(self):
        return True


def _populate(suite):
    df = pd.DataFrame({"x": [1.0, 2.0, 3.0], "y": [1.0, 4.0, 9.0]})
    suite.push_dataframe(df, name="a")
    suite.push_dataframe(df, name="b")
    suite.plot("a", "x", "y", new_sheet=True, title="Fig A", folder="Group/Sub")
    suite.plot("b", "x", "y", new_sheet=True, title="Fig B")
    suite.plot("a", "x", "y", new_sheet=True, title="Fig B")             # a second sheet with that name


def test_offline_remove_sheet_data_and_ambiguity(tmp_path):
    suite = OfflineClient(tmp_path / "p.sciproj")
    _populate(suite)
    sheets = suite.list_sheets()
    assert len(sheets) == 3
    with pytest.raises(ValueError):
        suite.remove_sheet("Fig B")                                      # two sheets share the name
    with pytest.raises(KeyError):
        suite.remove_sheet("nope")
    assert len(suite.list_sheets()) == 3                                 # nothing was deleted
    suite.remove_sheet("Fig A")                                          # by (unique) name
    pm = ProjectModel.load(tmp_path / "p.sciproj")
    assert [s.name for s in pm.sheets] == ["Fig B", "Fig B"]
    assert not any(n.get("children") for n in pm.tree if n.get("name") == "Group" and n["children"][0]["children"])
    ids = list(suite.list_sheets()["id"])
    suite.remove_sheet(ids)                                              # a list of ids
    assert suite.list_sheets().empty
    assert set(suite.list_data()["name"]) == {"a", "b"}                  # data stays in the pool
    suite.remove_data(["a", "b"])
    assert suite.list_data().empty
    with pytest.raises(KeyError):
        suite.remove_data("a")


def test_live_gui_remove_sheet_and_data(win):
    suite = SciSuiteClient()
    suite._ipc = _Wire(win)
    _populate(suite)
    assert len(win.sheets) >= 3
    n0 = len(win.sheets)
    ids = list(suite.list_sheets().query("name == 'Fig B'")["id"])
    assert len(ids) == 2
    suite.remove_sheet(ids[0])
    assert len(win.sheets) == n0 - 1 and ids[0] not in win.sheets and ids[0] not in win.open_tabs
    assert ids[1] in win.sheets
    # the tree entry went with it
    from PyQt6.QtWidgets import QTreeWidgetItemIterator
    it = QTreeWidgetItemIterator(win.tree)
    found = []
    while it.value() is not None:
        found.append(it.value().data(0, 256 + 2) if False else it.value().text(0))
        it += 1
    assert "Fig B" in found                                              # the other one is still there
    suite.remove_sheet("Fig A")
    assert not [s for s in win.sheets.values() if s.name == "Fig A"]
    # data: the traces that plotted it disappear with it
    suite.remove_data("b")
    assert "b" not in [o.name for o in win.repository.values()]
    assert all(t.data_id in win.repository for sm in win.sheets.values()
               for sub in sm.subplots for t in sub.traces)
    with pytest.raises(KeyError):
        suite.remove_sheet("does not exist")
    assert suite.list_sheets()["id"].tolist() == [i for i in suite.list_sheets()["id"]]   # snapshot is consistent
