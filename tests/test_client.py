import pandas as pd

from NoorSuite.client import SciSuiteClient
from NoorSuite.protocol import (ACTION_ADD_TO_SHEET, ACTION_APPEND_DATAFRAME,
                               ACTION_APPEND_TRACE)


class _Recorder:
    """Stands in for SciSuiteClient._ipc and captures every payload."""

    def __init__(self):
        self.payloads = []

    def send(self, payload, timeout=5.0):
        self.payloads.append(payload)
        return {"status": "success"}

    def is_alive(self):
        return True

    @property
    def last(self):
        return self.payloads[-1]


def _client():
    client = SciSuiteClient()
    rec = _Recorder()
    client._ipc = rec
    return client, rec


def test_push_dataframe_registers_one_object_with_all_numeric_columns():
    client, rec = _client()
    df = pd.DataFrame({"t": [0.0, 1.0, 2.0], "a": [1.0, 2.0, 3.0],
                       "b": [4.0, 5.0, 6.0], "label": ["x", "y", "z"]})
    client.push_dataframe(df, name="run1", tags=["r"])

    p = rec.last
    assert p["action"] == ACTION_APPEND_DATAFRAME
    assert p["name"] == "run1"
    assert p["column_order"] == ["t", "a", "b"]          # non-numeric dropped
    assert p["columns"]["a"] == [1.0, 2.0, 3.0]
    assert p["tags"] == ["r"]
    assert p["mode"] == "new"


def test_push_dataframe_includes_named_numeric_index():
    client, rec = _client()
    df = pd.DataFrame({"a": [1.0, 2.0]},
                      index=pd.Index([10.0, 20.0], name="freq"))
    client.push_dataframe(df, name="sweep")
    p = rec.last
    assert p["column_order"] == ["freq", "a"]
    assert p["columns"]["freq"] == [10.0, 20.0]


def test_plot_sends_add_to_sheet_after_optional_push():
    client, rec = _client()
    df = pd.DataFrame({"t": [0, 1, 2], "a": [1, 2, 3], "c": [3, 2, 1]})
    client.plot(df, x="t", y=["a", "c"], name="run2", new_sheet=True)

    assert rec.payloads[0]["action"] == ACTION_APPEND_DATAFRAME
    add = rec.payloads[1]
    assert add["action"] == ACTION_ADD_TO_SHEET
    assert add["data_name"] == "run2"
    assert add["x_col"] == "t"
    assert add["y_cols"] == ["a", "c"]
    assert add["sheet"] == "__new__"
    assert add["subplot_index"] == 0


def test_plot_by_name_does_not_push():
    client, rec = _client()
    client.plot("existing", x=None, y="a", subplot=2)
    assert len(rec.payloads) == 1
    add = rec.last
    assert add["action"] == ACTION_ADD_TO_SHEET
    assert add["x_col"] == "__index__"
    assert add["y_cols"] == ["a"]
    assert add["sheet"] == "__active__"
    assert add["subplot_index"] == 2


def test_push_series_uses_series_name():
    client, rec = _client()
    client.push_series(pd.Series([1.0, 2.0, 3.0], name="signal"))
    p = rec.last
    assert p["action"] == ACTION_APPEND_DATAFRAME
    assert p["column_order"] == ["signal"]


def test_push_trace_low_level_payload():
    client, rec = _client()
    client.push_trace("iv", [0, 1, 2], [3, 4, 5], plot_type="Scatter")
    p = rec.last
    assert p["action"] == ACTION_APPEND_TRACE
    assert p["name"] == "iv"
    assert p["x"] == [0.0, 1.0, 2.0]
    assert p["plot_type"] == "Scatter"


def test_list_helpers_return_dataframes():
    client, rec = _client()
    rec.send = lambda payload, timeout=5.0: {  # type: ignore[assignment]
        "status": "success",
        "data": [{"id": "d1", "name": "run", "columns": ["t", "a"],
                  "nrows": 3, "tags": [], "source": "dataframe"}],
        "traces": [{"data_id": "d1", "data_name": "run", "y_col": "a", "x_col": "t",
                    "sheet": "Sheet 1", "subplot": 0, "enabled": True,
                    "plot_type": "Line", "color": "#111"}],
    }
    assert list(client.list_data()["name"]) == ["run"]
    assert list(client.list_traces()["y_col"]) == ["a"]
