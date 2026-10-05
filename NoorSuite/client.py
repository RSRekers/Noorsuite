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
from contextlib import contextmanager

import numpy as np
import pandas as pd

from .model import PLOT_TYPES, ImageRef, format_tick_labels, heatmap_from_xyz
from .protocol import (ACTION_ADD_IMAGE_TO_SHEET, ACTION_ADD_TO_SHEET,
                       ACTION_APPEND_DATAFRAME, ACTION_APPEND_IMAGE,
                       ACTION_APPEND_TRACE, ACTION_CLEAR, ACTION_GET_DATA,
                       ACTION_GET_IMAGE, ACTION_LIST_DATA, ACTION_LIST_IMAGES,
                       ACTION_LIST_SHEETS, ACTION_LIST_TRACES, ACTION_ORGANIZE,
                       ACTION_REMOVE_DATA,
                       DEFAULT_PORT, IPCClient)

_DATA_COLUMNS = ["id", "name", "columns", "nrows", "tags", "source"]
_IMAGE_COLUMNS = ["id", "name", "shape", "dtype", "axis_names", "tags", "source",
                  "heatmap"]
_TRACE_COLUMNS = ["data_id", "data_name", "y_col", "x_col", "sheet", "subplot",
                  "enabled", "plot_type", "color"]
_SHEET_COLUMNS = ["id", "name", "rows", "cols", "subplots", "tags", "duplicate_name",
                  "folder"]


_TRACE_OPTIONS = ("color", "edge_color", "line_style", "line_width", "marker",
                  "marker_size", "alpha")


def _trace_style(plot_type, style, sort) -> dict:
    """Validated TraceRef style overrides for ``plot()`` (empty dict -> defaults)."""
    out = dict(style or {})
    bad = [k for k in out if k not in _TRACE_OPTIONS]
    if bad:
        raise ValueError(f"unknown style option(s) {bad}; use {list(_TRACE_OPTIONS)}")
    if plot_type is not None:
        if plot_type not in PLOT_TYPES:
            raise ValueError(f"plot_type {plot_type!r} not in {PLOT_TYPES}")
        out["plot_type"] = plot_type
    if sort:
        out["sort_x"] = True
    return out


def _heatmap_style(cmap, reverse_cmap, vmin, vmax, bins, boundaries, colorbar, interpolation,
                   isolines, iso_above, iso_below, iso_color, iso_width, iso_labels,
                   iso_label_fmt, xticks, yticks) -> dict:
    """Friendly ``heatmap()`` arguments -> ``ImageRef`` style fields (only what was given)."""
    st = {"colorbar": bool(colorbar), "cmap": cmap, "cmap_reverse": reverse_cmap,
          "vmin": vmin, "vmax": vmax, "interpolation": interpolation,
          "iso_above": iso_above, "iso_below": iso_below, "iso_color": iso_color,
          "iso_width": iso_width, "iso_labels": iso_labels, "iso_label_fmt": iso_label_fmt}
    if bins is not None:
        st["cmap_bins"] = int(bins)
    if boundaries is not None:
        st["cmap_boundaries"] = ", ".join(f"{float(b):g}" for b in boundaries)
    if isolines is not None and isolines is not False:
        st["iso_show"] = True
        if isinstance(isolines, (int, float)) and not isinstance(isolines, bool):
            st["iso_count"] = int(isolines)
        elif not isinstance(isolines, bool):
            st["iso_levels"] = ", ".join(f"{float(v):g}" for v in isolines)
    for prefix, ticks in (("x", xticks), ("y", yticks)):
        if ticks is None:
            continue
        if ticks == "data":
            st[f"{prefix}_tick_mode"] = "data"
        elif isinstance(ticks, (int, float)) and not isinstance(ticks, bool):
            st[f"{prefix}_tick_mode"], st[f"{prefix}_tick_every"] = "data", max(1, int(ticks))
        else:
            st[f"{prefix}_tick_mode"] = "custom"
            st[f"{prefix}_tick_values"] = ", ".join(f"{float(v):g}" for v in ticks)
    return {k: v for k, v in st.items() if v is not None}


def _organize_payload(title, tags, notes, folder, subplot, subplot_title, x_label,
                      y_label, x_tick_labels=None, y_tick_labels=None) -> dict:
    sub = {k: v for k, v in (("title", subplot_title), ("x_label", x_label),
                             ("y_label", y_label),
                             ("x_tick_labels", format_tick_labels(x_tick_labels)),
                             ("y_tick_labels", format_tick_labels(y_tick_labels))) if v}
    return {"name": title or "", "tags": list(tags or []), "notes": notes or "",
            "folder": folder or "", "subplots": {int(subplot): sub} if sub else {}}


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
    def push_dataframe(self, df, *, name=None, tags=None, units=None, mode="new",
                       notes=None):
        """Register a DataFrame as one data object in the pool (no plotting).

        ``mode="update"`` refreshes the columns of an existing object with the same
        ``name`` in place, so traces already referencing it re-render. ``tags`` / ``notes``
        (what the data is, how it was measured/processed) make it findable in the GUI search.
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
            "notes": notes or "",
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

    # --------------------------------------------------------------------- images
    def push_image(self, arr, *, name, axis_names=None, axis_units=None, tags=None,
                   mode="new", axis_coords=None):
        """Register an ND array as an image object in the pool (no display).

        ``axis_coords`` ({axis: values}) gives an axis real coordinates (numbers, or
        strings for a categorical axis) instead of the pixel index -- that is what turns
        an image into a heatmap; see :meth:`push_heatmap`."""
        arr = np.ascontiguousarray(arr)
        return self._ipc.send({
            "action": ACTION_APPEND_IMAGE,
            "name": name,
            "bytes": arr.tobytes(),
            "shape": list(arr.shape),
            "dtype": str(arr.dtype),
            "axis_names": list(axis_names) if axis_names else None,
            "axis_units": dict(axis_units or {}),
            "axis_coords": {int(a): list(v) for a, v in (axis_coords or {}).items()},
            "tags": list(tags or []),
            "mode": mode,
        })

    # ------------------------------------------------------------------- heatmaps
    def push_heatmap(self, z, x=None, y=None, *, name, x_name=None, y_name=None,
                     tags=None, mode="new"):
        """Register a 2-D grid as a *heatmap*: an image whose cells sit at real
        coordinates. ``z`` is a 2-D array (rows = y, columns = x) or a DataFrame (index =
        y values, columns = x values, e.g. a ``pivot``); ``x`` / ``y`` give the coordinate
        of every column / row (numbers, or strings for a categorical axis) and default to
        the DataFrame's labels, else the pixel index."""
        if isinstance(z, pd.DataFrame):
            x = list(z.columns) if x is None else x
            y = list(z.index) if y is None else y
            x_name = x_name or (str(z.columns.name) if z.columns.name else None)
            y_name = y_name or (str(z.index.name) if z.index.name else None)
        arr = np.asarray(z, dtype=float)
        if arr.ndim != 2:
            raise ValueError(f"a heatmap needs a 2-D grid, got shape {arr.shape}")
        coords = {}
        for axis, vals in ((0, y), (1, x)):
            if vals is not None:
                vals = list(np.asarray(vals).tolist()) if not isinstance(vals, list) else vals
                if len(vals) != arr.shape[axis]:
                    raise ValueError(f"{'y' if axis == 0 else 'x'} has {len(vals)} values "
                                     f"but the grid has {arr.shape[axis]} "
                                     f"{'rows' if axis == 0 else 'columns'}")
                coords[axis] = vals
        return self.push_image(arr, name=name, axis_names=[y_name or "y", x_name or "x"],
                               tags=tags, mode=mode, axis_coords=coords)

    def push_heatmap_xyz(self, df, x, y, z, *, name, tags=None, mode="new"):
        """Register long-format samples (one row per ``x, y, z`` triple) as a heatmap:
        the grid of unique x / y values, mean ``z`` per cell, NaN where there is none."""
        grid, xs, ys = heatmap_from_xyz(df[x], df[y], df[z])
        return self.push_heatmap(grid, xs.tolist(), ys.tolist(), name=name, x_name=str(x),
                                 y_name=str(y), tags=tags, mode=mode)

    def heatmap(self, data, *, x=None, y=None, name=None, x_name=None, y_name=None,
                cmap=None, reverse_cmap=None, vmin=None, vmax=None, bins=None,
                boundaries=None, colorbar=True, interpolation=None,
                isolines=None, iso_above=None, iso_below=None, iso_color=None,
                iso_width=None, iso_labels=None, iso_label_fmt=None,
                xticks=None, yticks=None, sheet=None, subplot=0, new_sheet=False,
                title=None, tags=None, notes=None, folder=None, subplot_title=None,
                x_label=None, y_label=None, x_tick_labels=None, y_tick_labels=None):
        """Push (if given a grid) and show a heatmap on a sheet subplot.

        ``data``: name of a pushed heatmap, or a 2-D array / DataFrame (see
        :meth:`push_heatmap` for ``x``, ``y``, ``name``). Colours: ``cmap`` (matplotlib
        name), ``reverse_cmap``, ``vmin`` / ``vmax``; **discrete colours** with ``bins=N``
        equal bands or explicit ``boundaries=[0, 10, 50, 100]`` (one colour per band).
        **Isolines**: ``isolines=True`` (automatic), an int (about that many) or a list of
        levels; only levels ``>= iso_above`` / ``<= iso_below`` are drawn; with discrete
        colours and no explicit levels, the lines follow the band edges. ``iso_color``,
        ``iso_width``, ``iso_labels`` (value labels on the lines). **Ticks**: ``xticks`` /
        ``yticks`` = ``"data"`` (one per coordinate), an int N (every Nth coordinate) or a
        list of positions. **Tick text**: ``x_tick_labels`` / ``y_tick_labels`` = ``{value:
        "text"}`` (ticks at those coordinates) or, on a categorical axis, ``{"category":
        "display name"}`` to rename categories. Sheet organization args as in :meth:`plot`."""
        if isinstance(data, str):
            hm_name = data
        else:
            hm_name = name or "heatmap"
            self.push_heatmap(data, x, y, name=hm_name, x_name=x_name, y_name=y_name)
        style = _heatmap_style(cmap, reverse_cmap, vmin, vmax, bins, boundaries, colorbar,
                               interpolation, isolines, iso_above, iso_below, iso_color,
                               iso_width, iso_labels, iso_label_fmt, xticks, yticks)
        return self.show_image(hm_name, sheet=sheet, subplot=subplot, new_sheet=new_sheet,
                               axes=(0, 1), style=style, title=title, tags=tags, notes=notes,
                               folder=folder, subplot_title=subplot_title, x_label=x_label,
                               y_label=y_label, x_tick_labels=x_tick_labels,
                               y_tick_labels=y_tick_labels)

    def show_image(self, data, *, name=None, axis_names=None, sheet=None, subplot=0,
                   axes=(-2, -1), new_sheet=False, style=None, title=None, tags=None,
                   notes=None, folder=None, subplot_title=None, x_label=None,
                   y_label=None, x_tick_labels=None, y_tick_labels=None):
        """Register (if given an array) and place an image on a sheet subplot.

        ``axes`` are the two axes to display as (rows, cols); negatives allowed.
        ``style`` is a dict of ``model.ImageRef.STYLE_FIELDS`` (colormap, vmin/vmax,
        isolines, ticks, ...); the sheet-organizing args are as in :meth:`plot`.
        """
        if style:
            bad = [k for k in style if k not in ImageRef.STYLE_FIELDS]
            if bad:
                raise ValueError(f"unknown image style option(s) {bad}; "
                                 f"use {list(ImageRef.STYLE_FIELDS)}")
        if isinstance(data, str):
            img_name = data
        else:
            arr = np.asarray(data)
            img_name = name or "image"
            self.push_image(arr, name=img_name, axis_names=axis_names)
            axes = tuple(a % arr.ndim for a in axes)
        target = "__new__" if new_sheet else (sheet or "__active__")
        return self._ipc.send({
            "action": ACTION_ADD_IMAGE_TO_SHEET,
            "name": img_name,
            "sheet": target,
            "subplot_index": int(subplot),
            "display_axes": [int(axes[0]), int(axes[1])],
            "image_style": dict(style or {}),
            "organize": _organize_payload(title, tags, notes, folder, subplot,
                                          subplot_title, x_label, y_label,
                                          x_tick_labels, y_tick_labels),
        })

    def list_images(self) -> pd.DataFrame:
        """Return a summary of every image object in the pool."""
        resp = self._ipc.send({"action": ACTION_LIST_IMAGES}) or {}
        return pd.DataFrame(resp.get("images", []), columns=_IMAGE_COLUMNS)

    def get_image(self, key) -> np.ndarray:
        """Retrieve an image object's ND array from the GUI (by id or name)."""
        resp = self._ipc.send({"action": ACTION_GET_IMAGE, "key": key}) or {}
        if resp.get("status") != "success":
            raise KeyError(resp.get("message", f"no image matching {key!r}"))
        return np.frombuffer(resp["bytes"], dtype=resp["dtype"]).reshape(resp["shape"])

    # ------------------------------------------------------------- pull / roundtrip
    def get_data(self, key) -> pd.DataFrame:
        """Retrieve a data object from the GUI as a DataFrame (by id or name).

        Edit it and push it back to keep the GUI in sync::

            df = suite.get_data("spectra")
            df["ratio"] = df["a"] / df["b"]
            suite.push_dataframe(df, name="spectra", mode="update")
        """
        resp = self._ipc.send({"action": ACTION_GET_DATA, "key": key}) or {}
        if resp.get("status") != "success":
            raise KeyError(resp.get("message", f"no data object matching {key!r}"))
        order = resp.get("column_order") or list(resp.get("columns", {}))
        return pd.DataFrame({c: resp["columns"][c] for c in order}, columns=order)

    get_dataframe = get_data

    @contextmanager
    def edit_data(self, key):
        """Pull, edit, and push back a data object in one block -- there is no live
        link between a DataFrame in your notebook and the object in the pool, so a
        change is only visible in the GUI once it's pushed back; this is that in one
        step instead of two, and can't forget the push::

            with suite.edit_data("spectra") as df:
                df["ratio"] = df["a"] / df["b"]
            # pushed back automatically here, open sheets already re-rendered

        Raises the same ``KeyError`` as :meth:`get_data` if ``key`` isn't found, and
        does *not* push if the block raises. ``key`` may be an id or a name --
        either way the push-back targets the object's actual name, so it lands back
        on the same object even if you looked it up by id.
        """
        resp = self._ipc.send({"action": ACTION_GET_DATA, "key": key}) or {}
        if resp.get("status") != "success":
            raise KeyError(resp.get("message", f"no data object matching {key!r}"))
        order = resp.get("column_order") or list(resp.get("columns", {}))
        df = pd.DataFrame({c: resp["columns"][c] for c in order}, columns=order)
        yield df
        self.push_dataframe(df, name=resp["name"], mode="update")

    # ---------------------------------------------------------------------- plot
    def plot(self, data, x, y, *, name=None, sheet=None, subplot=0, new_sheet=False,
             title=None, tags=None, notes=None, folder=None, subplot_title=None,
             x_label=None, y_label=None, plot_type=None, style=None, sort=False,
             x_tick_labels=None, y_tick_labels=None):
        """Add columns of a data object as traces to a sheet.

        ``data`` is a data-object name (already pushed) or a DataFrame (pushed now
        under ``name``). ``x`` is a column name or ``None`` (row index). ``y`` is a
        column name or a list. ``sheet`` targets by name / id, defaults to the active
        sheet; ``new_sheet=True`` forces a fresh sheet.

        Organizing the destination sheet in the same call: ``title`` (sheet name),
        ``tags``, ``notes``, ``folder`` (``"Project/Run 4"``, created if missing),
        ``subplot_title`` / ``x_label`` / ``y_label`` for the target subplot.

        Trace look, applied to every trace this call adds: ``plot_type`` (one of
        ``model.PLOT_TYPES``: "Line", "Scatter", "Line+Scatter", "Step", "Bar"), ``style``
        (dict of ``color``, ``line_style``, ``line_width``, ``marker``, ``marker_size``,
        ``alpha``, ``edge_color``), and ``sort=True`` to draw the points in ascending-x
        order (fixes a zig-zag when x is disordered; leave off for loops / hysteresis).

        ``x_tick_labels`` / ``y_tick_labels``: custom tick text, as ``{value: "text"}`` (ticks
        at those values) or a list of strings (placed at 0, 1, 2, ...), e.g.
        ``x_tick_labels={0: "RT", 1: "4 K", 2: "1.5 K"}``.
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
            "organize": _organize_payload(title, tags, notes, folder, subplot,
                                          subplot_title, x_label, y_label,
                                          x_tick_labels, y_tick_labels),
            "trace_style": _trace_style(plot_type, style, sort),
        })

    def organize_sheet(self, sheet, *, title=None, tags=None, notes=None, folder=None,
                       subplot=0, subplot_title=None, x_label=None, y_label=None,
                       replace_notes=False, x_tick_labels=None, y_tick_labels=None):
        """Name / tag / annotate / file an existing sheet (by id or name). Tags are merged
        in, notes appended (``replace_notes=True`` overwrites), ``folder`` moves it into
        that folder path, creating folders as needed."""
        org = _organize_payload(title, tags, notes, folder, subplot, subplot_title,
                                x_label, y_label, x_tick_labels, y_tick_labels)
        org["replace_notes"] = replace_notes
        return self._ipc.send({"action": ACTION_ORGANIZE, "kind": "sheet",
                               "target": sheet, **org})

    def annotate_data(self, key, *, tags=None, notes=None, replace_notes=False):
        """Add tags / notes to an existing data object (by id or name)."""
        return self._ipc.send({"action": ACTION_ORGANIZE, "kind": "data", "target": key,
                               "tags": list(tags or []), "notes": notes or "",
                               "replace_notes": replace_notes})

    # ------------------------------------------------------------------- inspect
    def list_data(self) -> pd.DataFrame:
        """Return a summary of every data object in the pool."""
        resp = self._ipc.send({"action": ACTION_LIST_DATA}) or {}
        return pd.DataFrame(resp.get("data", []), columns=_DATA_COLUMNS)

    def list_traces(self) -> pd.DataFrame:
        """Return every trace currently placed on a sheet."""
        resp = self._ipc.send({"action": ACTION_LIST_TRACES}) or {}
        return pd.DataFrame(resp.get("traces", []), columns=_TRACE_COLUMNS)

    def list_sheets(self) -> pd.DataFrame:
        """Return every sheet in the project: id, name, grid size, tags, and whether
        its name collides with another sheet's (``duplicate_name``). ``plot(...,
        sheet=...)`` matches by id first, then by name -- picking the first match
        when several sheets share a name -- so check this (or use the id) before
        targeting a sheet by name if you're not sure it's unique."""
        resp = self._ipc.send({"action": ACTION_LIST_SHEETS}) or {}
        return pd.DataFrame(resp.get("sheets", []), columns=_SHEET_COLUMNS)

    def remove_data(self, key):
        """Remove a data object (and every trace referencing it) by id or name."""
        return self._ipc.send({"action": ACTION_REMOVE_DATA, "key": key})

    def clear(self):
        """Remove every data object and every trace."""
        return self._ipc.send({"action": ACTION_CLEAR})


def launch(port: int = DEFAULT_PORT) -> SciSuiteClient:
    """Create a :class:`SciSuiteClient` and attach/launch in one call."""
    return SciSuiteClient(port).launch()


class OfflineClient(SciSuiteClient):
    """Same API as :class:`SciSuiteClient`, but writes to a project file because no GUI is
    running (see :mod:`NoorSuite.offline`). Create via :func:`connect`."""

    def __init__(self, project=None, port: int = DEFAULT_PORT):
        from .offline import OfflineBackend
        super().__init__(port)
        self._ipc = OfflineBackend(project)
        self.project_path = self._ipc.path

    def launch(self) -> "SciSuiteClient":
        """Start the GUI as a background process (it loads the session file on start)."""
        return SciSuiteClient(self.port).launch()

    def __repr__(self) -> str:
        return f"<OfflineClient project={self.project_path!r}>"


def connect(project=None, port: int = DEFAULT_PORT) -> SciSuiteClient:
    """Return a client that inserts data wherever it can: into the running GUI when one is
    listening on ``port`` (live, no file touched), otherwise straight into a project file --
    ``project`` (a ``.sciproj``) or, by default, the GUI's session file, which the GUI
    loads on its next start. Every method behaves the same either way."""
    live = SciSuiteClient(port)
    if live.connected:
        if project:
            print("[connect] A GUI is running -- inserting into its open project; "
                  f"{project!r} is ignored (open it in the GUI instead).")
        return live
    client = OfflineClient(project, port)
    print(f"[connect] No GUI on port {port} -- writing to {client.project_path}")
    return client
