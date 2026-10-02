"""Insert data into a NoorSuite project *without* a running GUI.

:class:`OfflineBackend` stands in for :class:`~NoorSuite.protocol.IPCClient`: it takes the
same payloads the GUI would receive and applies them straight to a project file
(``.sciproj``, or the GUI's crash-recovery session file by default). The whole
:class:`~NoorSuite.client.SciSuiteClient` API therefore works unchanged::

    from NoorSuite import connect
    suite = connect()                       # GUI running -> live; otherwise -> the session file
    suite.push_dataframe(df, name="IV")
    suite.plot("IV", x="t", y=["I", "V"], new_sheet=True)

Qt-free (needs numpy + pandas + matplotlib only for colour sampling, which falls back).
The GUI loads the session file on start, so data inserted offline shows up the next time
it opens; with an explicit ``project=`` path, open that file in the GUI instead.
"""
from __future__ import annotations

import os

import numpy as np

from .model import (INDEX_COL, DataObject, ImageObject, ImageRef, ProjectModel,
                    SheetModel, TraceRef, append_notes, apply_sheet_annotations,
                    merge_tags, split_folder_path, tree_move_sheet)
from .protocol import (ACTION_ADD_IMAGE_TO_SHEET, ACTION_ADD_TO_SHEET,
                       ACTION_APPEND_DATAFRAME, ACTION_APPEND_IMAGE,
                       ACTION_APPEND_TRACE, ACTION_CLEAR, ACTION_GET_DATA,
                       ACTION_GET_IMAGE, ACTION_LIST_DATA, ACTION_LIST_IMAGES,
                       ACTION_LIST_SHEETS, ACTION_LIST_TRACES, ACTION_ORGANIZE,
                       ACTION_PING,
                       ACTION_REMOVE_DATA, ACTION_REMOVE_TRACE)

SESSION_FILE = os.path.expanduser("~/.scisuite_session.json")


def _cycle_color(n: int) -> str:
    try:
        from matplotlib import rcParams
        colors = rcParams["axes.prop_cycle"].by_key().get("color", ["#1f77b4"])
    except Exception:
        colors = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd"]
    return colors[n % len(colors)]


class OfflineBackend:
    """File-backed implementation of the IPC request/response interface."""

    def __init__(self, path=None):
        self.path = os.path.abspath(os.path.expanduser(path)) if path else SESSION_FILE
        self.project = self._load()

    # ------------------------------------------------------------------ persistence
    def _load(self) -> ProjectModel:
        if os.path.exists(self.path):
            return ProjectModel.load(self.path)
        return ProjectModel()

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self.project.save(self.path)

    # ----------------------------------------------------------- IPCClient interface
    def is_alive(self) -> bool:
        return True

    def send(self, payload: dict, timeout: float = 5.0):
        action = payload.get("action")
        handler = {
            ACTION_PING: lambda p: {"status": "alive"},
            ACTION_LIST_DATA: self._list_data,
            ACTION_LIST_IMAGES: self._list_images,
            ACTION_LIST_TRACES: self._list_traces,
            ACTION_LIST_SHEETS: self._list_sheets,
            ACTION_GET_DATA: self._get_data,
            ACTION_GET_IMAGE: self._get_image,
        }.get(action)
        if handler is not None:            # queries: re-read so we see other writers
            if action != ACTION_PING:
                self.project = self._load()
            return handler(payload)
        mutate = {
            ACTION_APPEND_DATAFRAME: self._append_dataframe,
            ACTION_APPEND_TRACE: self._append_trace,
            ACTION_APPEND_IMAGE: self._append_image,
            ACTION_ADD_TO_SHEET: self._add_to_sheet,
            ACTION_ADD_IMAGE_TO_SHEET: self._add_image_to_sheet,
            ACTION_REMOVE_DATA: self._remove,
            ACTION_REMOVE_TRACE: self._remove,
            ACTION_CLEAR: self._clear,
            ACTION_ORGANIZE: self._organize,
        }.get(action)
        if mutate is None:
            return {"status": "error", "message": f"unknown action {action!r}"}
        self.project = self._load()        # read-modify-write against the file's latest state
        result = mutate(payload)
        self.save()
        return result if result is not None else {"status": "success"}

    # --------------------------------------------------------------------- lookups
    def _find_data(self, key):
        return next((o for o in self.project.data_objects if key in (o.id, o.name)), None)

    def _find_image(self, key):
        return next((o for o in self.project.images if key in (o.id, o.name)), None)

    def _find_sheet(self, target):
        by_id = next((s for s in self.project.sheets if s.sheet_id == target), None)
        if by_id is not None:
            return by_id
        return next((s for s in self.project.sheets if s.name == target), None)

    def _new_sheet(self, name=None) -> SheetModel:
        sm = SheetModel(name=name or f"Sheet {len(self.project.sheets) + 1}")
        self.project.sheets.append(sm)
        self.project.tree.append({"type": "sheet", "sheet_id": sm.sheet_id})
        if not self.project.active_sheet_id:
            self.project.active_sheet_id = sm.sheet_id
        return sm

    def _resolve_sheet(self, target) -> SheetModel:
        if target == "__new__":
            return self._new_sheet()
        if target in ("__active__", "", None):
            sm = self._find_sheet(self.project.active_sheet_id)
            return sm or (self.project.sheets[-1] if self.project.sheets else self._new_sheet())
        return self._find_sheet(target) or self._new_sheet(
            target if isinstance(target, str) else None)

    def _trace_color(self, sm: SheetModel, sub) -> str:
        i = len(sub.traces)
        if sm.active_colormap:
            try:
                from .colormap import sample
                spec = next((c for c in list(sm.colormaps) + list(self.project.colormaps)
                             if c.name == sm.active_colormap), sm.active_colormap)
                return sample(spec, i + 1)[-1]
            except Exception:
                pass
        return _cycle_color(i)

    # ------------------------------------------------------------------- mutations
    def _append_dataframe(self, p):
        name = p.get("name", "data")
        columns = p.get("columns", {})
        order = p.get("column_order") or list(columns)
        existing = self._find_data(name) if p.get("mode") == "update" else None
        if existing is not None:
            existing.update_from(columns, p.get("units"), order)
            if p.get("notes"):
                existing.notes = append_notes(existing.notes, p["notes"])
            existing.tags = merge_tags(existing.tags, p.get("tags"))
            return {"status": "success", "id": existing.id}
        obj = DataObject(name, columns, order, p.get("units"), p.get("tags"),
                         p.get("source", "dataframe"), notes=p.get("notes", ""))
        self.project.data_objects.append(obj)
        return {"status": "success", "id": obj.id}

    def _append_trace(self, p):
        name = p["name"]
        self._append_dataframe({
            "name": name, "mode": "new",
            "columns": {"x": p["x"], name: p["y"]}, "column_order": ["x", name],
            "units": {"x": p.get("x_unit", ""), name: p.get("y_unit", "")},
            "tags": p.get("tags", []), "source": "push_trace"})
        obj = self._find_data(name)
        sm = self._resolve_sheet("__active__")
        sub = sm.get_active_subplot()
        ref = TraceRef(obj.id, "x", name)
        ref.color = p.get("color") or self._trace_color(sm, sub)
        ref.plot_type = p.get("plot_type", "Line")
        sub.traces.append(ref)

    def _append_image(self, p):
        arr = np.frombuffer(p["bytes"], dtype=p["dtype"]).reshape(p["shape"]).copy()
        existing = self._find_image(p["name"]) if p.get("mode") == "update" else None
        if existing is not None:
            existing.update_from(arr, p.get("axis_names"))
            return
        self.project.images.append(ImageObject(
            p["name"], arr, p.get("axis_names"), p.get("axis_units"), p.get("tags"),
            p.get("source", "array")))

    def _add_to_sheet(self, p):
        key = p.get("data_name") or p.get("data_id")
        obj = self._find_data(key)
        if obj is None:
            return {"status": "error", "message": f"no data object named/id {key!r}"}
        sm = self._resolve_sheet(p.get("sheet", "__active__"))
        if p.get("organize"):
            self._apply_organize(sm, p["organize"])
        sub = sm.subplots[min(int(p.get("subplot_index", 0)), len(sm.subplots) - 1)]
        x_col = p.get("x_col", INDEX_COL) or INDEX_COL
        missing = [c for c in p.get("y_cols", []) if c not in obj.columns]
        for y_col in p.get("y_cols", []):
            if y_col in obj.columns:
                ref = TraceRef(obj.id, x_col, y_col)
                ref.color = self._trace_color(sm, sub)
                ref.apply_style(p.get("trace_style") or {})   # explicit options win
                sub.traces.append(ref)
        if missing:
            return {"status": "error", "message": f"columns not found in {obj.name!r}: {missing}"}

    def _add_image_to_sheet(self, p):
        obj = self._find_image(p.get("name") or p.get("id"))
        if obj is None:
            return {"status": "error", "message": f"no image named {p.get('name')!r}"}
        sm = self._resolve_sheet(p.get("sheet", "__active__"))
        sub = sm.subplots[min(int(p.get("subplot_index", 0)), len(sm.subplots) - 1)]
        r, c = p.get("display_axes") or [max(0, obj.ndim - 2), max(0, obj.ndim - 1)]
        index = {a: obj.shape[a] // 2 for a in range(obj.ndim) if a not in (r, c)}
        sub.image = ImageRef(obj.id, [int(r), int(c)], index)

    def _apply_organize(self, sm: SheetModel, org: dict) -> None:
        apply_sheet_annotations(sm, org)
        if org.get("folder"):
            self.project.tree = tree_move_sheet(self.project.tree, sm.sheet_id,
                                                split_folder_path(org["folder"]))

    def _organize(self, p):
        if p.get("kind") == "data":
            obj = self._find_data(p.get("target")) or self._find_image(p.get("target"))
            if obj is None:
                return {"status": "error", "message": f"no data object matching {p.get('target')!r}"}
            obj.tags = merge_tags(obj.tags, p.get("tags"))
            if p.get("notes") and hasattr(obj, "notes"):
                obj.notes = p["notes"] if p.get("replace_notes") else append_notes(obj.notes, p["notes"])
            return
        sm = self._find_sheet(p.get("target"))
        if sm is None:
            return {"status": "error", "message": f"no sheet matching {p.get('target')!r}"}
        self._apply_organize(sm, p)

    def _remove(self, p):
        key = p.get("key")
        img = self._find_image(key)
        if img is not None:
            self.project.images.remove(img)
            for sm in self.project.sheets:
                for sub in sm.subplots:
                    if sub.image is not None and sub.image.data_id == img.id:
                        sub.image = None
            return
        obj = self._find_data(key)
        if obj is None:
            return {"status": "error", "message": f"no data object matching {key!r}"}
        self.project.data_objects.remove(obj)
        for sm in self.project.sheets:
            for sub in sm.subplots:
                sub.traces = [t for t in sub.traces if t.data_id != obj.id]

    def _clear(self, p):
        self.project.data_objects.clear()
        self.project.images.clear()
        for sm in self.project.sheets:
            for sub in sm.subplots:
                sub.traces = []
                sub.image = None

    # --------------------------------------------------------------------- queries
    def _list_data(self, p):
        return {"status": "success", "data": [{
            "id": o.id, "name": o.name, "columns": list(o.column_order),
            "nrows": o.nrows, "tags": list(o.tags), "source": o.source,
        } for o in self.project.data_objects]}

    def _list_images(self, p):
        return {"status": "success", "images": [{
            "id": im.id, "name": im.name, "shape": list(im.shape),
            "dtype": str(im.data.dtype), "axis_names": list(im.axis_names),
            "tags": list(im.tags), "source": im.source,
        } for im in self.project.images]}

    def _list_traces(self, p):
        by_id = {o.id: o for o in self.project.data_objects}
        traces = []
        for sm in self.project.sheets:
            for i, sub in enumerate(sm.subplots):
                for t in sub.traces:
                    obj = by_id.get(t.data_id)
                    traces.append({
                        "data_id": t.data_id, "data_name": obj.name if obj else "",
                        "y_col": t.y_col, "x_col": t.x_col, "sheet": sm.name,
                        "subplot": i, "enabled": t.enabled, "plot_type": t.plot_type,
                        "color": t.color})
        return {"status": "success", "traces": traces}

    def _list_sheets(self, p):
        counts: dict = {}
        for sm in self.project.sheets:
            counts[sm.name] = counts.get(sm.name, 0) + 1
        folders: dict = {}

        def walk(nodes, path):
            for n in nodes:
                if n.get("type") == "sheet":
                    folders[n.get("sheet_id")] = "/".join(path)
                else:
                    walk(n.get("children", []), path + [n.get("name", "")])
        walk(self.project.tree, [])
        return {"status": "success", "sheets": [{
            "id": sm.sheet_id, "name": sm.name, "rows": sm.rows, "cols": sm.cols,
            "subplots": len(sm.subplots), "tags": list(sm.tags),
            "duplicate_name": counts[sm.name] > 1,
            "folder": folders.get(sm.sheet_id, "")} for sm in self.project.sheets]}

    def _get_data(self, p):
        obj = self._find_data(p.get("key"))
        if obj is None:
            return {"status": "error", "message": f"no data object matching {p.get('key')!r}"}
        return {"status": "success", "id": obj.id, "name": obj.name,
                "column_order": list(obj.column_order),
                "columns": {c: obj.columns[c].tolist() for c in obj.column_order}}

    def _get_image(self, p):
        im = self._find_image(p.get("key"))
        if im is None:
            return {"status": "error", "message": f"no image matching {p.get('key')!r}"}
        arr = np.ascontiguousarray(im.data)
        return {"status": "success", "id": im.id, "name": im.name,
                "bytes": arr.tobytes(), "shape": list(arr.shape), "dtype": str(arr.dtype)}
