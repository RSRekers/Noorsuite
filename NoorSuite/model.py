"""Data model for SciSuite / NoorSuite (v6).

The pool holds :class:`DataObject`s -- named handles around a set of columns (a pushed
DataFrame stays *one* object). A plot trace is a :class:`TraceRef`: a pointer to
``(data object, x column, y column)`` plus its own style, living on a
:class:`SubplotModel`. The same column can be referenced from several subplots with
different styling, and each reference has an ``enabled`` toggle that hides it without
deleting it.

:class:`ProjectModel` is the single serialization root, used for both ``.sciproj``
project files and the ``~/.scisuite_session.json`` autosave.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path

import numpy as np

PROJECT_VERSION = 6


def _safe_fragment(text: str, maxlen: int = 60) -> str:
    """A filesystem-safe slug of ``text`` for use as a sidecar file name."""
    frag = re.sub(r"[^A-Za-z0-9._ -]+", "", str(text)).strip()
    frag = re.sub(r"\s+", "_", frag).strip("._-")
    return frag[:maxlen].strip("._-") or "image"


def resolve_sidecar_names(images) -> dict:
    """Map each image ``id`` to its ``.npy`` sidecar file name.

    The file is named after the image (``<name>.npy``); the short ``id`` is appended
    only to disambiguate images whose names would otherwise collide.
    """
    base = {im.id: _safe_fragment(im.name) for im in images}
    seen: dict[str, int] = {}
    for name in base.values():
        seen[name] = seen.get(name, 0) + 1
    return {
        oid: (f"{name}_{oid}.npy" if seen[name] > 1 else f"{name}.npy")
        for oid, name in base.items()
    }

INDEX_COL = "__index__"   # x_col sentinel: use the row index as x

# Shared option vocabularies (also used by the UI form widgets).
# Errorbars are NOT a plot type -- they're an overlay any of these can carry
# (TraceRef.show_errorbar), so "Scatter + errorbar" and "Line + errorbar" are both
# just this plot type with that flag on. See "Errorbar overlay" below.
PLOT_TYPES = ["Line", "Scatter", "Line+Scatter", "Step", "Bar"]
ERROR_MODES = ["std", "sem", "minmax"]
ERROR_MODE_LABELS = ["Std. deviation", "Std. error of the mean", "Min / max span"]
ERR_COLUMN_ATTRS = ("yerr_col", "yerr_sem_col", "yerr_low_col", "yerr_high_col")
LINE_STYLES = ["-", "--", "-.", ":"]
LINE_STYLE_LABELS = ["Solid (-)", "Dashed (--)", "Dash-Dot (-.)", "Dotted (:)"]
MARKERS = ["None", ".", "o", "s", "D", "d", "^", "v", "<", ">",
           "p", "h", "8", "*", "x", "+", "1", "2", "3", "4"]
MARKER_LABELS = ["None", "Point (.)", "Circle (o)", "Square (s)", "Diamond (D)",
                 "Thin diamond (d)", "Triangle up (^)", "Triangle down (v)",
                 "Triangle left (<)", "Triangle right (>)", "Pentagon (p)",
                 "Hexagon (h)", "Octagon (8)", "Star (*)", "Cross (x)", "Plus (+)",
                 "Tri down (1)", "Tri up (2)", "Tri left (3)", "Tri right (4)"]
Y_TRANSFORMS = ["1x", "1e3", "1e-3", "1e-6", "1e-9", "Log10", "Norm", "custom",
                "rel_value", "rel_index"]
Y_TRANSFORM_LABELS = ["1x", "1e3 (kilo)", "1e-3 (milli)", "1e-6 (micro)",
                      "1e-9 (nano)", "Log10", "Norm (0-1)", "Custom factor...",
                      "Relative to value...", "Relative to nth point..."]
REL_MODES = ["frac", "pct"]
REL_MODE_LABELS = ["Fraction (0-1)", "Percent (%)"]
TICK_FORMATS = ["auto", "plain", "scientific", "fixed"]
TICK_FORMAT_LABELS = ["Auto", "Plain (no sci. notation)", "Scientific (1.2e4)",
                      "Fixed decimals"]
LEGEND_LOCS = ["best", "upper right", "upper left", "lower left", "lower right",
               "right", "center left", "center right", "lower center",
               "upper center", "center"]


def _new_id() -> str:
    return str(uuid.uuid4())[:8]


CUSTOM_FACTOR_PREFIX = "custom:"   # scale_factor = f"{CUSTOM_FACTOR_PREFIX}{multiplier}"
# Relative-change tokens: f"{PREFIX}{mode}:{ref}" -- mode is "frac" ((y-ref)/ref) or
# "pct" (same, x100). REL_VALUE's ref is a fixed number, the same for every series.
# REL_INDEX's ref is resolved from y itself (y[index]), so it's naturally *per-series*
# when applied to each of several repeat traces individually before aggregating them --
# each trace is normalized to its own value at that row, not a shared one.
REL_VALUE_PREFIX = "relval:"
REL_INDEX_PREFIX = "relidx:"


def _relative_change(y: np.ndarray, ref, mode: str) -> np.ndarray:
    denom = np.where(ref == 0, 1.0, ref)   # degrade gracefully instead of dividing by 0
    rel = (y - ref) / denom
    return rel * 100.0 if mode == "pct" else rel


def apply_y_transform(y, factor: str):
    """Return ``y`` transformed according to a scale-factor token.

    ``factor`` is one of :data:`Y_TRANSFORMS` (or a longer label that merely
    *contains* one, e.g. ``"1e3 (kilo)"``), ``f"{CUSTOM_FACTOR_PREFIX}<number>"`` for an
    arbitrary constant multiplier, ``f"{REL_VALUE_PREFIX}<frac|pct>:<number>"`` for the
    relative change from a fixed reference value, or ``f"{REL_INDEX_PREFIX}<frac|pct>:<row
    index>"`` for the relative change from ``y``'s own value at that row (a per-series
    baseline when applied to each of several repeat traces before aggregating them).
    Unknown/unparseable tokens return ``y`` unchanged.
    """
    y = np.asarray(y, dtype=float)
    if factor.startswith(CUSTOM_FACTOR_PREFIX):
        try:
            return y * float(factor[len(CUSTOM_FACTOR_PREFIX):])
        except ValueError:
            return y
    if factor.startswith(REL_VALUE_PREFIX):
        mode, _, rest = factor[len(REL_VALUE_PREFIX):].partition(":")
        try:
            return _relative_change(y, float(rest), mode)
        except ValueError:
            return y
    if factor.startswith(REL_INDEX_PREFIX):
        mode, _, rest = factor[len(REL_INDEX_PREFIX):].partition(":")
        try:
            idx = int(float(rest))
        except ValueError:
            return y
        if not len(y):
            return y
        ref = y[int(np.clip(idx, 0, len(y) - 1))]
        return _relative_change(y, ref, mode)
    if "1e-3" in factor:
        return y * 1e-3
    if "1e-6" in factor:
        return y * 1e-6
    if "1e-9" in factor:
        return y * 1e-9
    if "1e3" in factor:
        return y * 1e3
    if "Log10" in factor:
        return np.log10(np.clip(y, 1e-12, None))
    if "Norm" in factor:
        ptp = float(np.ptp(y))
        return (y - np.min(y)) / (ptp if ptp != 0 else 1.0)
    return y


def apply_numeric_expr(current: float, expr: str):
    """Resolve a bulk-edit numeric field against one value.

    ``""`` -> ``None`` (leave unchanged). A bare number -> that value. ``*F`` / ``/F`` /
    ``+D`` / ``-D`` -> transform ``current``. Anything unparseable -> ``None``.
    ``/0`` returns ``current`` unchanged.
    """
    expr = (expr or "").strip()
    if not expr:
        return None
    try:
        if expr[0] in "*/+-" and len(expr) > 1:
            op, rest = expr[0], expr[1:].strip()
            val = float(rest)
            if op == "*":
                return current * val
            if op == "/":
                return current / val if val else current
            if op == "+":
                return current + val
            return current - val
        return float(expr)
    except ValueError:
        return None


def _yerr_scale(factor: str, y=None) -> float:
    """Linear multiplier implied by a ``TraceRef.scale_factor`` token, used to keep an
    error-bar column consistent with :meth:`TraceRef.resolve`'s y-transform. ``Log10``
    and ``Norm`` don't have a meaningful error-bar scaling, so they're left at 1.0.
    ``REL_VALUE_PREFIX`` (a fixed reference for every point) is linear -- (y-ref)/ref --
    so its 1/ref (or 100/ref for percent) multiplier applies cleanly to an error column,
    which only needs its magnitude scaled, not shifted. ``REL_INDEX_PREFIX``'s reference
    comes from the data itself (``y[row index]``) rather than the token, so it also
    needs the trace's own *raw* y column (the same array :func:`apply_y_transform` uses
    to resolve that same reference for the y-transform, so they stay consistent) --
    when ``y`` isn't given, it degrades to 1.0 (unscaled)."""
    if factor.startswith(CUSTOM_FACTOR_PREFIX):
        try:
            return float(factor[len(CUSTOM_FACTOR_PREFIX):])
        except ValueError:
            return 1.0
    if factor.startswith(REL_VALUE_PREFIX):
        mode, _, rest = factor[len(REL_VALUE_PREFIX):].partition(":")
        try:
            ref = float(rest)
        except ValueError:
            return 1.0
        if ref == 0:
            return 1.0
        return (100.0 if mode == "pct" else 1.0) / ref
    if factor.startswith(REL_INDEX_PREFIX):
        if y is None or len(y) == 0:
            return 1.0
        mode, _, rest = factor[len(REL_INDEX_PREFIX):].partition(":")
        try:
            idx = int(float(rest))
        except ValueError:
            return 1.0
        y = np.asarray(y, dtype=float)
        ref = float(y[int(np.clip(idx, 0, len(y) - 1))])
        if ref == 0:
            return 1.0
        return (100.0 if mode == "pct" else 1.0) / ref
    if "1e-3" in factor:
        return 1e-3
    if "1e-6" in factor:
        return 1e-6
    if "1e-9" in factor:
        return 1e-9
    if "1e3" in factor:
        return 1e3
    return 1.0


def common_label(names: list) -> str:
    """A short label for a group of related names, e.g. ``["a_1","a_2","a_3"]`` ->
    ``"a"``. Falls back to a common suffix, then the first name, if they don't share a
    usable prefix."""
    prefix = os.path.commonprefix(names).rstrip("_- ")
    if prefix:
        return prefix
    suffix = os.path.commonprefix([n[::-1] for n in names])[::-1].lstrip("_- ")
    return suffix or names[0]


def aggregate_series(arrays: list) -> dict:
    """Row-wise mean / std / min-max summary across several equal-length y arrays --
    e.g. one per repeat trace of the same measurement, each already resolved (so each
    one's own :attr:`TraceRef.scale_factor` -- a per-series baseline normalization via
    ``REL_INDEX_PREFIX``, say -- is already baked in if the caller wants that).

    Returns ``{"mean", "std", "sem", "err_min", "err_max"}`` (``sem`` = std / sqrt(n)),
    arrays the same length as the input. ``err_min``/``err_max`` are already offsets from the mean (``mean - min`` /
    ``max - mean``), ready to feed straight into an asymmetric errorbar. Raises
    :class:`ValueError` for fewer than 2 arrays, or if their lengths differ.
    """
    if len(arrays) < 2:
        raise ValueError("need at least 2 series to aggregate")
    arrays = [np.asarray(a, dtype=float) for a in arrays]
    lengths = {len(a) for a in arrays}
    if len(lengths) != 1:
        raise ValueError(f"series have different lengths: {sorted(lengths)}")
    stack = np.column_stack(arrays)
    mean = stack.mean(axis=1)
    std = stack.std(axis=1, ddof=1)
    return {
        "mean": mean,
        "std": std,
        "sem": std / np.sqrt(stack.shape[1]),
        "err_min": mean - stack.min(axis=1),
        "err_max": stack.max(axis=1) - mean,
    }


SPLIT_LAYOUTS = ["block", "sequence"]
SPLIT_LAYOUT_LABELS = ["Blocks  (a a a  b b b  c c c ...)", "Sequence  (a b c ... a b c ...)"]


def split_into_repeats(values, n: int, layout: str = "block") -> dict:
    """Split *one* already-concatenated series into repeats and summarize them -- the
    same statistics as :func:`aggregate_series`, but for a single trace whose data is
    literally several repeat measurements stacked together, rather than several
    separate traces. Both layouts reshape ``values`` to ``(len(values)/n, n)``; they
    only differ in which axis is the "repeat" to aggregate over:

    - ``"block"`` (default): ``n`` repeats *per condition*, one block after another --
      ``[a1..an, b1..bn, ..., z1..zn]`` -- aggregates *within* each row (each block),
      giving ``len(values)/n`` points (one per block/condition).
    - ``"sequence"``: one full cycle of ``n`` conditions, the whole cycle repeated --
      ``[a,b,...,z, a,b,...,z, ...]`` -- aggregates *down each column* (across
      repeats of the same position in the cycle), giving ``n`` points (one per
      position), each summarizing ``len(values)/n`` repeats of it.

    Returns ``{"mean", "std", "sem", "err_min", "err_max"}``. Raises :class:`ValueError` if
    ``n < 1``, ``len(values)`` isn't an exact multiple of ``n``, or ``layout`` isn't
    one of :data:`SPLIT_LAYOUTS`.
    """
    values = np.asarray(values, dtype=float)
    n = int(n)
    if n < 1:
        raise ValueError("repeat count must be at least 1")
    if len(values) == 0 or len(values) % n != 0:
        raise ValueError(f"{len(values)} value(s) is not a multiple of the repeat count {n}")
    if layout not in SPLIT_LAYOUTS:
        raise ValueError(f"layout must be one of {SPLIT_LAYOUTS}, got {layout!r}")
    stack = values.reshape(-1, n)
    axis = 0 if layout == "sequence" else 1
    mean = stack.mean(axis=axis)
    std = stack.std(axis=axis, ddof=1) if stack.shape[axis] > 1 else np.zeros_like(mean)
    return {
        "mean": mean,
        "std": std,
        "sem": std / np.sqrt(stack.shape[axis]),
        "err_min": mean - stack.min(axis=axis),
        "err_max": stack.max(axis=axis) - mean,
    }


class ColorMap:
    """A named list of hex colours, or a reference to a matplotlib built-in.

    Sampling to a concrete colour list lives in :mod:`NoorSuite.colormap` (which needs
    matplotlib); this class is just the serializable data.
    """

    def __init__(self, name, colors=None, builtin=False):
        self.name = name
        self.colors = list(colors or [])
        self.builtin = builtin

    def to_dict(self) -> dict:
        return {"name": self.name, "colors": list(self.colors), "builtin": self.builtin}

    @classmethod
    def from_dict(cls, d: dict) -> "ColorMap":
        return cls(d["name"], d.get("colors", []), d.get("builtin", False))


SIDECAR_MIN_VALUES = 4096     # data objects with more numbers than this are saved as .npz files


class DataObject:
    """A named set of columns in the data pool (typically one pushed DataFrame)."""

    def __init__(self, name, columns=None, column_order=None, units=None,
                 tags=None, source="", obj_id=None, notes=""):
        self.id = obj_id or _new_id()
        self.name = name
        self.notes = notes          # free-text description (what/how/when this data is)
        self.columns: dict[str, np.ndarray] = {}
        self.column_order: list[str] = []
        if columns:
            for col in (column_order or list(columns.keys())):
                self.columns[col] = np.asarray(columns[col], dtype=float)
                self.column_order.append(col)
        self.units: dict[str, str] = dict(units or {})
        self.tags = list(tags or [])
        self.source = source
        self._rev = 0               # bumped whenever the columns change (save / fingerprint caches)

    @property
    def nrows(self) -> int:
        for col in self.column_order:
            return len(self.columns[col])
        return 0

    @property
    def ncols(self) -> int:
        return len(self.column_order)

    def get(self, col: str) -> np.ndarray:
        """Column values; ``""`` or :data:`INDEX_COL` yields the row index."""
        if col in ("", INDEX_COL):
            return np.arange(self.nrows, dtype=float)
        return self.columns[col]

    def head(self, n: int = 8):
        """Return ``(column_order, rows)`` with the first ``n`` rows for preview."""
        k = min(n, self.nrows)
        rows = [[self.columns[c][i] for c in self.column_order] for i in range(k)]
        return list(self.column_order), rows

    def update_from(self, columns, units=None, column_order=None) -> None:
        """Replace / extend columns in place, keeping ``id`` and ``name``."""
        for col in (column_order or list(columns.keys())):
            if col not in self.columns:
                self.column_order.append(col)
            self.columns[col] = np.asarray(columns[col], dtype=float)
        self._rev += 1
        if units:
            self.units.update(units)

    def n_values(self) -> int:
        return sum(len(self.columns[c]) for c in self.column_order)

    def signature(self) -> tuple:
        """Cheap identity of the data: changes when a column is replaced or added."""
        return (self._rev, tuple((c, id(self.columns[c]), len(self.columns[c]))
                                 for c in self.column_order))

    def to_dict(self, sidecar=False) -> dict:
        """``sidecar=True``: a big object leaves its numbers out (they go to a ``.npz`` next to
        the project file, see :meth:`ProjectModel.prepare_save`)."""
        d = {
            "id": self.id,
            "name": self.name,
            "column_order": list(self.column_order),
            "units": dict(self.units),
            "tags": list(self.tags),
            "source": self.source,
            "notes": self.notes,
        }
        if sidecar and self.n_values() > SIDECAR_MIN_VALUES:
            d["columns"] = {}
        else:
            d["columns"] = {c: self.columns[c].tolist() for c in self.column_order}
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "DataObject":
        return cls(d["name"], d.get("columns", {}), d.get("column_order"),
                   d.get("units"), d.get("tags"), d.get("source", ""), d.get("id"),
                   d.get("notes", ""))


class TraceRef:
    """A pointer from a subplot to one ``(data object, x col, y col)`` + its style."""

    _STYLE_FIELDS = ("plot_type", "color", "edge_color", "line_style", "line_width",
                     "marker", "marker_size", "alpha", "scale_factor",
                     "show_errorbar", "yerr_mode", "sort_x", "show_band", "band_alpha")

    def __init__(self, data_id, x_col, y_col, label="", enabled=True):
        self.data_id = data_id
        self.x_col = x_col          # "" or INDEX_COL -> row index
        self.y_col = y_col
        self.label = label          # defaults to y_col
        self.enabled = enabled

        self.plot_type = "Line"
        self.color = "#1f77b4"
        self.edge_color = "#000000"
        self.line_style = "-"
        self.line_width = 1.8
        self.marker = "None"
        self.marker_size = 6.0
        self.alpha = 1.0
        self.scale_factor = "1x"

        # Errorbar overlay: independent of plot_type, so any of them (Line, Scatter,
        # ...) can carry error whiskers -- "Scatter + errorbar" is just plot_type
        # "Scatter" with show_errorbar True. yerr_col / yerr_low_col / yerr_high_col
        # point at the column(s) holding the error (usually on a derived DataObject
        # built by combining several repeat traces -- see app._combine_selected_traces);
        # yerr_mode picks which of them resolve_yerr() actually returns.
        self.show_errorbar = False
        # Shaded band (mean +- error, drawn with fill_between under the line): independent of
        # the error bars and of the plot type, fed by the same error columns / yerr_mode.
        self.show_band = False
        self.band_alpha = 0.25
        self.yerr_sem_col = ""      # std. error of the mean, used when yerr_mode == "sem"
        self.yerr_col = ""          # symmetric error, used when yerr_mode == "std"
        self.yerr_low_col = ""      # offset below the mean (mean - min)
        self.yerr_high_col = ""     # offset above the mean (max - mean)
        self.yerr_mode = "std"      # "std" | "minmax" -- see model.ERROR_MODES

        # Draw the points ordered by x (stable; NaN last) instead of in data order, so a
        # trace whose x is disordered doesn't zig-zag. Display only -- the data object is
        # untouched. Leave off for loops/hysteresis/parametric curves, where order IS the shape.
        self.sort_x = False

    @property
    def has_error_data(self) -> bool:
        """Whether this trace has error column(s) to show at all (regardless of
        whether :attr:`show_errorbar` is currently on) -- gates the "Show error bars"
        checkbox in the UI."""
        return bool(self.yerr_col or self.yerr_sem_col
                    or (self.yerr_low_col and self.yerr_high_col))

    @property
    def display_label(self) -> str:
        return self.label or self.y_col

    def style_dict(self) -> dict:
        return {f: getattr(self, f) for f in self._STYLE_FIELDS}

    def apply_style(self, d: dict) -> None:
        for f in self._STYLE_FIELDS + ERR_COLUMN_ATTRS + ("label",):   # these ride along (API)
            if f in d and d[f] is not None:
                setattr(self, f, d[f])

    def resolve(self, data_object: DataObject):
        """Return ``(x_array, y_array)`` with the y-transform applied."""
        x = np.asarray(data_object.get(self.x_col), dtype=float)
        y = np.asarray(apply_y_transform(data_object.get(self.y_col), self.scale_factor),
                       dtype=float)
        if self.sort_x:
            order = np.argsort(x, kind="stable")
            x, y = x[order], y[order]
        return x, y

    def _yerr_sorted(self, data_object: DataObject, err):
        """Reorder an error array the same way :meth:`resolve` orders x/y."""
        if err is None or not self.sort_x:
            return err
        order = np.argsort(np.asarray(data_object.get(self.x_col), dtype=float),
                           kind="stable")
        return err[..., order]

    def resolve_yerr(self, data_object: DataObject):
        """Return the y-error data (independent of :attr:`show_errorbar` -- that flag
        only gates whether *rendering* draws it), or ``None`` if there's none set.

        ``yerr_mode == "minmax"`` (with both columns set) returns a ``(2, N)`` array of
        (lower, upper) offsets from :attr:`yerr_low_col` / :attr:`yerr_high_col`;
        otherwise a 1-D symmetric array from :attr:`yerr_col`.
        """
        try:
            y_raw = data_object.get(self.y_col)
        except KeyError:
            y_raw = None
        scale = _yerr_scale(self.scale_factor, y_raw)
        if self.yerr_mode == "minmax" and self.yerr_low_col and self.yerr_high_col:
            lo = np.asarray(data_object.get(self.yerr_low_col), dtype=float) * scale
            hi = np.asarray(data_object.get(self.yerr_high_col), dtype=float) * scale
            return self._yerr_sorted(data_object, np.vstack([lo, hi]))
        if self.yerr_mode == "sem" and self.yerr_sem_col:
            return self._yerr_sorted(
                data_object,
                np.asarray(data_object.get(self.yerr_sem_col), dtype=float) * scale)
        if self.yerr_col:
            return self._yerr_sorted(
                data_object,
                np.asarray(data_object.get(self.yerr_col), dtype=float) * scale)
        return None

    def to_dict(self) -> dict:
        d = {"data_id": self.data_id, "x_col": self.x_col, "y_col": self.y_col,
             "label": self.label, "enabled": self.enabled,
             "yerr_col": self.yerr_col, "yerr_low_col": self.yerr_low_col,
             "yerr_high_col": self.yerr_high_col, "yerr_sem_col": self.yerr_sem_col}
        d.update(self.style_dict())
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "TraceRef":
        t = cls(d["data_id"], d.get("x_col", ""), d["y_col"],
                d.get("label", ""), d.get("enabled", True))
        t.yerr_col = d.get("yerr_col", "")
        t.yerr_low_col = d.get("yerr_low_col", "")
        t.yerr_high_col = d.get("yerr_high_col", "")
        t.yerr_sem_col = d.get("yerr_sem_col", "")
        t.apply_style(d)
        return t


class ImageObject:
    """A named ND array in the data pool, shown as an image (a 2-D slice at a time)."""

    def __init__(self, name, data, axis_names=None, axis_units=None, tags=None,
                 source="", obj_id=None, axis_coords=None):
        self.id = obj_id or _new_id()
        self.name = name
        self.data = np.asarray(data)
        # Real coordinates per axis (axis index -> list of numbers, or of strings for a
        # categorical axis), e.g. the x / y values of a heatmap. Axes without an entry
        # fall back to the plain pixel index.
        self.axis_coords: dict[int, list] = {}
        for a, vals in (axis_coords or {}).items():
            self.set_axis_coords(int(a), vals)
        self.axis_names = list(axis_names) if axis_names else \
            [f"axis_{i}" for i in range(self.data.ndim)]
        self.axis_units = dict(axis_units or {})
        self.tags = list(tags or [])
        self.source = source
        self._dirty = True            # array not yet written to a sidecar file

    @property
    def ndim(self) -> int:
        return self.data.ndim

    @property
    def shape(self):
        return tuple(self.data.shape)

    @property
    def is_heatmap(self) -> bool:
        """Has real coordinates on at least one axis (otherwise it is a plain image)."""
        return bool(self.axis_coords)

    def set_axis_coords(self, axis: int, values) -> None:
        vals = list(values)
        if len(vals) != self.data.shape[axis]:
            raise ValueError(f"axis {axis} has {self.data.shape[axis]} entries but "
                             f"{len(vals)} coordinate values were given")
        try:
            vals = [float(v) for v in vals]
        except (TypeError, ValueError):
            vals = [str(v) for v in vals]          # categorical labels
        self.axis_coords[axis] = vals

    def axis_positions(self, axis: int):
        """``(positions, labels)`` for ``axis``: float positions of each cell centre and,
        for a categorical axis, the string labels (positions are then ``0..n-1``);
        ``None`` if the axis has no coordinates."""
        vals = self.axis_coords.get(axis)
        if vals is None or len(vals) != self.data.shape[axis]:
            return None
        if vals and isinstance(vals[0], str):
            return np.arange(len(vals), dtype=float), list(vals)
        return np.asarray(vals, dtype=float), None

    def head_meta(self) -> str:
        axes = ", ".join(f"{n}={s}" for n, s in zip(self.axis_names, self.shape))
        return f"{self.data.dtype}  [{axes}]"

    def sidecar_name(self) -> str:
        """Default ``.npy`` sidecar file name for this image (``<name>.npy``).

        The serializer uses :func:`resolve_sidecar_names` instead, which additionally
        disambiguates images whose names would collide.
        """
        return f"{_safe_fragment(self.name)}.npy"

    def slice(self, display_axes, index) -> np.ndarray:
        """2-D slice: index every non-display axis, order as (rows, cols)."""
        r, c = int(display_axes[0]), int(display_axes[1])
        sel = [slice(None)] * self.data.ndim
        for ax in range(self.data.ndim):
            if ax not in (r, c):
                n = self.data.shape[ax]
                sel[ax] = int(np.clip(index.get(ax, n // 2), 0, n - 1))
        sub = self.data[tuple(sel)]                       # now 2-D, axes (min(r,c), max(r,c))
        return sub if r < c else np.swapaxes(sub, 0, 1)

    def update_from(self, data, axis_names=None, axis_coords=None) -> None:
        self.data = np.asarray(data)
        if axis_names:
            self.axis_names = list(axis_names)
        elif len(self.axis_names) != self.data.ndim:
            self.axis_names = [f"axis_{i}" for i in range(self.data.ndim)]
        # stale coordinates (axis length changed) are dropped; new ones replace old
        self.axis_coords = {a: v for a, v in self.axis_coords.items()
                            if a < self.data.ndim and len(v) == self.data.shape[a]}
        for a, vals in (axis_coords or {}).items():
            self.set_axis_coords(int(a), vals)
        self._dirty = True

    def to_dict(self) -> dict:
        # Metadata only; the array itself is written to a sidecar file by ProjectModel.save.
        return {
            "id": self.id,
            "name": self.name,
            "shape": list(self.shape),
            "dtype": str(self.data.dtype),
            "axis_names": list(self.axis_names),
            "axis_units": dict(self.axis_units),
            "tags": list(self.tags),
            "source": self.source,
            "axis_coords": {str(a): list(v) for a, v in self.axis_coords.items()},
            "array_file": "",
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ImageObject":
        shape = tuple(d.get("shape", ()))
        stub = np.zeros(shape, dtype=d.get("dtype", "float64"))
        obj = cls(d["name"], stub, d.get("axis_names"), d.get("axis_units"),
                  d.get("tags"), d.get("source", ""), d.get("id"),
                  {int(a): v for a, v in (d.get("axis_coords") or {}).items()})
        obj._dirty = False
        return obj


class ImageRef:
    """A pointer from a subplot to an :class:`ImageObject` + its display state."""

    _FIELDS = ("data_id", "display_axes", "index", "slice_axis", "cmap", "vmin", "vmax",
               "interpolation", "origin", "aspect", "alpha", "colorbar",
               # heatmap options: colormap shaping, isolines, axis ticks
               "use_coords", "cmap_reverse", "cmap_bins", "cmap_boundaries",
               "iso_show", "iso_levels", "iso_count", "iso_above", "iso_below",
               "iso_color", "iso_width", "iso_style", "iso_labels", "iso_label_fmt",
               "x_tick_mode", "x_tick_values", "x_tick_every",
               "y_tick_mode", "y_tick_values", "y_tick_every")
    # fields a caller may set when placing an image/heatmap (everything but the pointers)
    STYLE_FIELDS = tuple(f for f in _FIELDS
                         if f not in ("data_id", "display_axes", "index", "slice_axis"))

    def __init__(self, data_id, display_axes=(0, 1), index=None):
        self.data_id = data_id
        self.display_axes = list(display_axes)
        self.index: dict[int, int] = {int(k): int(v) for k, v in (index or {}).items()}
        self.slice_axis = None        # axis the navigation slider drives (None -> auto)
        self.cmap = "viridis"
        self.vmin = None
        self.vmax = None
        self.interpolation = "nearest"
        self.origin = "upper"          # upper | lower
        self.aspect = "equal"         # auto | equal
        self.alpha = 1.0
        self.colorbar = False

        # --- heatmap options -------------------------------------------------------
        self.use_coords = True         # draw at the object's axis coordinates, if it has any
        self.cmap_reverse = False
        self.cmap_bins = 0             # 0 -> continuous; N >= 2 -> N discrete colour bands
        self.cmap_boundaries = ""      # explicit band edges "0, 10, 50, 100" (overrides bins)
        self.iso_show = False          # contour lines over the image
        self.iso_levels = ""           # explicit levels "10, 20, 50"; blank -> auto / band edges
        self.iso_count = 8             # about this many automatic levels
        self.iso_above = None          # only draw isolines at levels >= this value
        self.iso_below = None          # ... and <= this value
        self.iso_color = "#000000"
        self.iso_width = 0.8
        self.iso_style = "-"
        self.iso_labels = False
        self.iso_label_fmt = "%g"
        # ticks: "auto" (matplotlib) | "data" (one per cell centre, every Nth) | "custom"
        self.x_tick_mode = "auto"
        self.x_tick_values = ""        # custom: "0, 5, 10"
        self.x_tick_every = 1
        self.y_tick_mode = "auto"
        self.y_tick_values = ""
        self.y_tick_every = 1

    def apply_style(self, d: dict) -> None:
        """Set any of :attr:`STYLE_FIELDS` from ``d`` (``None`` values are skipped)."""
        for f in self.STYLE_FIELDS:
            if f in d and d[f] is not None:
                setattr(self, f, d[f])

    def to_dict(self) -> dict:
        d = {f: getattr(self, f) for f in self._FIELDS}
        d["display_axes"] = list(self.display_axes)
        d["index"] = {str(k): v for k, v in self.index.items()}
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ImageRef":
        ref = cls(d["data_id"], d.get("display_axes", (0, 1)),
                  {int(k): int(v) for k, v in d.get("index", {}).items()})
        for f in cls._FIELDS:
            if f in d and f not in ("data_id", "display_axes", "index") and d[f] is not None:
                setattr(ref, f, d[f])
        return ref


# ---------------------------------------------------------------------------
# Shapes: lines, arrows, rectangles, ellipses, text and highlight bands drawn on a subplot.
# They share ONE z-ordered stack with the traces (SubplotModel.stack()).
# ---------------------------------------------------------------------------
SHAPE_KINDS = ["rect", "ellipse", "line", "arrow", "xspan", "yspan", "text", "picture"]
SHAPE_KIND_LABELS = ["Rectangle", "Ellipse", "Line", "Arrow", "Vertical band (x range)",
                     "Horizontal band (y range)", "Text", "Picture / SVG"]
SHAPE_COORDS = ["data", "axes"]
SHAPE_COORD_LABELS = ["Data coordinates", "Axes fraction (0-1)"]
FILL_MODES = ["solid", "linear", "radial"]
FILL_MODE_LABELS = ["Solid", "Linear gradient", "Radial gradient"]
# kinds that have an interior
FILLABLE_KINDS = ("rect", "ellipse", "xspan", "yspan")


def _hex_rgb(color) -> tuple:
    """``"#rrggbb"`` / ``"#rgb"`` -> floats in 0..1 (the GUI only ever stores hex)."""
    c = str(color or "#000000").strip().lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    try:
        return tuple(int(c[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except (ValueError, IndexError):
        return (0.0, 0.0, 0.0)


def normalize_stops(stops) -> list:
    """Clean a gradient stop list to ``[[pos, "#hex", alpha], ...]`` sorted by position.
    Accepts ``["#a", "#b"]`` (evenly spaced), ``[(pos, color)]`` or ``[(pos, color, alpha)]``."""
    out = []
    items = list(stops or [])
    for i, s in enumerate(items):
        if isinstance(s, str):
            out.append([i / max(1, len(items) - 1), s, 1.0])
            continue
        try:
            pos = float(s[0])
            col = str(s[1])
            alpha = float(s[2]) if len(s) > 2 else 1.0
        except (TypeError, ValueError, IndexError):
            continue
        out.append([min(1.0, max(0.0, pos)), col, min(1.0, max(0.0, alpha))])
    out.sort(key=lambda s: s[0])
    return out


def gradient_rgba(stops, mode="linear", angle=0.0, cx=0.5, cy=0.5, radius=0.5,
                  n=192, reverse=False) -> np.ndarray:
    """An ``(n, n, 4)`` float RGBA image (row 0 = bottom) of a linear or radial gradient
    through ``stops`` (see :func:`normalize_stops`; colours *and* alpha are interpolated).

    ``angle`` (degrees, 0 = left -> right, 90 = bottom -> top) drives the linear one;
    ``cx, cy`` (0..1 within the shape's bounding box) and ``radius`` (fraction of the box
    width, ``0.5`` = reaches the box edge) the radial one."""
    st = normalize_stops(stops) or [[0.0, "#000000", 1.0], [1.0, "#ffffff", 1.0]]
    if len(st) == 1:
        st = [st[0], [1.0, st[0][1], st[0][2]]]
    pos = np.array([s[0] for s in st])
    rgb = np.array([_hex_rgb(s[1]) for s in st])
    alpha = np.array([s[2] for s in st])
    u = (np.arange(n) + 0.5) / n
    uu, vv = np.meshgrid(u, u)                      # vv grows with the row = upwards
    if mode == "radial":
        t = np.hypot(uu - float(cx), vv - float(cy)) / max(1e-6, float(radius))
    else:
        a = np.deg2rad(float(angle))
        ca, sa = np.cos(a), np.sin(a)
        span = (abs(ca) + abs(sa)) or 1.0
        t = ((uu - 0.5) * ca + (vv - 0.5) * sa) / span + 0.5
    t = np.clip(t, 0.0, 1.0)
    if reverse:
        t = 1.0 - t
    img = np.empty((n, n, 4))
    for k in range(3):
        img[..., k] = np.interp(t, pos, rgb[:, k])
    img[..., 3] = np.interp(t, pos, alpha)
    return img


class ShapeRef:
    """A drawn object on one subplot. Geometry is ``x0,y0`` / ``x1,y1`` in ``coords``
    (``"data"`` follows the axes, ``"axes"`` is a 0..1 fraction of the panel):
    rect / ellipse use them as two opposite corners of the bounding box, line / arrow as
    start and end, ``xspan`` only x0/x1, ``yspan`` only y0/y1, ``text`` the anchor x0,y0.
    ``layer_pos`` is where it sits in the subplot's stack (see ``SubplotModel.stack``)."""

    _FIELDS = ("kind", "name", "shape_id", "parent_id", "scale_x", "scale_y", "enabled",
               "coords", "x0", "y0", "x1", "y1", "angle",
               "edge_color", "line_width", "line_style", "alpha", "fill", "fill_color",
               "fill_alpha", "fill_mode", "grad_stops", "grad_angle", "grad_cx", "grad_cy",
               "grad_radius", "grad_reverse", "text", "fontsize", "arrow_head", "asset_id",
               "keep_aspect", "layer_pos")
    # everything settable through a style / geometry payload (all but the kind)
    STYLE_FIELDS = tuple(f for f in _FIELDS if f not in ("kind", "layer_pos", "shape_id"))

    def __init__(self, kind="rect", name=""):
        self.kind = kind if kind in SHAPE_KINDS else "rect"
        self.name = name
        self.shape_id = _new_id()         # stable handle for parent_id
        self.parent_id = ""               # "" -> a root; else the shape_id it moves along with
        self.scale_x = 1.0                # scale factors applied around the object's centre
        self.scale_y = 1.0
        self.enabled = True
        self.coords = "data"
        self.x0, self.y0, self.x1, self.y1 = 0.0, 0.0, 1.0, 1.0
        self.angle = 0.0                  # rotation (deg) of a rect / ellipse about its centre
        self.edge_color = "#d62728"
        self.line_width = 1.5
        self.line_style = "-"
        self.alpha = 1.0                  # outline / text opacity
        self.fill = True
        self.fill_color = "#d62728"
        self.fill_alpha = 0.2
        self.fill_mode = "solid"          # solid | linear | radial
        self.grad_stops = [[0.0, "#ffffff", 1.0], [1.0, "#d62728", 1.0]]
        self.grad_angle = 0.0
        self.grad_cx, self.grad_cy, self.grad_radius = 0.5, 0.5, 0.5
        self.grad_reverse = False
        self.text = "Text"
        self.fontsize = 10.0
        self.arrow_head = "->"            # matplotlib arrowstyle
        self.asset_id = ""                # kind "picture": the project asset to show
        self.keep_aspect = True           # picture: fit inside the box without distortion
        self.layer_pos = 10 ** 6          # "above everything" until the stack is rebuilt

    @property
    def display_label(self) -> str:
        if self.name:
            return self.name
        base = dict(zip(SHAPE_KINDS, SHAPE_KIND_LABELS)).get(self.kind, self.kind)
        if self.kind == "picture" and self.asset_id:
            return f"Picture {self.asset_id}"
        return f"{base}: {self.text[:24]}" if self.kind == "text" else base

    @property
    def is_filled(self) -> bool:
        return self.kind in FILLABLE_KINDS and bool(self.fill)

    def bounds(self) -> tuple:
        """``(left, bottom, right, top)`` of the *effective* box: corners put in order and
        the scale factors applied around the centre."""
        l, b = min(self.x0, self.x1), min(self.y0, self.y1)
        r, t = max(self.x0, self.x1), max(self.y0, self.y1)
        sx, sy = float(self.scale_x or 1.0), float(self.scale_y or 1.0)
        cx, cy, hw, hh = (l + r) / 2.0, (b + t) / 2.0, (r - l) / 2.0 * sx, (t - b) / 2.0 * sy
        return (cx - hw, cy - hh, cx + hw, cy + hh)

    def geom(self) -> tuple:
        """``(cx, cy, w, h, angle)`` of the effective box (what group moves track)."""
        l, b, r, t = self.bounds()
        return ((l + r) / 2.0, (b + t) / 2.0, r - l, t - b, float(self.angle))

    def apply_style(self, style: dict | None) -> None:
        for k, v in (style or {}).items():
            if k in self.STYLE_FIELDS and v is not None:
                if k == "grad_stops":
                    v = normalize_stops(v)
                setattr(self, k, v)

    def to_dict(self) -> dict:
        return {f: ([list(s) for s in self.grad_stops] if f == "grad_stops"
                    else getattr(self, f)) for f in self._FIELDS}

    @classmethod
    def from_dict(cls, d: dict) -> "ShapeRef":
        s = cls(d.get("kind", "rect"))
        for f in cls._FIELDS:
            if f in d and d[f] is not None and f != "kind":
                setattr(s, f, d[f])
        s.grad_stops = normalize_stops(s.grad_stops) or normalize_stops(["#ffffff", "#000000"])
        return s


def apply_group_transform(child: ShapeRef, old: tuple, new: tuple, fwd=None, inv=None) -> None:
    """Carry ``child`` along when its root changed from ``old`` to ``new`` (both
    :meth:`ShapeRef.geom` tuples): translation, rotation and the size ratio are applied
    about the root's centre. ``fwd`` / ``inv`` map the shapes' coordinates to a uniform
    (display) space and back, so a rotation stays a true rotation on axes with different
    scales; without them the shapes' own coordinates are used."""
    import math
    f = fwd or (lambda p: (float(p[0]), float(p[1])))
    g = inv or (lambda p: (float(p[0]), float(p[1])))
    ocx, ocy, ow, oh, oa = old
    ncx, ncy, nw, nh, na = new
    sx = (nw / ow) if ow else 1.0
    sy = (nh / oh) if oh else 1.0
    if not ow:
        sx = 1.0
    if not oh:
        sy = 1.0
    oc, nc = f((ocx, ocy)), f((ncx, ncy))
    ra, rb = math.radians(-oa), math.radians(na)

    def mp(p):
        dx, dy = f(p)[0] - oc[0], f(p)[1] - oc[1]
        x = dx * math.cos(ra) - dy * math.sin(ra)
        y = dx * math.sin(ra) + dy * math.cos(ra)
        x, y = x * sx, y * sy
        qx = x * math.cos(rb) - y * math.sin(rb) + nc[0]
        qy = x * math.sin(rb) + y * math.cos(rb) + nc[1]
        return g((qx, qy))

    k = child.kind
    if k in ("line", "arrow"):
        child.x0, child.y0 = mp((child.x0, child.y0))
        child.x1, child.y1 = mp((child.x1, child.y1))
    elif k == "xspan":
        a, b = mp((child.x0, ocy))[0], mp((child.x1, ocy))[0]
        child.x0, child.x1 = a, b
    elif k == "yspan":
        a, b = mp((ocx, child.y0))[1], mp((ocx, child.y1))[1]
        child.y0, child.y1 = a, b
    else:                                   # rect / ellipse / picture / text
        cx, cy = (child.x0 + child.x1) / 2.0, (child.y0 + child.y1) / 2.0
        qx, qy = mp((cx, cy))
        hw, hh = abs(child.x1 - child.x0) / 2.0, abs(child.y1 - child.y0) / 2.0
        child.x0, child.x1, child.y0, child.y1 = qx - hw, qx + hw, qy - hh, qy + hh
        # the child's own axes may be turned by ~90 deg relative to the root's
        rel = (child.angle - oa) % 180.0
        swap = 45.0 < rel < 135.0
        child.scale_x = float(child.scale_x) * (sy if swap else sx)
        child.scale_y = float(child.scale_y) * (sx if swap else sy)
        child.angle = float(child.angle) + (na - oa)


def make_shape(kind, geometry=None, style=None) -> ShapeRef:
    """Build a shape from plain dicts (``x0,y0,x1,y1`` ...) -- used by the GUI, the IPC
    handler and :class:`~NoorSuite.offline.OfflineBackend`."""
    s = ShapeRef(kind)
    s.apply_style({**(geometry or {}), **(style or {})})
    for f in ("x0", "y0", "x1", "y1"):
        try:
            setattr(s, f, float(getattr(s, f)))
        except (TypeError, ValueError):
            setattr(s, f, 0.0)
    return s


class SubplotModel:
    """Configuration for one panel / subplot within a sheet."""

    _FIELDS = ("title", "x_label", "y_label", "x_scale", "y_scale", "show_grid",
               "grid_axis", "grid_ticks", "grid_style", "grid_width", "grid_color",
               "grid_alpha", "aspect", "aspect_ratio",
               "x_tick_format", "y_tick_format", "x_tick_digits", "y_tick_digits",
               "x_tick_labels", "y_tick_labels",
               "x_min", "x_max", "y_min", "y_max",
               "tick_label_size", "face_color", "face_alpha",
               "spine_color", "spine_width", "spine_style",
               "title_fontsize", "xlabel_fontsize", "ylabel_fontsize",
               "legend_visible", "legend_loc", "legend_frame", "legend_fontsize",
               "legend_ncol", "bare")

    def __init__(self, title="Plot"):
        self.title = title
        self.x_label = ""
        self.y_label = ""
        self.x_scale = "linear"        # linear | log
        self.y_scale = "linear"
        self.show_grid = True
        # Tick label number representation: "auto" (matplotlib default) | "plain"
        # (never scientific, e.g. 12000) | "scientific" (always, e.g. 1.2e4) |
        # "fixed" (a fixed number of decimals, *_tick_digits). Display only -- the
        # underlying data (and any TraceRef.scale_factor) is untouched.
        self.x_tick_format = "auto"
        self.y_tick_format = "auto"
        self.x_tick_digits = 2
        self.y_tick_digits = 2
        # Custom tick labels as a value->text mapping: "0=Low; 1=Mid; 2=High" puts ticks at
        # 0, 1, 2 labelled Low/Mid/High (see parse_tick_labels). On a categorical heatmap
        # axis a non-numeric key renames that category instead ("ctrl=Control").
        self.x_tick_labels = ""
        self.y_tick_labels = ""
        # Data aspect: "auto" (default) | "equal" (1:1) | "custom" (aspect_ratio, y-per-x
        # data units). Ignored when the subplot has an image -- its own ImageRef.aspect
        # governs there instead.
        self.aspect = "auto"
        self.aspect_ratio = 1.0
        self.grid_axis = "both"        # both | x | y
        self.grid_ticks = "major"     # major | minor | both
        self.grid_style = "--"
        self.grid_width = 0.8
        self.grid_color = "#b0b0b0"
        self.grid_alpha = 0.5
        self.traces: list[TraceRef] = []
        self.shapes: list["ShapeRef"] = []    # drawn objects; z-ordered together with traces
        self.image: "ImageRef | None" = None

        # Axis limits (None -> autoscale)
        self.x_min = None
        self.x_max = None
        self.y_min = None
        self.y_max = None

        # Cosmetics
        self.tick_label_size = 13.0
        self.face_color = "#ffffff"    # axes background
        self.face_alpha = 1.0
        self.spine_color = "#000000"
        self.spine_width = 0.8
        self.spine_style = "-"
        self.title_fontsize = 16.0
        self.xlabel_fontsize = 15.0
        self.ylabel_fontsize = 15.0

        # Legend
        self.legend_visible = True
        self.legend_loc = "best"
        self.legend_frame = True
        self.legend_fontsize = 11.0
        self.legend_ncol = 1
        # "content only": no axes, ticks, labels, title, legend or background -- just what
        # is drawn inside (an image, a picture, objects, traces)
        self.bare = False

    # -- object hierarchy -----------------------------------------------------------
    def shape_by_id(self, shape_id):
        return next((s for s in self.shapes if s.shape_id == shape_id), None) \
            if shape_id else None

    def parent_of(self, shape):
        return self.shape_by_id(shape.parent_id)

    def children_of(self, shape) -> list:
        return [s for s in self.shapes if s.parent_id == shape.shape_id and s is not shape]

    def descendants(self, shape) -> list:
        out, todo = [], list(self.children_of(shape))
        while todo:
            s = todo.pop(0)
            if s not in out:
                out.append(s)
                todo.extend(self.children_of(s))
        return out

    def depth_of(self, shape) -> int:
        d, seen, cur = 0, {id(shape)}, self.parent_of(shape)
        while cur is not None and id(cur) not in seen:
            seen.add(id(cur))
            d += 1
            cur = self.parent_of(cur)
        return d

    def set_parent(self, shape, parent) -> bool:
        """Make ``shape`` move along with ``parent`` (``None`` -> a root). Refuses a cycle."""
        if parent is None:
            shape.parent_id = ""
            return True
        if parent is shape or parent in self.descendants(shape):
            return False
        shape.parent_id = parent.shape_id
        return True

    def remove_shape(self, shape) -> None:
        """Delete a shape; its children move up to its parent."""
        for c in self.children_of(shape):
            c.parent_id = shape.parent_id
        self.shapes = [s for s in self.shapes if s is not shape]

    # -- one z-ordered stack of traces and shapes (index 0 = back) ------------------
    def stack(self) -> list:
        """Traces and shapes interleaved back-to-front. A shape sits above
        ``min(layer_pos, len(traces))`` traces; shapes with the same position keep their
        ``shapes`` order. The result is exactly the list shown in the GUI and drawn."""
        out, n = [], len(self.traces)
        by_pos: dict = {}
        for sh in self.shapes:
            by_pos.setdefault(max(0, min(int(sh.layer_pos), n)), []).append(sh)
        for i in range(n + 1):
            out.extend(by_pos.get(i, []))
            if i < n:
                out.append(self.traces[i])
        return out

    def set_stack(self, items) -> None:
        """Adopt a (re)ordered stack: traces and shapes keep their relative order and every
        shape remembers how many traces lie below it."""
        items = list(items)
        keep = {id(x) for x in self.traces} | {id(x) for x in self.shapes}
        if {id(x) for x in items} != keep or len(items) != len(keep):
            raise ValueError("stack must be a permutation of the current traces and shapes")
        n_tr, traces, shapes = 0, [], []
        for it in items:
            if isinstance(it, ShapeRef):
                it.layer_pos = n_tr
                shapes.append(it)
            else:
                n_tr += 1
                traces.append(it)
        self.traces, self.shapes = traces, shapes

    def remove_trace(self, ref) -> None:
        """Delete a trace without letting the shapes above it slide down a layer."""
        if ref not in self.traces:
            return
        i = self.traces.index(ref)
        for sh in self.shapes:
            if int(sh.layer_pos) > i:
                sh.layer_pos = int(sh.layer_pos) - 1
        del self.traces[i]

    def to_dict(self) -> dict:
        d = {f: getattr(self, f) for f in self._FIELDS}
        d["traces"] = [t.to_dict() for t in self.traces]
        if self.shapes:
            d["shapes"] = [sh.to_dict() for sh in self.shapes]
        d["image"] = self.image.to_dict() if self.image is not None else None
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "SubplotModel":
        s = cls(d.get("title", "Plot"))
        for f in cls._FIELDS:
            if f in d and d[f] is not None:
                setattr(s, f, d[f])
        s.traces = [TraceRef.from_dict(t) for t in d.get("traces", [])]
        s.shapes = [ShapeRef.from_dict(x) for x in d.get("shapes", [])]
        known = {sh.shape_id for sh in s.shapes}
        for sh in s.shapes:                 # a parent that no longer exists -> root
            if sh.parent_id and sh.parent_id not in known:
                sh.parent_id = ""
        img = d.get("image")
        s.image = ImageRef.from_dict(img) if img else None
        return s


class SheetModel:
    """A tab holding an NxM grid of subplots."""

    _FIG_FIELDS = ("fig_face_color", "fig_face_alpha", "fig_frame_on",
                   "fig_edge_color", "fig_edge_width", "fig_edge_style",
                   "fig_width_cm", "fig_height_cm", "fig_real_points")

    def __init__(self, name="Sheet 1", rows=1, cols=1, sheet_id=None):
        self.sheet_id = sheet_id or _new_id()
        self.name = name
        self.rows = rows
        self.cols = cols
        self.active_index = 0
        self.subplots = [SubplotModel(f"Subplot {i + 1}") for i in range(rows * cols)]

        self.fig_face_color = "#ffffff"
        self.fig_face_alpha = 1.0
        self.fig_frame_on = False
        self.fig_edge_color = "#000000"
        self.fig_edge_width = 0.0
        self.fig_edge_style = "-"
        # Physical export size in cm; None (either) -> auto (current on-screen aspect,
        # trimmed to content). Used by "Copy to clipboard" / "Export as SVG" only -- the
        # on-screen figure still fills its tab like before.
        self.fig_width_cm = None
        self.fig_height_cm = None
        # True: fonts are real points on the figure's physical size (what you see is what the
        # document prints). False (older sheets): text sizes follow the figure's size relative to
        # an 8x6 inch figure and the grid, so small figures do not drown in text.
        self.fig_real_points = True      # new sheets: a 13 pt tick label is 13 pt on paper

        self.tags: list[str] = []
        self.colormaps: list[ColorMap] = []   # custom colormaps owned by this sheet
        self.active_colormap = ""             # "" -> matplotlib prop-cycle default
        self.notes = ""                       # free-text annotation (the "Notes" tab)

    def set_grid(self, rows, cols) -> None:
        self.rows = rows
        self.cols = cols
        need = rows * cols
        while len(self.subplots) < need:
            self.subplots.append(SubplotModel(f"Subplot {len(self.subplots) + 1}"))
        self.subplots = self.subplots[:need]
        if self.active_index >= need:
            self.active_index = 0

    def get_active_subplot(self) -> SubplotModel:
        if 0 <= self.active_index < len(self.subplots):
            return self.subplots[self.active_index]
        return self.subplots[0]

    def to_dict(self) -> dict:
        d = {
            "sheet_id": self.sheet_id,
            "name": self.name,
            "rows": self.rows,
            "cols": self.cols,
            "active_index": self.active_index,
            "tags": list(self.tags),
            "notes": self.notes,
            "active_colormap": self.active_colormap,
            "colormaps": [c.to_dict() for c in self.colormaps],
            "subplots": [s.to_dict() for s in self.subplots],
        }
        d.update({f: getattr(self, f) for f in self._FIG_FIELDS})
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "SheetModel":
        sh = cls(d.get("name", "Sheet"), d.get("rows", 1), d.get("cols", 1), d.get("sheet_id"))
        sh.active_index = d.get("active_index", 0)
        sh.fig_real_points = False       # sheets saved before the option keep their scaled text
        for f in cls._FIG_FIELDS:
            if f in d and d[f] is not None:
                setattr(sh, f, d[f])
        sh.tags = list(d.get("tags", []))
        sh.notes = d.get("notes", "")
        sh.active_colormap = d.get("active_colormap", "")
        sh.colormaps = [ColorMap.from_dict(c) for c in d.get("colormaps", [])]
        subs = d.get("subplots")
        if subs:
            sh.subplots = [SubplotModel.from_dict(s) for s in subs]
        sh.set_grid(sh.rows, sh.cols)   # keep subplot count consistent with the grid
        return sh


class AssetModel:
    """An imported figure file (SVG / PDF / PNG / JPG) stored inside the project. Used by
    LaTeX documents (``\\noorfile{<id>}``) and as a picture object on a subplot.
    ``version`` counts external edits (Inkscape round trips)."""

    def __init__(self, name="Figure", ext="svg", data=b"", asset_id=None, version=1):
        self.asset_id = asset_id or _new_id()
        self.name = name
        self.ext = ext
        self.data = bytes(data)
        self.version = int(version)

    def to_dict(self, sidecar=False) -> dict:
        import base64
        d = {"asset_id": self.asset_id, "name": self.name, "ext": self.ext,
             "version": self.version}
        d["data"] = "" if sidecar else base64.b64encode(self.data).decode("ascii")
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "AssetModel":
        import base64
        return cls(d.get("name", "Figure"), d.get("ext", "svg"),
                   base64.b64decode(d.get("data", "") or b""), d.get("asset_id"),
                   d.get("version", 1))


class TableModel:
    """A table of a data object for LaTeX / Word documents (see :mod:`NoorSuite.tables`).
    ``columns`` = ``[{"col", "header", "fmt", "digits", "align", "scale"}]`` in display order;
    ``data_id`` is the data object's id (a name also works)."""

    _FIELDS = ("name", "data_id", "columns", "row_start", "row_end", "step", "max_rows",
               "sort_by", "descending", "booktabs")

    def __init__(self, name="Table", data_id="", columns=None, table_id=None):
        self.table_id = table_id or _new_id()
        self.name = name
        self.data_id = data_id
        self.columns = [dict(c) for c in (columns or [])]
        self.row_start = 1
        self.row_end = None          # None -> to the last row
        self.step = 1
        self.max_rows = 0            # 0 -> no limit
        self.sort_by = ""            # column name; "" -> keep the data order
        self.descending = False
        self.booktabs = True

    def to_dict(self) -> dict:
        d = {f: getattr(self, f) for f in self._FIELDS}
        d["table_id"] = self.table_id
        d["columns"] = [dict(c) for c in self.columns]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "TableModel":
        t = cls(d.get("name", "Table"), d.get("data_id", ""), d.get("columns"), d.get("table_id"))
        for f in cls._FIELDS:
            if f in d and d[f] is not None and f not in ("name", "data_id", "columns"):
                setattr(t, f, d[f])
        return t


class DocumentModel:
    """A LaTeX document stored in the project: ``source`` is the ``.tex`` text; figures are
    sheets referenced with ``\noorsheet{<sheet id>}`` (see :mod:`NoorSuite.latex`)."""

    def __init__(self, name="Document", source="", doc_id=None, bib_style="",
                 cite_mode="superscript", layout=""):
        self.doc_id = doc_id or _new_id()
        self.name = name
        self.source = source
        # reference-list format (latex.BIB_STYLES id) for *this* document; "" -> the user's
        # default. Kept per document so changing it elsewhere never reformats this one.
        self.bib_style = bib_style
        # how citation numbers appear in the text (refstyle.CITE_MODES), native formats only
        self.cite_mode = cite_mode
        # id of a user layout ("user:<slug>") applied to this document; "" -> detected from
        # the class / packages
        self.layout = layout

    def to_dict(self) -> dict:
        return {"doc_id": self.doc_id, "name": self.name, "source": self.source,
                "bib_style": self.bib_style, "cite_mode": self.cite_mode,
                "layout": self.layout}

    @classmethod
    def from_dict(cls, d: dict) -> "DocumentModel":
        return cls(d.get("name", "Document"), d.get("source", ""), d.get("doc_id"),
                   d.get("bib_style", ""), d.get("cite_mode", "superscript"),
                   d.get("layout", ""))


class ProjectModel:
    """Serialization root: everything needed to restore a session."""

    def __init__(self):
        self.version = PROJECT_VERSION
        self.data_objects: list[DataObject] = []
        self.images: list[ImageObject] = []
        self.sheets: list[SheetModel] = []
        self.tree: list[dict] = []          # folder/sheet organization (raw nodes)
        self.colormaps: list[ColorMap] = []  # project-wide colormap library
        self.documents: list[DocumentModel] = []   # LaTeX documents (optional key)
        self.references: list[dict] = []           # bibliography records (see zotero.normalize)
        self.assets: list[AssetModel] = []         # imported SVG / PDF / PNG / JPG figures
        self.tables: list[TableModel] = []         # data tables for documents (optional key)
        self.active_sheet_id = ""

    def to_dict(self, sidecar=False) -> dict:
        """The JSON-ready project. ``sidecar=True`` leaves big numeric data and imported figure
        files out (``save`` stores them as binary files next to the project)."""
        return {
            "version": PROJECT_VERSION,
            "active_sheet_id": self.active_sheet_id,
            "data_objects": [d.to_dict(sidecar) for d in self.data_objects],
            "images": [im.to_dict() for im in self.images],
            "sheets": [s.to_dict() for s in self.sheets],
            "tree": self.tree,
            "colormaps": [c.to_dict() for c in self.colormaps],
            "documents": [dm.to_dict() for dm in self.documents],
            "references": [dict(r) for r in self.references],
            "assets": [a.to_dict(sidecar) for a in self.assets],
            "tables": [t.to_dict() for t in self.tables],
        }

    def signature(self) -> str:
        """Hash of everything that would be written -- computed from the small parts plus the
        identity of the big arrays, so an unchanged project is recognised in milliseconds."""
        import hashlib
        d = self.to_dict(sidecar=True)
        for entry, obj in zip(d["data_objects"], self.data_objects):
            entry["sig"] = repr(obj.signature())
        for entry, a in zip(d["assets"], self.assets):
            entry["sig"] = (len(a.data), id(a.data))
        for entry, im in zip(d["images"], self.images):
            entry["sig"] = (id(im.data), tuple(im.data.shape))
        raw = json.dumps(d, sort_keys=True, default=str)
        return hashlib.blake2b(raw.encode("utf-8"), digest_size=16).hexdigest()

    @classmethod
    def from_dict(cls, d: dict) -> "ProjectModel":
        version = d.get("version")
        if version != PROJECT_VERSION:
            raise ValueError(
                f"Unsupported project version {version!r}; this build reads "
                f"version {PROJECT_VERSION}."
            )
        p = cls()
        p.active_sheet_id = d.get("active_sheet_id", "")
        p.data_objects = [DataObject.from_dict(x) for x in d.get("data_objects", [])]
        p.images = [ImageObject.from_dict(x) for x in d.get("images", [])]
        p.sheets = [SheetModel.from_dict(x) for x in d.get("sheets", [])]
        p.tree = d.get("tree", [])
        p.colormaps = [ColorMap.from_dict(c) for c in d.get("colormaps", [])]
        p.documents = [DocumentModel.from_dict(x) for x in d.get("documents", [])]
        p.references = [dict(r) for r in d.get("references", []) if r.get("key")]
        p.assets = [AssetModel.from_dict(x) for x in d.get("assets", [])]
        p.tables = [TableModel.from_dict(x) for x in d.get("tables", [])]
        return p

    def to_json(self, indent=2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_json(cls, text: str) -> "ProjectModel":
        return cls.from_dict(json.loads(text))

    # --- project file + image sidecar folder ---------------------------------
    @staticmethod
    def _assets_dir(path) -> Path:
        return Path(path).with_suffix("")     # report.sciproj -> report/

    def save(self, path, cache=None) -> None:
        """Write the project to ``path`` (see :meth:`prepare_save`)."""
        self.prepare_save(path, cache).run()

    def prepare_save(self, path, cache=None) -> "SavePlan":
        """Everything that touches the live model, done now (on the GUI thread): the JSON-ready
        payload and the list of files to write. ``SavePlan.run()`` only does file I/O and can
        run on another thread.

        * big data objects -> ``<stem>/_data/<id>.npz`` (raw binary, ~8 bytes per number instead
          of ~25 in JSON), small ones stay inline so tiny projects remain readable;
        * imported figure files -> ``<stem>/_files/<id>.<ext>`` (no base64 in the JSON);
        * image arrays -> ``<stem>/<name>.npy`` as before;
        * a file is rewritten only when its content changed (``cache`` remembers what was
          written; pass the same dict for every save to the same path);
        * the JSON is written to a temp file and renamed, so a crash cannot truncate it.
        """
        path = Path(path)
        cache = cache if cache is not None else {}
        folder = self._assets_dir(path)
        payload = self.to_dict(sidecar=True)
        jobs, keep_npy, keep_data, keep_files = [], set(), set(), set()
        dcache = cache.setdefault("data", {})
        fcache = cache.setdefault("files", {})

        names = resolve_sidecar_names(self.images)
        by_id = {im.id: im for im in self.images}
        for entry in payload["images"]:
            obj = by_id[entry["id"]]
            fname = names[obj.id]
            target = folder / fname
            keep_npy.add(fname)
            if obj._dirty or not target.exists():
                jobs.append(_ImageJob(target, obj))
            entry["array_file"] = f"{folder.name}/{fname}"

        for entry, obj in zip(payload["data_objects"], self.data_objects):
            if obj.n_values() <= SIDECAR_MIN_VALUES:
                continue
            fname = f"{obj.id}.npz"
            target = folder / "_data" / fname
            sig = obj.signature()
            keep_data.add(fname)
            entry["data_file"] = f"{folder.name}/_data/{fname}"
            if dcache.get(obj.id) != sig or not target.is_file():
                jobs.append(_NpzJob(target, [obj.columns[c] for c in obj.column_order],
                                    dcache, obj.id, sig))

        for entry, a in zip(payload["assets"], self.assets):
            if not a.data:
                continue
            fname = f"{a.asset_id}.{a.ext}"
            target = folder / "_files" / fname
            sig = (a.version, len(a.data), id(a.data))
            keep_files.add(fname)
            entry["data_file"] = f"{folder.name}/_files/{fname}"
            if fcache.get(a.asset_id) != sig or not target.is_file():
                jobs.append(_BytesJob(target, a.data, fcache, a.asset_id, sig))
        return SavePlan(path, payload, jobs, folder, keep_npy, keep_data, keep_files)

    def seed_cache(self, path, cache: dict) -> None:
        """After loading ``path``: remember which sidecar files already hold the data, so the
        first save does not rewrite them."""
        folder = self._assets_dir(path)
        dcache = cache.setdefault("data", {})
        fcache = cache.setdefault("files", {})
        for obj in self.data_objects:
            if obj.n_values() > SIDECAR_MIN_VALUES and (folder / "_data" / f"{obj.id}.npz").is_file():
                dcache[obj.id] = obj.signature()
        for a in self.assets:
            if a.data and (folder / "_files" / f"{a.asset_id}.{a.ext}").is_file():
                fcache[a.asset_id] = (a.version, len(a.data), id(a.data))

    @staticmethod
    def _sweep_assets(assets: Path, keep: set, keep_data=None, keep_files=None) -> None:
        """Delete every ``*.npy`` in ``assets`` not in ``keep`` (and the ``_data`` / ``_files``
        sidecars nobody owns any more); drop the folder if it ends up empty."""
        if not assets.is_dir():
            return

        def clean(folder: Path, owned, pattern="*"):
            if not folder.is_dir():
                return
            for f in folder.glob(pattern):
                if f.is_file() and f.name not in owned:
                    try:
                        f.unlink()
                    except OSError:
                        pass
            try:
                next(folder.iterdir())
            except StopIteration:
                try:
                    folder.rmdir()
                except OSError:
                    pass
            except OSError:
                pass
        for stale in assets.glob("*.npy"):
            if stale.name not in keep:
                try:
                    stale.unlink()
                except OSError:
                    pass
        clean(assets / "_data", keep_data or set())
        clean(assets / "_files", keep_files or set())
        try:
            next(assets.iterdir())
        except StopIteration:
            try:
                assets.rmdir()
            except OSError:
                pass
        except OSError:
            pass

    @classmethod
    def load(cls, path) -> "ProjectModel":
        path = Path(path)
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        project = cls.from_dict(raw)
        project.missing = []                      # sidecar files the project refers to but not found
        for obj, entry in zip(project.data_objects, raw.get("data_objects", [])):
            ref = entry.get("data_file")
            if not ref:
                continue
            if (path.parent / ref).is_file():
                with np.load(path.parent / ref, allow_pickle=False) as z:
                    obj.column_order = list(entry.get("column_order", []))
                    obj.columns = {c: np.asarray(z[f"c{i}"], dtype=float)
                                   for i, c in enumerate(obj.column_order)}
            else:
                project.missing.append(f"data '{obj.name}': {ref}")
        for a, entry in zip(project.assets, raw.get("assets", [])):
            ref = entry.get("data_file")
            if not ref:
                continue
            if (path.parent / ref).is_file():
                a.data = (path.parent / ref).read_bytes()
            else:
                project.missing.append(f"figure '{a.name}': {ref}")
        raw_images = {x["id"]: x for x in raw.get("images", [])}
        for img in project.images:
            ref = raw_images.get(img.id, {}).get("array_file")
            if ref:
                arr_path = (path.parent / ref)
                if arr_path.is_file():
                    img.data = np.load(arr_path, allow_pickle=False)
                    img._dirty = False
        return project


# ---------------------------------------------------------------------------
# Organizing: names / tags / notes / folders (shared by the GUI and offline backend)
# ---------------------------------------------------------------------------
def merge_tags(old, new) -> list:
    """``old`` plus any of ``new`` not already present (case-insensitive), order kept."""
    out = list(old)
    seen = {t.lower() for t in out}
    for t in new or []:
        t = str(t).strip()
        if t and t.lower() not in seen:
            out.append(t)
            seen.add(t.lower())
    return out


def append_notes(old: str, new: str) -> str:
    return (old.rstrip() + "\n\n" + new).strip() if old.strip() else new


def split_folder_path(path) -> list:
    """``"Project A/Run 4"`` -> ``["Project A", "Run 4"]`` (``/`` or backslash separated)."""
    return [p.strip() for p in str(path or "").replace("\\", "/").split("/") if p.strip()]


def apply_sheet_annotations(sm, payload: dict, assets=None) -> None:
    """Apply an ``organize`` payload's sheet fields: ``name``, ``tags`` (merged in),
    ``notes`` (appended unless ``replace_notes``), ``subplots`` ({index: {title, x_label,
    y_label}}) and ``grid`` ([rows, cols])."""
    if payload.get("name"):
        sm.name = payload["name"]
    if payload.get("grid"):
        sm.set_grid(int(payload["grid"][0]), int(payload["grid"][1]))
    if payload.get("tags"):
        sm.tags = merge_tags(sm.tags, payload["tags"])
    if payload.get("notes"):
        sm.notes = payload["notes"] if payload.get("replace_notes")             else append_notes(sm.notes, payload["notes"])
    for idx, fields in (payload.get("subplots") or {}).items():
        i = int(idx)
        if 0 <= i < len(sm.subplots):
            for f in ("title", "x_label", "y_label", "x_tick_labels", "y_tick_labels"):
                if fields.get(f):
                    setattr(sm.subplots[i], f, fields[f])
            if "bare" in fields:
                sm.subplots[i].bare = bool(fields["bare"])
    # drawn objects: {"subplot": i, "kind", "geometry": {x0..}, "style": {...},
    #                 "position": "front" | "back"}; "clear_shapes": [subplot indices]
    for i in payload.get("clear_shapes") or []:
        if 0 <= int(i) < len(sm.subplots):
            sm.subplots[int(i)].shapes = []
    for spec in payload.get("shapes") or []:
        i = int(spec.get("subplot", 0))
        if not 0 <= i < len(sm.subplots):
            continue
        sub = sm.subplots[i]
        sh = make_shape(spec.get("kind", "rect"), spec.get("geometry"), spec.get("style"))
        sh.name = spec.get("name") or sh.name
        if spec.get("asset"):                           # a picture: asset by id or name
            key = spec["asset"]
            hit = next((a for a in (assets or []) if key in (a.asset_id, a.name)), None)
            sh.asset_id = hit.asset_id if hit is not None else str(key)
            sh.kind = "picture"
            sh.fill = False
        if spec.get("parent"):                          # moves along with that object
            key = spec["parent"]
            par = next((s for s in sub.shapes
                        if key in (s.shape_id, s.name) and s is not sh), None)
            if par is not None:
                sh.parent_id = par.shape_id
        sh.layer_pos = 0 if spec.get("position") == "back" else len(sub.traces)
        if spec.get("position") == "back":
            sub.shapes.insert(0, sh)
        else:
            sub.shapes.append(sh)


def tree_move_sheet(nodes: list, sheet_id: str, folder_path: list,
                    kind: str = "sheet") -> list:
    """Return ``nodes`` (raw project tree) with the sheet (or, ``kind="document"``, the
    document) ``sheet_id`` moved into the folder chain ``folder_path`` (created as
    needed; ``[]`` = top level)."""
    key = "sheet_id" if kind == "sheet" else "doc_id"

    def strip(lst):
        out = []
        for n in lst:
            if n.get("type") in ("sheet", "document"):
                if not (n.get("type") == kind and n.get(key) == sheet_id):
                    out.append(n)
            elif n.get("type") == "folder":
                out.append({**n, "children": strip(n.get("children", []))})
            else:
                out.append(n)
        return out

    nodes = strip(nodes)
    level = nodes
    for part in folder_path:
        folder = next((n for n in level if n.get("type") == "folder"
                       and n.get("name") == part), None)
        if folder is None:
            folder = {"type": "folder", "id": _new_id(), "name": part, "children": []}
            level.append(folder)
        level = folder["children"]
    level.append({"type": kind, key: sheet_id})
    return nodes


# ---------------------------------------------------------------------------
# Copying subplot style / duplicating sheets (shared by the GUI and tests)
# ---------------------------------------------------------------------------
SUBPLOT_LABEL_FIELDS = ("title", "x_label", "y_label", "x_tick_labels", "y_tick_labels")
SUBPLOT_LIMIT_FIELDS = ("x_min", "x_max", "y_min", "y_max")
# Everything on a SubplotModel that is pure styling (cosmetics, grid, legend, number
# format, aspect, scales) -- not content (title/labels) or data range (limits).
SUBPLOT_STYLE_FIELDS = tuple(
    f for f in SubplotModel._FIELDS
    if f not in SUBPLOT_LABEL_FIELDS + SUBPLOT_LIMIT_FIELDS)


def copy_subplot_style(src, dst, include_labels=False, include_limits=False) -> None:
    """Copy ``src``'s style onto ``dst``; optionally also the title/axis labels and/or
    the axis limits. Traces and images are never copied."""
    fields = list(SUBPLOT_STYLE_FIELDS)
    if include_labels:
        fields += SUBPLOT_LABEL_FIELDS
    if include_limits:
        fields += SUBPLOT_LIMIT_FIELDS
    for f in fields:
        setattr(dst, f, getattr(src, f))


def duplicate_sheet_model(sm, name=None):
    """A deep copy of ``sm`` with a fresh ``sheet_id`` (traces keep pointing at the same
    data objects); named ``"<name> (copy)"`` unless ``name`` is given."""
    import copy
    d = copy.deepcopy(sm.to_dict())
    d["sheet_id"] = _new_id()
    d["name"] = name or f"{sm.name} (copy)"
    return SheetModel.from_dict(d)


THIN_MIN_POINTS = 20_000       # shorter lines are always drawn as they are


def thin_line(x, y, n_bins: int, visible_fraction: float = 1.0):
    """Min / max decimation of a long, monotonic line for drawing: every bin of consecutive points
    keeps its lowest and highest value (plus the first and last point), so spikes and peaks survive
    and the picture at the resolution of ``n_bins`` columns is the same as with all points.

    Anything that cannot be thinned safely (short, NaN / inf, not monotonic in x) comes back
    unchanged. ``visible_fraction`` < 1 (explicit axis limits) raises the resolution so a zoomed
    view still gets about ``n_bins`` bins inside the window."""
    x = np.asarray(x)
    y = np.asarray(y)
    n = len(x)
    if n < THIN_MIN_POINTS or len(y) != n or n_bins < 16:
        return x, y
    f = min(1.0, max(1e-3, float(visible_fraction)))
    bins = int(min(n // 8, n_bins / f))
    if n < 4 * bins:
        return x, y
    d = np.diff(x)
    if not (np.all(d >= 0) or np.all(d <= 0)):
        return x, y
    if not (np.isfinite(y).all() and np.isfinite(x).all()):
        return x, y
    per = n // bins
    main = per * bins
    yy = y[:main].reshape(bins, per)
    base = np.arange(bins) * per
    idx = [base + yy.argmin(axis=1), base + yy.argmax(axis=1), np.array([0, n - 1])]
    if main < n:
        tail = y[main:]
        idx.append(np.array([main + int(tail.argmin()), main + int(tail.argmax())]))
    keep = np.unique(np.concatenate(idx))
    return x[keep], y[keep]


# ---------------------------------------------------------------------------
# Heatmaps: grids, colour bands, isoline levels, tick positions (Qt/matplotlib-free)
# ---------------------------------------------------------------------------
TICK_MODES = ["auto", "data", "custom"]
TICK_MODE_LABELS = ["Automatic", "At every data coordinate", "Custom values"]


def parse_float_list(text) -> list:
    """``"1, 2.5; 3"`` -> ``[1.0, 2.5, 3.0]`` (comma / semicolon / whitespace separated);
    unparseable items are skipped."""
    out = []
    for tok in str(text or "").replace(";", ",").replace("\n", ",").replace(" ", ",").split(","):
        if tok.strip():
            try:
                out.append(float(tok))
            except ValueError:
                pass
    return out


def heatmap_from_xyz(x, y, z):
    """Pivot scattered/long-format ``(x, y, z)`` samples onto the grid of their unique
    values: returns ``(grid, xs, ys)`` with ``grid[i, j]`` = mean ``z`` at ``(xs[j], ys[i])``
    (NaN where there is no sample), so it plugs straight into an image/heatmap object
    (axis 0 = y rows, axis 1 = x columns)."""
    x, y, z = (np.asarray(a, dtype=float) for a in (x, y, z))
    xs, xi = np.unique(x, return_inverse=True)
    ys, yi = np.unique(y, return_inverse=True)
    total = np.zeros((len(ys), len(xs)))
    count = np.zeros((len(ys), len(xs)))
    ok = np.isfinite(z)
    np.add.at(total, (yi[ok], xi[ok]), z[ok])
    np.add.at(count, (yi[ok], xi[ok]), 1)
    with np.errstate(invalid="ignore", divide="ignore"):
        grid = np.where(count > 0, total / count, np.nan)
    return grid, xs, ys


def heatmap_band_edges(ref, vmin, vmax):
    """Edges of the discrete colour bands, or ``None`` for a continuous colormap:
    ``ref.cmap_boundaries`` if it lists 2+ distinct values, else ``cmap_bins`` equal bands
    between ``vmin`` and ``vmax``."""
    edges = sorted(set(parse_float_list(ref.cmap_boundaries)))
    if len(edges) >= 2:
        return np.asarray(edges, dtype=float)
    if int(ref.cmap_bins) >= 2 and vmax > vmin:
        return np.linspace(vmin, vmax, int(ref.cmap_bins) + 1)
    return None


def nice_levels(vmin, vmax, n) -> list:
    """About ``n`` round-valued levels strictly inside ``[vmin, vmax]`` (1/2/2.5/5 x 10^k steps)."""
    if not (np.isfinite(vmin) and np.isfinite(vmax)) or vmax <= vmin or n < 1:
        return []
    raw = (vmax - vmin) / (n + 1)
    mag = 10 ** np.floor(np.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw * 0.75)
    first = np.ceil(vmin / step) * step
    levels = np.arange(first, vmax + step * 1e-9, step)
    return [float(round(v, 12)) for v in levels if vmin < v < vmax]


def heatmap_iso_levels(ref, vmin, vmax, band_edges=None) -> list:
    """Levels at which to draw isolines: ``ref.iso_levels`` if given, else the interior colour
    -band edges of a discrete colormap, else about ``iso_count`` round values -- then
    restricted to ``iso_above <= level <= iso_below`` (either bound optional)."""
    levels = sorted(set(parse_float_list(ref.iso_levels)))
    if not levels:
        if band_edges is not None:
            levels = [float(v) for v in band_edges[1:-1]]
        else:
            levels = nice_levels(vmin, vmax, int(ref.iso_count))
    if ref.iso_above is not None:
        levels = [v for v in levels if v >= ref.iso_above]
    if ref.iso_below is not None:
        levels = [v for v in levels if v <= ref.iso_below]
    return levels


def resolve_ticks(mode, values_text, every, positions, labels=None):
    """Tick ``(positions, labels)`` for one heatmap axis, or ``None`` for automatic.
    ``positions`` are the cell centres (data coordinates), ``labels`` optional category names."""
    if mode == "custom":
        vals = parse_float_list(values_text)
        return (vals, [f"{v:.4g}" for v in vals]) if vals else None
    if mode == "data":
        step = max(1, int(every or 1))
        idx = range(0, len(positions), step)
        pos = [float(positions[i]) for i in idx]
        lab = [labels[i] if labels else f"{positions[i]:.4g}" for i in idx]
        return pos, lab
    return None


def make_image_ref(obj, display_axes, style=None) -> "ImageRef":
    """The :class:`ImageRef` for placing ``obj`` with ``display_axes`` (slice positions
    start mid-stack). A heatmap (coordinates on a displayed axis) defaults to a free aspect,
    since its cells are in data units, not square pixels; ``style`` overrides anything."""
    r, c = int(display_axes[0]), int(display_axes[1])
    index = {a: obj.shape[a] // 2 for a in range(obj.ndim) if a not in (r, c)}
    ref = ImageRef(obj.id, [r, c], index)
    if r in obj.axis_coords or c in obj.axis_coords:
        ref.aspect = "auto"
    ref.apply_style(style or {})
    return ref


# ---------------------------------------------------------------------------
# Custom tick labels: a value -> string mapping per axis
# ---------------------------------------------------------------------------
def parse_tick_labels(text) -> list:
    """``"0=Low; 1=Mid; 2=High"`` -> ``[("0", "Low"), ("1", "Mid"), ("2", "High")]``.
    Entries are separated by ``;`` or newlines, key and label by the first ``=``; entries
    without ``=`` are skipped. (Labels cannot contain ``;``.)"""
    out = []
    for item in str(text or "").replace("\n", ";").split(";"):
        key, sep, label = item.partition("=")
        if sep and key.strip():
            out.append((key.strip(), label.strip()))
    return out


def format_tick_labels(spec) -> str:
    """Normalize a user-facing spec to the stored text: a mapping ``{value: text}`` (keys
    numbers, or category names to rename), or a sequence of strings placed at positions
    ``0..n-1``; a string is passed through."""
    if not spec:
        return ""
    if isinstance(spec, str):
        return spec
    items = spec.items() if isinstance(spec, dict) else enumerate(spec)
    return "; ".join(f"{k:g}={v}" if isinstance(k, (int, float)) and not isinstance(k, bool)
                     else f"{k}={v}" for k, v in items)


def apply_tick_labels(text, base_ticks=None, base_names=None):
    """Resolve an axis's tick-label mapping to ``(ticks, names)``, or ``None`` for no change.

    * A key equal to one of ``base_names`` (a categorical axis' labels) **renames** that
      tick; the other ticks keep their position and name.
    * Otherwise numeric keys define the ticks themselves: one tick per key, at that data
      coordinate, labelled with its text (replacing any base ticks).
    """
    pairs = parse_tick_labels(text)
    if not pairs:
        return None
    names = list(base_names) if base_names is not None else []
    renames = {k: v for k, v in pairs if k in names}
    if renames and base_ticks is not None:
        return list(base_ticks), [renames.get(n, n) for n in names]
    numeric = []
    for k, label in pairs:
        try:
            numeric.append((float(k), label))
        except ValueError:
            pass
    if not numeric:
        return None
    numeric.sort(key=lambda p: p[0])
    return [p[0] for p in numeric], [p[1] for p in numeric]


# ---------------------------------------------------------------------------
# Saving: jobs that only touch files (safe on a worker thread)
# ---------------------------------------------------------------------------
def _replace_atomic(tmp: Path, target: Path) -> None:
    import os
    os.replace(tmp, target)


class _ImageJob:
    def __init__(self, target: Path, img):
        self.target, self.img = target, img

    def __call__(self):
        self.target.parent.mkdir(parents=True, exist_ok=True)
        np.save(self.target, self.img.data)
        self.img._dirty = False


class _NpzJob:
    def __init__(self, target: Path, arrays, cache: dict, key: str, sig):
        self.target, self.arrays, self.cache, self.key, self.sig = target, arrays, cache, key, sig

    def __call__(self):
        self.target.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.target.with_name(self.target.name + ".tmp")
        with open(tmp, "wb") as fh:
            np.savez(fh, **{f"c{i}": a for i, a in enumerate(self.arrays)})
        _replace_atomic(tmp, self.target)
        self.cache[self.key] = self.sig


class _BytesJob:
    def __init__(self, target: Path, data: bytes, cache: dict, key: str, sig):
        self.target, self.data, self.cache, self.key, self.sig = target, data, cache, key, sig

    def __call__(self):
        self.target.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.target.with_name(self.target.name + ".tmp")
        tmp.write_bytes(self.data)
        _replace_atomic(tmp, self.target)
        self.cache[self.key] = self.sig


class SavePlan:
    """The file-writing half of a save (see :meth:`ProjectModel.prepare_save`)."""

    def __init__(self, path, payload, jobs, folder, keep_npy, keep_data, keep_files):
        self.path, self.payload, self.jobs = path, payload, jobs
        self.folder, self.keep = folder, (keep_npy, keep_data, keep_files)

    def run(self) -> None:
        for job in self.jobs:
            job()
        tmp = self.path.with_name(self.path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.payload, fh, indent=1)
        _replace_atomic(tmp, self.path)
        ProjectModel._sweep_assets(self.folder, *self.keep)
