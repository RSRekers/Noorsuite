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
PLOT_TYPES = ["Line", "Scatter", "Line+Scatter", "Step", "Bar"]
LINE_STYLES = ["-", "--", "-.", ":"]
LINE_STYLE_LABELS = ["Solid (-)", "Dashed (--)", "Dash-Dot (-.)", "Dotted (:)"]
MARKERS = ["None", "o", "s", "^", "D", "x", "+"]
MARKER_LABELS = ["None", "Circle (o)", "Square (s)", "Triangle (^)",
                 "Diamond (D)", "Cross (x)", "Plus (+)"]
Y_TRANSFORMS = ["1x", "1e3", "1e-3", "1e-6", "1e-9", "Log10", "Norm"]
Y_TRANSFORM_LABELS = ["1x", "1e3 (kilo)", "1e-3 (milli)", "1e-6 (micro)",
                      "1e-9 (nano)", "Log10", "Norm (0-1)"]
LEGEND_LOCS = ["best", "upper right", "upper left", "lower left", "lower right",
               "right", "center left", "center right", "lower center",
               "upper center", "center"]


def _new_id() -> str:
    return str(uuid.uuid4())[:8]


def apply_y_transform(y, factor: str):
    """Return ``y`` transformed according to a scale-factor token.

    ``factor`` is one of :data:`Y_TRANSFORMS` (or a longer label that merely
    *contains* one, e.g. ``"1e3 (kilo)"``).  Unknown tokens return ``y`` unchanged.
    """
    y = np.asarray(y, dtype=float)
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


class DataObject:
    """A named set of columns in the data pool (typically one pushed DataFrame)."""

    def __init__(self, name, columns=None, column_order=None, units=None,
                 tags=None, source="", obj_id=None):
        self.id = obj_id or _new_id()
        self.name = name
        self.columns: dict[str, np.ndarray] = {}
        self.column_order: list[str] = []
        if columns:
            for col in (column_order or list(columns.keys())):
                self.columns[col] = np.asarray(columns[col], dtype=float)
                self.column_order.append(col)
        self.units: dict[str, str] = dict(units or {})
        self.tags = list(tags or [])
        self.source = source

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
        if units:
            self.units.update(units)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "column_order": list(self.column_order),
            "columns": {c: self.columns[c].tolist() for c in self.column_order},
            "units": dict(self.units),
            "tags": list(self.tags),
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DataObject":
        return cls(d["name"], d.get("columns", {}), d.get("column_order"),
                   d.get("units"), d.get("tags"), d.get("source", ""), d.get("id"))


class TraceRef:
    """A pointer from a subplot to one ``(data object, x col, y col)`` + its style."""

    _STYLE_FIELDS = ("plot_type", "color", "edge_color", "line_style", "line_width",
                     "marker", "marker_size", "alpha", "scale_factor")

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

    @property
    def display_label(self) -> str:
        return self.label or self.y_col

    def style_dict(self) -> dict:
        return {f: getattr(self, f) for f in self._STYLE_FIELDS}

    def apply_style(self, d: dict) -> None:
        for f in self._STYLE_FIELDS:
            if f in d and d[f] is not None:
                setattr(self, f, d[f])

    def resolve(self, data_object: DataObject):
        """Return ``(x_array, y_array)`` with the y-transform applied."""
        x = np.asarray(data_object.get(self.x_col), dtype=float)
        y = apply_y_transform(data_object.get(self.y_col), self.scale_factor)
        return x, np.asarray(y, dtype=float)

    def to_dict(self) -> dict:
        d = {"data_id": self.data_id, "x_col": self.x_col, "y_col": self.y_col,
             "label": self.label, "enabled": self.enabled}
        d.update(self.style_dict())
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "TraceRef":
        t = cls(d["data_id"], d.get("x_col", ""), d["y_col"],
                d.get("label", ""), d.get("enabled", True))
        t.apply_style(d)
        return t


class ImageObject:
    """A named ND array in the data pool, shown as an image (a 2-D slice at a time)."""

    def __init__(self, name, data, axis_names=None, axis_units=None, tags=None,
                 source="", obj_id=None):
        self.id = obj_id or _new_id()
        self.name = name
        self.data = np.asarray(data)
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

    def update_from(self, data, axis_names=None) -> None:
        self.data = np.asarray(data)
        if axis_names:
            self.axis_names = list(axis_names)
        elif len(self.axis_names) != self.data.ndim:
            self.axis_names = [f"axis_{i}" for i in range(self.data.ndim)]
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
            "array_file": "",
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ImageObject":
        shape = tuple(d.get("shape", ()))
        stub = np.zeros(shape, dtype=d.get("dtype", "float64"))
        obj = cls(d["name"], stub, d.get("axis_names"), d.get("axis_units"),
                  d.get("tags"), d.get("source", ""), d.get("id"))
        obj._dirty = False
        return obj


class ImageRef:
    """A pointer from a subplot to an :class:`ImageObject` + its display state."""

    _FIELDS = ("data_id", "display_axes", "index", "slice_axis", "cmap", "vmin", "vmax",
               "interpolation", "origin", "aspect", "alpha", "colorbar")

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


class SubplotModel:
    """Configuration for one panel / subplot within a sheet."""

    _FIELDS = ("title", "x_label", "y_label", "x_scale", "y_scale", "show_grid",
               "grid_axis", "grid_ticks", "grid_style", "grid_width", "grid_color",
               "grid_alpha", "aspect", "aspect_ratio",
               "x_min", "x_max", "y_min", "y_max",
               "tick_label_size", "face_color", "face_alpha",
               "spine_color", "spine_width", "spine_style",
               "title_fontsize", "xlabel_fontsize", "ylabel_fontsize",
               "legend_visible", "legend_loc", "legend_frame", "legend_fontsize",
               "legend_ncol")

    def __init__(self, title="Plot"):
        self.title = title
        self.x_label = ""
        self.y_label = ""
        self.x_scale = "linear"        # linear | log
        self.y_scale = "linear"
        self.show_grid = True
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
        self.image: "ImageRef | None" = None

        # Axis limits (None -> autoscale)
        self.x_min = None
        self.x_max = None
        self.y_min = None
        self.y_max = None

        # Cosmetics
        self.tick_label_size = 9.0
        self.face_color = "#ffffff"    # axes background
        self.face_alpha = 1.0
        self.spine_color = "#000000"
        self.spine_width = 0.8
        self.spine_style = "-"
        self.title_fontsize = 11.0
        self.xlabel_fontsize = 10.0
        self.ylabel_fontsize = 10.0

        # Legend
        self.legend_visible = True
        self.legend_loc = "best"
        self.legend_frame = True
        self.legend_fontsize = 8.0
        self.legend_ncol = 1

    def to_dict(self) -> dict:
        d = {f: getattr(self, f) for f in self._FIELDS}
        d["traces"] = [t.to_dict() for t in self.traces]
        d["image"] = self.image.to_dict() if self.image is not None else None
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "SubplotModel":
        s = cls(d.get("title", "Plot"))
        for f in cls._FIELDS:
            if f in d and d[f] is not None:
                setattr(s, f, d[f])
        s.traces = [TraceRef.from_dict(t) for t in d.get("traces", [])]
        img = d.get("image")
        s.image = ImageRef.from_dict(img) if img else None
        return s


class SheetModel:
    """A tab holding an NxM grid of subplots."""

    _FIG_FIELDS = ("fig_face_color", "fig_face_alpha", "fig_frame_on",
                   "fig_edge_color", "fig_edge_width", "fig_edge_style",
                   "fig_width_cm", "fig_height_cm")

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


class ProjectModel:
    """Serialization root: everything needed to restore a session."""

    def __init__(self):
        self.version = PROJECT_VERSION
        self.data_objects: list[DataObject] = []
        self.images: list[ImageObject] = []
        self.sheets: list[SheetModel] = []
        self.tree: list[dict] = []          # folder/sheet organization (raw nodes)
        self.colormaps: list[ColorMap] = []  # project-wide colormap library
        self.active_sheet_id = ""

    def to_dict(self) -> dict:
        return {
            "version": PROJECT_VERSION,
            "active_sheet_id": self.active_sheet_id,
            "data_objects": [d.to_dict() for d in self.data_objects],
            "images": [im.to_dict() for im in self.images],
            "sheets": [s.to_dict() for s in self.sheets],
            "tree": self.tree,
            "colormaps": [c.to_dict() for c in self.colormaps],
        }

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

    def save(self, path) -> None:
        """Write the project JSON to ``path`` and each image array to ``<stem>/``.

        Sidecar files are named after the image (``<name>.npy``, see
        :func:`resolve_sidecar_names`). Any ``*.npy`` in the folder that no current
        image owns -- left behind by a delete or a rename -- is swept, and an
        emptied folder is removed.
        """
        path = Path(path)
        payload = self.to_dict()
        assets = self._assets_dir(path)
        names = resolve_sidecar_names(self.images)
        if self.images:
            assets.mkdir(parents=True, exist_ok=True)
            by_id = {im.id: im for im in self.images}
            for entry in payload["images"]:
                obj = by_id[entry["id"]]
                fname = names[obj.id]
                target = assets / fname
                if obj._dirty or not target.exists():
                    np.save(target, obj.data)
                    obj._dirty = False
                entry["array_file"] = f"{assets.name}/{fname}"
        self._sweep_assets(assets, set(names.values()))
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)

    @staticmethod
    def _sweep_assets(assets: Path, keep: set) -> None:
        """Delete every ``*.npy`` in ``assets`` not in ``keep``; drop the folder if empty."""
        if not assets.is_dir():
            return
        for stale in assets.glob("*.npy"):
            if stale.name not in keep:
                try:
                    stale.unlink()
                except OSError:
                    pass
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
        raw_images = {x["id"]: x for x in raw.get("images", [])}
        for img in project.images:
            ref = raw_images.get(img.id, {}).get("array_file")
            if ref:
                arr_path = (path.parent / ref)
                if arr_path.is_file():
                    img.data = np.load(arr_path, allow_pickle=False)
                    img._dirty = False
        return project
