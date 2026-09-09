# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

NoorSuite (product name **NOORSUITE**; class names still say `SciSuite*`) is a PyQt6 +
matplotlib desktop tool for organizing and re-plotting datasets from a research project. It
runs as **two cooperating processes**:

- **Jupyter side** (`NoorSuite.client.SciSuiteClient`) — exploratory: read data with pandas,
  `push_dataframe(...)` the frames worth keeping (each becomes one *data object*),
  `plot(...)` columns onto a sheet, and `list_data()` / `list_traces()` to see what is there.
  It never pulls data or styles back.
- **GUI side** (`python -m NoorSuite --gui`) — pick x/y columns from a data object, place them
  as traces on sheets/subplots, style them (right-hand inspector *or* double-click on the
  canvas), organize sheets into folders, save `.sciproj` projects, export.

They talk over a loopback TCP socket (default port 55555).

## Commands

```bash
pip install -e .                       # editable install (PyQt6, matplotlib, numpy, pandas)
python -m NoorSuite --gui 55555         # launch the GUI (needs a display)
pytest                                 # run the test suite
pytest tests/test_model.py::test_project_roundtrip_with_tree_and_colormaps   # a single test
QT_QPA_PLATFORM=offscreen python -c "import NoorSuite.app"      # import GUI code headless
```

The pure-logic tests (`test_model`, `test_transforms`, `test_client`, `test_protocol`) do not
import PyQt6 and run in any env with numpy + pandas + pytest. GUI code needs a PyQt6 env; on
this machine the anaconda `developmentstuff` env has it, `base` has pytest (see the
`python-envs` memory).

`noorgraph.ipynb` is the manual integration harness. `%autoreload` reloads the *notebook*
process only — after changing `app.py` / `sheet.py` / `dialogs.py` / model code, restart the
GUI (`SciSuiteClient().launch()` reconnects to a running one or starts a fresh process).

## Architecture

Module split under `NoorSuite/` (was one file `scisuite.py`, now a compat shim):

| Module | Role |
| --- | --- |
| `model.py` | `DataObject`, `ImageObject`, `TraceRef`, `ImageRef`, `SubplotModel`, `SheetModel`, `ColorMap`, `ProjectModel` (+ `ProjectModel.save/load` for the image sidecar); `apply_y_transform`, `apply_numeric_expr`; option vocabularies. Needs only numpy. |
| `colormap.py` | Colormap sampling + `.scicmap` file I/O (needs matplotlib): `sample(spec, n)`, `available_specs`, `resolve_spec`, `save_file`/`load_file`, `BUILTIN_NAMES`. |
| `fuzzy.py` | `fuzzy_match` (case-insensitive subsequence) + `fuzzy_score`. Dependency-free. |
| `widgets.py` | `CollapsibleSection` (header `QToolButton` that shows/hides a body widget; `toggled` signal). Shared by `app.py`, `sheet.py`, `dialogs.py`. |
| `protocol.py` | Qt-free wire protocol: `frame`/`read_frame` (8-byte big-endian length prefix + pickle), action-name constants, `IPCClient`. |
| `ipc.py` | `IPCBridge` — the server `QObject` (needs PyQt6). Re-exports everything from `protocol.py`. |
| `client.py` | `SciSuiteClient` — DataFrame-first Jupyter API. |
| `sheet.py` | `PlotSheet` widget + the rendering engine + canvas hit-testing + the active-subplot highlight. |
| `dialogs.py` | `TraceStyleWidget` / `AxesStyleWidget` / `BulkTraceEditWidget` / `ColormapPanel` / `ImageStyleWidget` + the scrollable double-click editor dialogs. |
| `app.py` | `SciSuiteWindow` (alias `ModernOriginSuite`) + `ColumnTree` + `ProjectTree` + `ReorderList` + `ImageAxesPanel` — panels, tabs, inspector, save/load, IPC intake. |
| `__main__.py` | `python -m NoorSuite --gui <port>` entry. |

### The data model (v6)

- A **`DataObject`** is a named bag of columns — one pushed DataFrame. Its columns are the
  addressable unit; the object name is a stable pointer (`update_from` refreshes columns in
  place across re-pushes).
- An **`ImageObject`** is a named ND `np.ndarray` + `axis_names`. `slice(display_axes, index)`
  returns the 2-D slice to `imshow`. Lives in `SciSuiteWindow.images` (separate from
  `repository`); its array persists to a sidecar `.npy`, not inline (see below).
- A **`TraceRef`** on a `SubplotModel` points at `(data_id, x_col, y_col)` + its own style +
  an `enabled` flag. An **`ImageRef`** (`SubplotModel.image`, or `None`) points at an
  `ImageObject` + `display_axes` [row, col] / `slice_axis` (which non-display axis the slider
  drives; `None` = auto) / `index` (per-axis slice position) / cmap / vmin-vmax /
  interpolation / origin / aspect (`"equal"` by default) / alpha / colorbar. The image draws
  at `zorder=0` with `extent=[0,ncols,0,nrows]`, so `TraceRef`s overlay on top in the same
  coords.
- **Auto axis labels.** While a subplot's own `x_label` / `y_label` is blank, `render` fills
  it in (an explicit label always wins): from the image's `axis_names` for the two
  `display_axes` (so labels follow the Row/Col/Slice dropdowns), else from the plotted
  columns via `PlotSheet._auto_trace_labels` — the shared x column name across the enabled
  traces (`"index"` for the row index; blank if they disagree) and, only when a single trace
  is shown, its y column name.
- `x_col == "" or "__index__"` (`model.INDEX_COL`) means "use the row index".
- A **`ColorMap`** is `{name, colors[hex], builtin}`. `SheetModel` owns `colormaps` (custom,
  per-sheet) + `active_colormap` (name; `""` = matplotlib prop-cycle); `ProjectModel.colormaps`
  is the project library. `colormap.available_specs` merges built-ins + library + sheet-custom;
  `sample(spec, n)` turns any of them into `n` hex colours. New traces draw from
  `active_colormap` if set (`SciSuiteWindow._next_trace_color`), else `_cycle_color`.
- `SheetModel.tags` feed the fuzzy search; subplots are drawn in `SheetModel.subplots` list
  order into grid cells 1..N, so reordering that list moves the tiles.
- `repository` in `SciSuiteWindow` is `dict[data_id -> DataObject]`. `self.sheets` is
  `dict[sheet_id -> SheetModel]` (master); `self.open_tabs` is the subset with an open tab;
  `self.colormaps` is the project library.

### Cross-cutting mechanisms

- **IPC threading.** `IPCBridge` accepts on a daemon thread. *Mutations* (`append_dataframe`,
  `append_trace`, `append_image`, `add_to_sheet`, `add_image_to_sheet`, `remove_data`, `clear`)
  are re-emitted as the `data_received` Qt signal and handled on the GUI thread by
  `SciSuiteWindow.handle_incoming_ipc`. *Queries* (`list_data`, `list_images`, `list_traces`,
  `get_data`) are answered from `IPCBridge.snapshot`. Two refresh methods: `_refresh_ipc_snapshot()`
  (cheap metadata — call after any change) and `_refresh_ipc_data()` (rebuilds
  `snapshot["data_full"]`, the full column values that back `client.get_data()` — call only on
  data-object add/update/remove/clear/load).

- **Jupyter round-trip.** `client.get_data(name)` → DataFrame from `data_full`;
  `client.get_image(name)` → ndarray from `image_full` (both rebuilt by `_refresh_ipc_data`).
  Edit and `push_dataframe(df, name=name, mode="update")` / `push_image(arr, name=name,
  mode="update")` → `update_from` refreshes the object in place (id preserved), open sheets
  re-render, the `ColumnTree` / data pool rebuild. `list_data` / `list_images` / `list_traces`
  are the metadata listings.

- **Rendering is full clear-and-rebuild.** `PlotSheet.render(repository)` does `fig.clf()`,
  applies the figure frame (`fig.patch` edge/width/style, only visible with `fig_frame_on` and
  width > 0), sets each subplot's spines straight from the `SubplotModel` (hidden when
  `spine_width <= 0`), and re-adds every subplot. It repopulates `self.artist_map` (`Artist ->
  TraceRef`) + `self._text_targets` / legend handles so `on_canvas_click` hit-tests
  `event.dblclick` (`_hit_test`) and emits `element_double_clicked` with a `{"kind": ...}`
  dict; `SciSuiteWindow.open_element_editor` maps that to a dialog.

- **Active-subplot cue is a figure-level rectangle, not spine styling.** A `draw_event`
  handler (`PlotSheet._on_draw`, reentrancy-guarded) places `self._highlight_patch` — a dashed
  `Rectangle` around the active axes' `get_tightbbox` (which includes the title and labels),
  in `transFigure` coords. Never call it from `render()` (no renderer yet); `render()` just
  nulls the patch and lets the next draw rebuild it.

- **Image slider bar.** `PlotSheet`'s vertical `QSplitter` is 3 panes: plot → `slider_bar`
  → Notes. `slider_bar` (shown only when the active subplot's image has `ndim > 2`) has
  **Row (y)** + **Col (x)** combos (all axes) that set `ImageRef.display_axes`, a **Slice**
  combo (the non-display axes) that sets `ImageRef.slice_axis`, and the `QSlider` that writes
  `ImageRef.index[slice_axis]`. Changing the slice axis re-derives `display_axes` (for a 3-D
  image → the other two, ascending); changing row/col re-derives the slice axis. Every change
  re-renders; `_rebuild_slider_bar()` runs at the end of `render()` and on active-subplot
  change (guarded by `_img_loading`).

- **One source of truth for style widgets.** `TraceStyleWidget` (`TraceRef`),
  `AxesStyleWidget` (`SubplotModel`) and `ImageStyleWidget` (`sub.image`) live in `dialogs.py`,
  embedded both in the right-hand inspector and the pop-up dialogs. `AxesStyleWidget`'s groups
  are `CollapsibleSection`s (Labels & scale / Axis limits open; Cosmetics / Legend / Grid
  folded; Image auto-hidden when `sub.image is None`). Dialogs (`_BaseEditDialog`) wrap
  content in a capped-height `QScrollArea` with the button box outside it.

- **`ProjectModel` is the only serialization root** — `.sciproj` files *and* the
  `~/.scisuite_session.json` autosave. It carries `data_objects`, `images`, `sheets`
  (subplots + `TraceRef`s + `image` + figure-frame + grid + `tags` + `notes` + per-sheet
  `colormaps`/`active_colormap`), `tree`, and `colormaps` (library). Format is **v6 only**;
  `from_dict` raises `ValueError` on any other `version`. Save via `ProjectModel.save(path)` /
  load via `ProjectModel.load(path)` — image arrays go to a **sidecar folder**
  `Path(path).with_suffix("")` (`report.sciproj` → `report/`), one `.npy` per image **named
  after the image** (`model.resolve_sidecar_names` — `<name>.npy`, sanitized; a short `_<id>`
  is appended only to break a name collision), linked from the JSON by `array_file`;
  `to_json`/`from_json` alone keep metadata but drop the arrays. The folder is fully managed:
  `save()` and `SciSuiteWindow._sync_image_sidecars` (run by `_remove_image` / `_clear_all`)
  both call `ProjectModel._sweep_assets`, which deletes every `*.npy` in the folder that no
  current image owns — left by a delete or a rename, including legacy `img_<id>.npy` — and
  removes the folder once empty. `testproject.sciproj` (+ `testproject/`) is a regenerable v6
  sample.

- **Project file identity & autosave.** `SciSuiteWindow.project_path` is set by *Save As…* /
  *Open* only (not by the session autosave). The toolbar `project_label` + window title show
  `_project_name()` (`Path(project_path).stem` or "Untitled project"). Ctrl+S → `_quick_save`
  (writes `project_path`, or falls back to Save As); a `QTimer` (`_AUTOSAVE_SECONDS`) calls
  `_autosave_project` which re-writes `project_path` if set. `closeEvent` does one last
  autosave.

### UI layout (`app.py`)

Left is a vertical splitter: **data pool** list (`DataPoolList` — DataObjects + ImageObjects,
different icons; `ExtendedSelection` — Ctrl/Shift click to multi-pick, Delete or the
"Delete N selected" context action removes them all via `_delete_selected_data`)
→ **middle `QStackedWidget`**: page 0 = **column picker** (`ColumnTree` X/Y ticks + `head()`
preview + "Add to active/new sheet"; valid X+Y is draggable, MIME
`application/x-scisuite-cols`), page 1 = **`ImageAxesPanel`** (row/col axis combos + add
buttons) — `_on_data_selected` picks the page by object type → **project tree**
(`ProjectTree`: folders + sheets, **single-click** to open a tab, right-click "Set tags…",
accepts column drops; also `ExtendedSelection` — multi-pick folders/sheets, Delete or
"Delete N selected" → `_delete_selected_tree_items`, which folds a selected child into its
selected ancestor folder and confirms once; `_on_activate` won't switch tabs while >1 is
selected). Center: one tab per open sheet, each a `QSplitter(Vertical)` — plot →
image `slider_bar` → collapsible **Notes** pane (`current_sheet()` still returns the
`PlotSheet`). Right, top→bottom: **rows/cols spinboxes**
(`on_grid_changed`), the **subplot-order strip** (`ReorderList`, drag to reorder
`SheetModel.subplots`; its multi-selection is the colormap "Selected subplots" scope), the
active subplot's `TraceRef` list (`ExtendedSelection`; checkbox = `enabled`; >1 selected swaps
`TraceStyleWidget` for `BulkTraceEditWidget`), the **`ColormapPanel`**, and the Axes/Trace
inspectors. Fuzzy search (`fuzzy.fuzzy_match`) filters the data pool and the tree; the tree
haystack per sheet is `_sheet_haystack` (name + tags + notes + referenced data-object /
image names + every subplot title/x_label/y_label + trace labels).

## Conventions / gotchas

- `DataObject.id` / `SheetModel.sheet_id` are 8-char uuid4 slices (`model._new_id`). Data
  objects are matched by **name** for `push_dataframe(mode="update")`, `plot(name, ...)`,
  `remove_data` (id also accepted).
- Deleting a data object removes every `TraceRef` that references it (GUI asks first).
- Port 55555 already bound is **not an error** — a GUI is already running; the client
  reconnects.
- Adding a style/model field: give it a default, add it to the model's `_FIELDS` /
  `_STYLE_FIELDS` / `_FIG_FIELDS` tuple (drives (de)serialization *and* the dialog snapshot),
  and add a control to the matching `*StyleWidget` (or dialog).
- `apply_y_transform` matches `"1e-3"` before `"1e3"` on purpose — order matters there.
- Branding lives in `app.py`: `APP_NAME` / `APP_ID` / `icon_path()` / `app_icon()`; the logo
  is `NoorSuite/NOORSUITE_ICON.jpg` (copy also at repo root). `__main__.main` sets the Windows
  AppUserModelID so the taskbar shows our icon.
- Two search boxes: `search_bar` (data pool → `_filter_data_pool`) and `tree_search`
  (project tree → `_filter_project_tree`, fuzzy over `_sheet_haystack`).
- Right-panel sections are `CollapsibleSection`s (a checkable `QToolButton` header hiding a
  body widget); the whole right panel is inside a `QScrollArea`.
- `apply_numeric_expr(current, "-0.5")` means *subtract 0.5*, not "set to -0.5" — the
  bulk-edit numeric fields are transform-or-set, and negative absolute widths/sizes are
  meaningless anyway.
- `ColormapPanel` talks to the window through a small host protocol
  (`active_sheet_model`, `available_colormap_names`, `resolve_colormap`, `sample_colors`,
  `selected_subplots`, `copy_colormap_to_library`, `export_colormap`, `import_colormap`) —
  keep those methods on `SciSuiteWindow` in sync if you extend the panel.
- Only a sheet's **own** custom colormaps can be deleted from the panel; built-ins and library
  entries are read-only there.
- Closing a sheet's tab does not delete the sheet; it stays in the tree (`self.sheets`).
