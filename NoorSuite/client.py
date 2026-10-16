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
                       ACTION_APPEND_TRACE, ACTION_CLEAR, ACTION_DOCUMENT,
                       ACTION_GET_DATA, ACTION_GET_DOCUMENT, ACTION_LIST_DOCUMENTS,
                       ACTION_LIST_REFERENCES, ACTION_REFERENCES, ACTION_ASSET,
                       ACTION_LIST_ASSETS, ACTION_TABLE, ACTION_LIST_TABLES,
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
                      y_label, x_tick_labels=None, y_tick_labels=None,
                      content_only=None) -> dict:
    sub = {k: v for k, v in (("title", subplot_title), ("x_label", x_label),
                             ("y_label", y_label),
                             ("x_tick_labels", format_tick_labels(x_tick_labels)),
                             ("y_tick_labels", format_tick_labels(y_tick_labels))) if v}
    if content_only is not None:
        sub["bare"] = bool(content_only)
    return {"name": title or "", "tags": list(tags or []), "notes": notes or "",
            "folder": folder or "", "subplots": {int(subplot): sub} if sub else {}}


_SHAPE_KINDS = ("rect", "ellipse", "line", "arrow", "xspan", "yspan", "text", "picture")
_SHAPE_ALIASES = {"rectangle": "rect", "box": "rect", "circle": "ellipse", "oval": "ellipse",
                  "hband": "yspan", "vband": "xspan", "span_x": "xspan", "span_y": "yspan",
                  "label": "text", "annotation": "text", "image": "picture",
                  "svg": "picture", "figure": "picture"}


def _shape_spec(kind, subplot, x0, x1, y0, y1, text, coords, position, name, fill, colors,
                stops, cmap, gradient_angle, center, radius, style, asset=None) -> dict:
    """One ``shapes`` entry of an organize payload (see ``model.apply_sheet_annotations``)."""
    kind = _SHAPE_ALIASES.get(str(kind).lower(), str(kind).lower())
    if kind not in _SHAPE_KINDS:
        raise ValueError(f"unknown shape kind {kind!r}; use one of {_SHAPE_KINDS}")
    geom = {k: v for k, v in (("x0", x0), ("x1", x1), ("y0", y0), ("y1", y1)) if v is not None}
    st = dict(style)
    if text is not None:
        st["text"] = text
    st["coords"] = coords
    if fill in (False, "none"):
        st["fill"] = False
    elif fill in ("solid", "linear", "radial"):
        st["fill"], st["fill_mode"] = True, fill
    elif fill is True:
        st["fill"] = True
    elif isinstance(fill, str):                         # a colour
        st["fill"], st["fill_color"] = True, fill
    if cmap and not (stops or colors):
        try:
            from matplotlib import colormaps
            from matplotlib.colors import to_hex
            cm = colormaps[cmap]
            colors = [to_hex(cm(t)) for t in (0.0, 0.25, 0.5, 0.75, 1.0)]
        except Exception as exc:
            raise ValueError(f"unknown colormap {cmap!r}") from exc
    if stops or colors:
        st["grad_stops"] = [list(s) for s in stops] if stops else list(colors)
        if fill not in ("linear", "radial"):
            st["fill"], st["fill_mode"] = True, "linear"
    if gradient_angle is not None:
        st["grad_angle"] = gradient_angle
    if center is not None:
        st["grad_cx"], st["grad_cy"] = center
    if radius is not None:
        st["grad_radius"] = radius
    if "rotation" in st:
        st["angle"] = st.pop("rotation")
    spec = {"subplot": int(subplot), "kind": kind, "geometry": geom, "style": st,
            "position": position, "name": name or ""}
    if asset:
        spec["asset"] = asset
        spec["kind"] = "picture"
    elif kind == "picture":
        raise ValueError("a picture needs asset=<name or id of a pushed figure file>")
    return spec


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
            columns[key] = np.ascontiguousarray(df.index.values, dtype=float)
            order.append(key)

        for col in df.columns:
            if pd.api.types.is_numeric_dtype(df[col]):
                columns[str(col)] = np.ascontiguousarray(df[col].values, dtype=float)
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
                   y_label=None, x_tick_labels=None, y_tick_labels=None,
                   content_only=None):
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
                                          x_tick_labels, y_tick_labels, content_only),
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
                       replace_notes=False, x_tick_labels=None, y_tick_labels=None,
                       content_only=None):
        """Name / tag / annotate / file an existing sheet (by id or name). Tags are merged
        in, notes appended (``replace_notes=True`` overwrites), ``folder`` moves it into
        that folder path, creating folders as needed. ``content_only=True`` hides the
        subplot's axes, ticks, labels, title, legend and background (just the drawn content,
        e.g. an image or picture)."""
        org = _organize_payload(title, tags, notes, folder, subplot, subplot_title,
                                x_label, y_label, x_tick_labels, y_tick_labels, content_only)
        org["replace_notes"] = replace_notes
        return self._ipc.send({"action": ACTION_ORGANIZE, "kind": "sheet",
                               "target": sheet, **org})

    def add_shape(self, sheet, kind="rect", *, subplot=0, x0=None, x1=None, y0=None, y1=None,
                  text=None, coords="data", position="front", name=None, fill=None,
                  colors=None, stops=None, cmap=None, gradient_angle=None, center=None,
                  radius=None, asset=None, parent=None, scale=None, **style):
        """Draw an object on a subplot of an existing sheet (by id or name).

        ``kind``: ``rect`` | ``ellipse`` | ``line`` | ``arrow`` | ``xspan`` (vertical band,
        ``x0``..``x1``) | ``yspan`` (horizontal band, ``y0``..``y1``) | ``text``. Corners /
        end points are ``x0, y0, x1, y1`` in data units (``coords="axes"`` = 0..1 of the
        panel). ``position`` puts it ``"front"`` (default) or ``"back"`` in the layer stack.

        Fill: ``fill="#ffcc00"`` solid colour, ``fill="linear" | "radial"`` plus a gradient
        through ``colors=["#fff", "#f00", ...]`` (evenly spaced key colours),
        ``stops=[(0, "#fff", 1.0), (0.6, "#f00", 0.5), (1, "#00f", 0.0)]`` (position, colour,
        opacity) or ``cmap="viridis"``; ``gradient_angle`` (deg) / ``center=(0.5, 0.5)`` /
        ``radius``. Other styles: ``edge_color``, ``line_width``, ``line_style``,
        ``alpha``, ``fill_alpha``, ``rotation``, ``fontsize``, ``arrow_head``.

        ``scale=1.5`` (or ``scale_x`` / ``scale_y``) multiplies the size around the centre;
        ``parent="<name of another object>"`` makes it move / rotate / scale along with that
        object (its root).

        A picture: ``add_shape(sheet, "picture", asset="logo", x0=.., x1=.., y0=.., y1=..)``
        with a figure file pushed by :meth:`push_asset` (an SVG can then be edited in
        Inkscape from the GUI)."""
        if scale is not None:
            style.setdefault("scale_x", scale)
            style.setdefault("scale_y", scale)
        spec = _shape_spec(kind, subplot, x0, x1, y0, y1, text, coords, position, name, fill,
                           colors, stops, cmap, gradient_angle, center, radius, style, asset)
        if parent:
            spec["parent"] = parent
        return self._ipc.send({"action": ACTION_ORGANIZE, "kind": "sheet", "target": sheet,
                               "shapes": [spec]})

    def push_asset(self, source, name=None, *, ext=None) -> dict:
        """Store a figure file (SVG / PDF / PNG / JPG) in the project -- a ``str`` / ``Path``
        to a file, or ``bytes`` plus ``ext="svg"``. Re-pushing the same ``name`` replaces it.
        Use it as ``\\noorfile{<name>}`` in a document or ``add_shape(..., asset=<name>)``."""
        import pathlib
        if isinstance(source, (bytes, bytearray)):
            data, ext = bytes(source), (ext or "svg").lower().lstrip(".")
            name = name or "figure"
        else:
            path = pathlib.Path(source)
            data = path.read_bytes()
            ext = (ext or path.suffix).lower().lstrip(".")
            name = name or path.stem
        ext = "jpg" if ext == "jpeg" else ext
        if ext not in ("svg", "pdf", "png", "jpg"):
            raise ValueError(f"unsupported figure format {ext!r} (svg, pdf, png, jpg)")
        return self._ipc.send({"action": ACTION_ASSET, "name": name, "ext": ext, "data": data})

    def push_table(self, name, data, columns=None, *, rows=None, step=1, max_rows=0,
                   sort_by="", descending=False, booktabs=True, digits=None, fmt=None):
        """Define a table of a data object for LaTeX / Word documents. Reference it in a
        document as ``\\noortable{<name>}`` (see :meth:`table_snippet`); it follows the data.

        ``columns``: names, ``(name, header)`` pairs or dicts ``{"col", "header", "fmt"
        ("auto"|"fixed"|"sig"|"sci"|"int"), "digits", "align" ("l"|"c"|"r"), "scale"}``;
        default every column (header = name + unit). ``rows=(first, last)`` 1-based,
        ``step``, ``max_rows``, ``sort_by`` / ``descending``. Re-pushing a name updates it."""
        from . import tables as _tables
        specs = _tables.normalize_columns(None, columns, digits, fmt) if columns else None
        opts = {"row_start": rows[0] if rows else 1, "row_end": rows[1] if rows else None,
                "step": step, "max_rows": max_rows, "sort_by": sort_by,
                "descending": descending, "booktabs": booktabs}
        return self._ipc.send({"action": ACTION_TABLE, "name": name, "data": data,
                               "columns": specs if specs is not None else "__all__",
                               "options": opts, "digits": digits, "fmt": fmt})

    def table_snippet(self, name, caption="", label="") -> str:
        """A ``table`` environment with the caption and ``\\noortable{<name>}`` inside."""
        from . import tables as _tables
        return _tables.table_snippet(_tables._slug(name) or name, name, caption, label)

    def list_tables(self) -> pd.DataFrame:
        resp = self._ipc.send({"action": ACTION_LIST_TABLES}) or {}
        return pd.DataFrame(resp.get("tables", []), columns=["id", "name", "data", "columns"])

    def list_assets(self) -> pd.DataFrame:
        resp = self._ipc.send({"action": ACTION_LIST_ASSETS}) or {}
        return pd.DataFrame(resp.get("assets", []),
                            columns=["id", "name", "ext", "version", "bytes"])

    def clear_shapes(self, sheet, subplot=0):
        """Remove every drawn object from one subplot of a sheet."""
        return self._ipc.send({"action": ACTION_ORGANIZE, "kind": "sheet", "target": sheet,
                               "clear_shapes": [int(subplot)]})

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

    # ------------------------------------------------------------- LaTeX documents
    def push_document(self, name, source, *, mode="replace", folder=None, template=None,
                      title=None, authors=None, abstract="", affiliations=None):
        """Create or update a LaTeX document stored in the project (shown in the project
        tree, opened with its live PDF preview in the GUI). ``mode="append"`` adds to the
        end of an existing one. Figures are sheets: ``\\noorsheet{<sheet id>}`` --
        :meth:`figure_snippet` returns a ready ``figure`` environment.

        ``template`` = ``"acs"`` | ``"rsc"`` | ``"elsevier"`` | ``"article"`` lays the
        document out for that journal: a body-only ``source`` is wrapped (``title``,
        ``authors`` and ``abstract`` fill the front matter), a complete one is switched to the
        template's preamble with its text kept. ``authors`` are names or dicts ``{"name",
        "affils": ["1", "2"], "email", "corresponding"}`` with ``affiliations={"1": "Univ A",
        ...}``; the list is written in the layout's own convention."""
        return self._ipc.send({"action": ACTION_DOCUMENT, "name": name, "source": source,
                               "mode": mode, "folder": folder or "", "template": template or "",
                               "title": title or "", "authors": list(authors or []),
                               "abstract": abstract, "affiliations": dict(affiliations or {})})

    def push_si_document(self, main, name=None, *, title=None) -> dict:
        """Create the Supporting Information document of the paper ``main`` (a document name
        or id): same class / preamble / macros / authors, new title, empty body skeleton,
        figures / tables / equations / pages numbered S1, S2, ... References and sheets are
        project-wide, so the SI can cite and show the same items."""
        from . import templates
        src = self.get_document(main)
        return self.push_document(name or f"{main} - SI", templates.si_document(src, title))

    def list_templates(self) -> pd.DataFrame:
        """Document layouts usable as ``push_document(template=<id>)``: the built-in journal
        layouts plus the user's own saved ones (``user:<name>``)."""
        from . import templates
        return pd.DataFrame(templates.list_templates(), columns=["id", "label"])

    def list_documents(self) -> pd.DataFrame:
        resp = self._ipc.send({"action": ACTION_LIST_DOCUMENTS}) or {}
        return pd.DataFrame(resp.get("documents", []), columns=["id", "name", "chars"])

    def get_document(self, key) -> str:
        """The LaTeX source of a document (by id or name)."""
        resp = self._ipc.send({"action": ACTION_GET_DOCUMENT, "key": key}) or {}
        if resp.get("status") != "success":
            raise KeyError(resp.get("message", f"no document matching {key!r}"))
        return resp["source"]

    def push_references(self, items) -> list:
        """Add bibliography records to the project (stored with it, so documents cite and
        compile offline). ``items`` are Zotero / CSL-JSON dicts; returns their cite keys."""
        from .zotero import normalize
        records = [r for r in (normalize(i) for i in items) if r]
        self._ipc.send({"action": ACTION_REFERENCES, "items": records})
        return [r["key"] for r in records]

    def zotero_pick(self) -> list:
        """Open Zotero's picker (needs Zotero + Better BibTeX running, no library export),
        store the chosen items as references, return their cite keys -- use them as
        ``\\cite{key}``. The popup appears in Zotero and waits for the user."""
        from .zotero import cayw_pick
        return self.push_references(cayw_pick())

    def zotero_search(self, query: str = "", limit: int = 50) -> pd.DataFrame:
        """Search the whole Zotero library (Better BibTeX JSON-RPC: no picker, no library
        export; Zotero must be running). Columns: key, label, year, doi. Add hits to the
        project with :meth:`zotero_add`."""
        from .zotero import label, rpc_search
        recs = rpc_search(query, limit=limit)
        self._last_zotero = {r["key"]: r for r in recs}
        return pd.DataFrame([{"key": r["key"], "label": label(r), "year": r["year"],
                              "doi": r["doi"]} for r in recs],
                            columns=["key", "label", "year", "doi"])

    def zotero_add(self, keys) -> list:
        """Store library items in the project by citation key (the key you cite with, e.g.
        ``\\cite{huangSolidStateBatteryCathode2021}``); returns the keys that were found."""
        from .zotero import rpc_find_keys
        keys = [keys] if isinstance(keys, str) else list(keys)
        found, missing = rpc_find_keys(keys)
        if found:
            self._ipc.send({"action": ACTION_REFERENCES, "items": found})
        if missing:
            print(f"[zotero] not found in the library: {missing}")
        return [r["key"] for r in found]

    def list_references(self) -> pd.DataFrame:
        resp = self._ipc.send({"action": ACTION_LIST_REFERENCES}) or {}
        return pd.DataFrame(resp.get("references", []),
                            columns=["key", "label", "zotero_key"])

    def figure_snippet(self, sheet, caption="", label="", width="") -> str:
        """A LaTeX ``figure`` environment that includes ``sheet`` (id or name) via
        ``\\noorsheet`` -- paste it into a document, or pass it to
        :meth:`push_document` with ``mode="append"``."""
        from .latex import figure_snippet
        sheets = self.list_sheets()
        hit = sheets[(sheets["id"] == sheet) | (sheets["name"] == sheet)]
        if hit.empty:
            raise KeyError(f"no sheet matching {sheet!r}; see list_sheets()")
        row = hit.iloc[0]
        return figure_snippet(row["id"], row["name"], caption, label, width)

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
