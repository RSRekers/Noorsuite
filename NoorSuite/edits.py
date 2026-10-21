"""Edits of an existing project that the GUI, the offline backend and the CLI share (Qt-free).

Every function works on plain models and returns a short message on success; a wrong request raises
``ValueError`` (the client validates with :func:`check_edit` before anything is sent, so a typo fails in
the caller, not silently in the GUI).
"""
from __future__ import annotations

import numpy as np

from .model import (CUSTOM_FACTOR_PREFIX, ERR_COLUMN_ATTRS, ERROR_MODES, LINE_STYLES, MARKERS,
                    PLOT_TYPES, REL_INDEX_PREFIX, REL_VALUE_PREFIX, SPLIT_LAYOUTS, Y_TRANSFORMS,
                    ColorMap, DataObject, TraceRef, _truthy, aggregate_series, common_label,
                    split_into_repeats)

TRACE_FIELDS = TraceRef._STYLE_FIELDS + ERR_COLUMN_ATTRS + ("label", "enabled")
TRACE_ALIASES = {"linewidth": "line_width", "lw": "line_width", "linestyle": "line_style",
                 "ls": "line_style", "markersize": "marker_size", "ms": "marker_size",
                 "type": "plot_type", "visible": "enabled", "normalize": "scale_factor",
                 "errorbars": "show_errorbar", "band": "show_band", "sort": "sort_x"}
OPS = ("trace_style", "remove_trace", "new_sheet", "duplicate_sheet", "folder", "colormap",
       "save_project", "open_project", "move_trace", "combine_traces")
SPREAD_DISPLAYS = ("band", "bars", "both")


def check_trace_style(style: dict) -> dict:
    """Validated / coerced trace style (``TraceRef`` field names or the aliases above)."""
    ref = TraceRef("", "", "")
    out = {}
    for key, val in dict(style or {}).items():
        if val is None:
            continue
        name = TRACE_ALIASES.get(key, key)
        if name not in TRACE_FIELDS:
            raise ValueError(f"unknown trace option {key!r}; valid: "
                             + ", ".join(sorted(set(TRACE_FIELDS) | set(TRACE_ALIASES))))
        default = getattr(ref, name, "")
        try:
            if isinstance(default, bool):
                out[name] = _truthy(val)
            elif isinstance(default, (int, float)):
                out[name] = float(val)
            else:
                out[name] = str(val)
        except (TypeError, ValueError):
            raise ValueError(f"bad value {val!r} for trace option {key!r}") from None
    if "plot_type" in out and out["plot_type"] not in PLOT_TYPES:
        raise ValueError(f"plot_type must be one of {PLOT_TYPES}")
    if "line_style" in out and out["line_style"] not in LINE_STYLES:
        raise ValueError(f"line_style must be one of {LINE_STYLES}")
    if "marker" in out and out["marker"] not in MARKERS:
        raise ValueError(f"marker must be one of {MARKERS}")
    if "scale_factor" in out:
        sf = out["scale_factor"]
        if sf not in Y_TRANSFORMS and not sf.startswith((CUSTOM_FACTOR_PREFIX, REL_VALUE_PREFIX,
                                                         REL_INDEX_PREFIX)):
            try:                                                   # a plain number = a multiplier
                out["scale_factor"] = f"{CUSTOM_FACTOR_PREFIX}{float(sf):g}"
            except ValueError:
                raise ValueError(f"scale_factor must be one of {Y_TRANSFORMS}, a number, "
                                 f"'{CUSTOM_FACTOR_PREFIX}<x>', '{REL_VALUE_PREFIX}pct:<ref>' or "
                                 f"'{REL_INDEX_PREFIX}pct:<n>'") from None
    return out


def select_traces(sm, subplot, trace):
    """``[(subplot, TraceRef)]`` picked by ``subplot`` (index, ``"all"``) and ``trace`` (index, label /
    y column text, or ``"all"``)."""
    subs = list(enumerate(sm.subplots)) if str(subplot) == "all" else [(int(subplot),
                                                                       sm.subplots[int(subplot)])]
    if isinstance(trace, (list, tuple)):                      # several selectors: their union, in order
        out = []
        for one in trace:
            out += [h for h in select_traces(sm, subplot, one) if all(h[1] is not o[1] for o in out)]
        return out
    out = []
    for _i, sub in subs:
        if str(trace) == "all":
            out += [(sub, t) for t in sub.traces]
        elif isinstance(trace, int) or str(trace).lstrip("-").isdigit():
            n = int(trace)
            if -len(sub.traces) <= n < len(sub.traces):
                out.append((sub, sub.traces[n]))
        else:
            out += [(sub, t) for t in sub.traces
                    if str(trace) in (t.label, t.y_col) or str(trace).lower() in t.display_label.lower()]
    return out


def edit_trace_style(sm, payload: dict) -> str:
    style = check_trace_style(payload.get("style"))
    hits = select_traces(sm, payload.get("subplot", 0), payload.get("trace", "all"))
    if not hits:
        raise ValueError("no trace matches " + repr(payload.get("trace")))
    for _sub, t in hits:
        enabled = style.get("enabled")
        t.apply_style({k: v for k, v in style.items() if k != "enabled"})
        if enabled is not None:
            t.enabled = enabled
    return f"restyled {len(hits)} trace(s)"


def edit_remove_trace(sm, payload: dict) -> str:
    hits = select_traces(sm, payload.get("subplot", 0), payload.get("trace", "all"))
    if not hits:
        raise ValueError("no trace matches " + repr(payload.get("trace")))
    for sub, t in hits:
        sub.traces = [x for x in sub.traces if x is not t]
    return f"removed {len(hits)} trace(s)"


def edit_move_trace(sm, payload: dict) -> str:
    """Move a trace to position ``to`` in its subplot's stack (drawn later = on top)."""
    hits = select_traces(sm, payload.get("subplot", 0), payload.get("trace", 0))
    if len(hits) != 1:
        raise ValueError("move_trace needs exactly one trace")
    sub, t = hits[0]
    sub.traces = [x for x in sub.traces if x is not t]
    to = int(payload.get("to", len(sub.traces)))
    sub.traces.insert(max(0, min(to, len(sub.traces))), t)
    return "moved"


def set_spread(ref: TraceRef, display: str = "band", mode: str = "std") -> None:
    """Error columns of a derived mean trace + how its spread is drawn (``display`` band / bars / both)."""
    ref.yerr_col, ref.yerr_sem_col = "std", "sem"
    ref.yerr_low_col, ref.yerr_high_col = "err_min", "err_max"
    ref.yerr_mode = mode
    ref.show_band = display in ("band", "both")
    ref.show_errorbar = display in ("bars", "both")


def derived_columns(x, agg: dict) -> dict:
    return {"x": np.asarray(x, dtype=float), "mean": agg["mean"], "std": agg["std"], "sem": agg["sem"],
            "err_min": agg["err_min"], "err_max": agg["err_max"]}


DERIVED_ORDER = ["x", "mean", "std", "sem", "err_min", "err_max"]


def combine_traces(sm, payload: dict, repository: dict):
    """The GUI's "Combine into mean +- error trace...": several traces of one subplot (``trace`` =
    list of selectors / ``"all"``) -> their row-wise mean with std / sem / min-max, or, with ``split=N``,
    ONE trace holding repeats (``layout`` block / sequence) -> one point per condition. The result is a
    derived :class:`DataObject` (NOT stored: the caller puts it into its repository) plus one new trace
    (``plot_type``, ``display``, ``mode``, ``label``) that replaces the originals unless
    ``remove_originals`` is false. Returns ``(DataObject, message)``."""
    display = payload.get("display", "band")
    mode = payload.get("mode", "std")
    plot_type = payload.get("plot_type") or "Line"
    if display not in SPREAD_DISPLAYS:
        raise ValueError(f"display must be one of {SPREAD_DISPLAYS}")
    if mode not in ERROR_MODES:
        raise ValueError(f"mode must be one of {ERROR_MODES}")
    if plot_type not in PLOT_TYPES:
        raise ValueError(f"plot_type must be one of {PLOT_TYPES}")
    sub_i = int(payload.get("subplot", 0))
    sub = sm.subplots[sub_i]
    refs = [t for _s, t in select_traces(sm, sub_i, payload.get("trace", "all"))]
    split = payload.get("split")
    resolved = []
    for ref in refs:
        data = repository.get(ref.data_id)
        if data is None or ref.y_col not in data.columns:
            raise ValueError(f"'{ref.display_label}' has no data to resolve")
        resolved.append(ref.resolve(data))
    if split:
        n = int(split)
        layout = payload.get("layout", "block")
        if layout not in SPLIT_LAYOUTS:
            raise ValueError(f"layout must be one of {SPLIT_LAYOUTS}")
        if len(refs) != 1:
            raise ValueError("split needs exactly one trace (the one that holds the repeats)")
        x, y = resolved[0]
        agg = split_into_repeats(y, n, layout=layout)
        x_axis = 0 if layout == "sequence" else 1
        x_new = np.asarray(x, dtype=float).reshape(-1, n).mean(axis=x_axis)
        label = payload.get("label") or refs[0].display_label
        obj = DataObject(f"{label} (n={n} mean±err)", derived_columns(x_new, agg), DERIVED_ORDER,
                         source="split")
    else:
        if len(refs) < 2:
            raise ValueError("combine needs at least two traces (or split=N for one trace of repeats)")
        x0 = resolved[0][0]
        for x, _y in resolved[1:]:
            if len(x) != len(x0) or not np.allclose(x, x0, equal_nan=True):
                raise ValueError("the traces do not share the same x values -- combining them "
                                 "row by row would not be meaningful")
        agg = aggregate_series([y for _x, y in resolved])
        label = payload.get("label") or common_label([r.display_label for r in refs])
        obj = DataObject(f"{label} (n={len(refs)} mean±err)", derived_columns(x0, agg),
                         DERIVED_ORDER, source="combined")
    new_ref = TraceRef(obj.id, "x", "mean", label=label)
    new_ref.plot_type = plot_type
    new_ref.color = refs[0].color
    set_spread(new_ref, display, mode)
    insert_at = min((sub.traces.index(r) for r in refs if r in sub.traces), default=len(sub.traces))
    if payload.get("remove_originals", True):
        for s in sm.subplots:
            s.traces = [t for t in s.traces if all(t is not r for r in refs)]
        sub.traces.insert(min(insert_at, len(sub.traces)), new_ref)
    else:
        sub.traces.append(new_ref)
    return obj, f"combined {len(refs)} trace(s) into {obj.name!r}"


def check_colormap(payload: dict) -> dict:
    act = payload.get("do")
    if act not in ("add", "remove", "apply"):
        raise ValueError("colormap action must be add, remove or apply")
    if not payload.get("name"):
        raise ValueError("a colormap needs a name")
    if act == "add":
        cols = [str(c) if str(c).startswith("#") else "#" + str(c) for c in payload.get("colors", [])]
        if len(cols) < 2 or any(len(c) not in (4, 7) for c in cols):
            raise ValueError("a colormap needs at least two colours like #1f77b4")
        payload = {**payload, "colors": cols}
    return payload


def edit_colormap(payload: dict, library: list, sm=None) -> str:
    """add / remove / apply (to ``sm``) a custom colormap: in the project library
    (``library=True`` default) or on the sheet only."""
    payload = check_colormap(payload)
    name, act = payload["name"], payload["do"]
    on_sheet = bool(payload.get("sheet_only"))
    if act == "add":
        cm = ColorMap(name, payload["colors"])
        target = sm.colormaps if on_sheet and sm is not None else library
        target[:] = [c for c in target if c.name != name] + [cm]
        return f"colormap {name!r} stored"
    if act == "remove":
        n0 = len(library) + (len(sm.colormaps) if sm is not None else 0)
        library[:] = [c for c in library if c.name != name]
        if sm is not None:
            sm.colormaps = [c for c in sm.colormaps if c.name != name]
        return "removed" if n0 else "nothing to remove"
    if sm is None:
        raise ValueError("applying a colormap needs a sheet")
    sm.active_colormap = name if name != "default" else ""
    return f"sheet uses {name!r} for new traces"


def check_edit(payload: dict) -> dict:
    """Validate an ``edit`` payload before it is sent."""
    op = payload.get("op")
    if op not in OPS:
        raise ValueError(f"unknown edit {op!r}; use one of {OPS}")
    if op == "trace_style":
        payload = {**payload, "style": check_trace_style(payload.get("style"))}
    elif op == "colormap":
        payload = check_colormap(payload)
    elif op == "combine_traces":
        if payload.get("display", "band") not in SPREAD_DISPLAYS:
            raise ValueError(f"display must be one of {SPREAD_DISPLAYS}")
        if payload.get("mode", "std") not in ERROR_MODES:
            raise ValueError(f"mode must be one of {ERROR_MODES}")
        if payload.get("split") is not None and int(payload["split"]) < 1:
            raise ValueError("split must be a positive number of repeats")
    elif op == "folder":
        if payload.get("do") not in ("create", "rename", "delete"):
            raise ValueError("folder action must be create, rename or delete")
        if not payload.get("path"):
            raise ValueError("a folder path is required")
        if payload.get("do") == "rename" and not payload.get("new_name"):
            raise ValueError("rename needs new_name")
    elif op in ("save_project", "open_project") and not payload.get("path"):
        raise ValueError("a path is required")
    return payload
