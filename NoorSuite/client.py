"""DataFrame-first client for driving a running SciSuite window from Jupyter.

Typical use::

    from NoorSuite import SciSuiteClient
    suite = SciSuiteClient().launch()

    suite.push_dataframe(df, name="IV_sweep")           # register a data object
    suite.plot("IV_sweep", x="time_s", y=["current_mA", "voltage_V"], new_sheet=True)
    suite.list_data()                                   # what is in the pool
    suite.list_traces()                                 # what is on the sheets
"""
from __future__ import annotations

import subprocess
import sys

import numpy as np
import pandas as pd

from .protocol import (ACTION_ADD_TO_SHEET, ACTION_APPEND_DATAFRAME,
                       ACTION_APPEND_TRACE, ACTION_CLEAR, ACTION_LIST_DATA,
                       ACTION_LIST_TRACES, ACTION_REMOVE_DATA, DEFAULT_PORT,
                       IPCClient)

_DATA_COLUMNS = ["id", "name", "columns", "nrows", "tags", "source"]
_TRACE_COLUMNS = ["data_id", "data_name", "y_col", "x_col", "sheet", "subplot",
                  "enabled", "plot_type", "color"]


class SciSuiteClient:
    """Client API for interactive use in Jupyter notebooks."""

    def __init__(self, port: int = DEFAULT_PORT):
        self.port = port
        self.process = None
        self._ipc = IPCClient(port)

    # ------------------------------------------------------------------ lifecycle
    def launch(self) -> "SciSuiteClient":
        """Attach to a running SciSuite window, or start one as a background process."""
        if self._ipc.is_alive():
            print(f"[link] Reconnected to existing SciSuite window on port {self.port}.")
            return self
        self.process = subprocess.Popen(
            [sys.executable, "-m", "NoorSuite", "--gui", str(self.port)]
        )
        print(f"[launch] Started SciSuite process on port {self.port}.")
        return self

    @property
    def connected(self) -> bool:
        return self._ipc.is_alive()

    def __repr__(self) -> str:
        return f"<SciSuiteClient port={self.port} {'connected' if self.connected else 'offline'}>"

    # ---------------------------------------------------------------------- push
    def push_dataframe(self, df, *, name=None, tags=None, units=None, mode="new"):
        """Register a DataFrame as one data object in the pool (no plotting).

        ``mode="update"`` refreshes the columns of an existing object with the same
        ``name`` in place, so traces already referencing it re-render.
        """
        df = pd.DataFrame(df)
        columns, order = {}, []

        if df.index.name and pd.api.types.is_numeric_dtype(df.index):
            key = str(df.index.name)
            columns[key] = np.asarray(df.index.values, dtype=float).tolist()
            order.append(key)

        for col in df.columns:
            if pd.api.types.is_numeric_dtype(df[col]):
                columns[str(col)] = np.asarray(df[col].values, dtype=float).tolist()
                order.append(str(col))

        return self._ipc.send({
            "action": ACTION_APPEND_DATAFRAME,
            "name": name or "data",
            "columns": columns,
            "column_order": order,
            "units": dict(units or {}),
            "tags": list(tags or []),
            "mode": mode,
        })

    def push_series(self, s, name=None, tags=None, units=None, mode="new"):
        """Register a :class:`pandas.Series` as a one-column data object."""
        s = pd.Series(s)
        col = name or (s.name if s.name is not None else "series")
        return self.push_dataframe(s.rename(col).to_frame(), name=col, tags=tags,
                                   units=units, mode=mode)

    def push_dataset(self, name, df, **kwargs):
        """Alias for :meth:`push_dataframe` with an explicit ``name``."""
        return self.push_dataframe(df, name=name, **kwargs)

    def push_trace(self, name, x, y, x_unit="", y_unit="", tags=None,
                   color=None, plot_type="Line"):
        """Register a single (x, y) pair and drop it on the active subplot."""
        return self._ipc.send({
            "action": ACTION_APPEND_TRACE,
            "name": name,
            "x": np.asarray(x, dtype=float).tolist(),
            "y": np.asarray(y, dtype=float).tolist(),
            "x_unit": x_unit,
            "y_unit": y_unit,
            "tags": list(tags or []),
            "color": color,
            "plot_type": plot_type,
        })

    # ---------------------------------------------------------------------- plot
    def plot(self, data, x, y, *, name=None, sheet=None, subplot=0, new_sheet=False):
        """Add columns of a data object as traces to a sheet.

        ``data`` is a data-object name (already pushed) or a DataFrame (pushed now
        under ``name``). ``x`` is a column name or ``None`` (row index). ``y`` is a
        column name or a list. ``sheet`` targets by name / id, defaults to the active
        sheet; ``new_sheet=True`` forces a fresh sheet.
        """
        if isinstance(data, str):
            data_name = data
        else:
            data_name = name or "data"
            self.push_dataframe(data, name=data_name)
        y_cols = [y] if isinstance(y, str) else list(y)
        target = "__new__" if new_sheet else (sheet or "__active__")
        return self._ipc.send({
            "action": ACTION_ADD_TO_SHEET,
            "data_name": data_name,
            "x_col": x or "__index__",
            "y_cols": y_cols,
            "sheet": target,
            "subplot_index": int(subplot),
        })

    # ------------------------------------------------------------------- inspect
    def list_data(self) -> pd.DataFrame:
        """Return a summary of every data object in the pool."""
        resp = self._ipc.send({"action": ACTION_LIST_DATA}) or {}
        return pd.DataFrame(resp.get("data", []), columns=_DATA_COLUMNS)

    def list_traces(self) -> pd.DataFrame:
        """Return every trace currently placed on a sheet."""
        resp = self._ipc.send({"action": ACTION_LIST_TRACES}) or {}
        return pd.DataFrame(resp.get("traces", []), columns=_TRACE_COLUMNS)

    def remove_data(self, key):
        """Remove a data object (and every trace referencing it) by id or name."""
        return self._ipc.send({"action": ACTION_REMOVE_DATA, "key": key})

    def clear(self):
        """Remove every data object and every trace."""
        return self._ipc.send({"action": ACTION_CLEAR})


def launch(port: int = DEFAULT_PORT) -> SciSuiteClient:
    """Create a :class:`SciSuiteClient` and attach/launch in one call."""
    return SciSuiteClient(port).launch()
