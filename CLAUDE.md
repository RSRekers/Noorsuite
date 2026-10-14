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
- **Data aspect ratio (non-image subplots).** `SubplotModel.aspect` — `"auto"` (default,
  matplotlib's normal autoscale), `"equal"` (1:1), or `"custom"` (`aspect_ratio`, y data-units
  displayed per x data-unit) — applied in `render` via `ax.set_aspect(..., adjustable="box")`
  right after the x/y scales. It's set *before* the image layer, so a subplot with an image
  ignores it: `ImageRef.aspect` (via `imshow`) is drawn later and wins there. UI: the
  "Cosmetics" section of `AxesStyleWidget`.
- **Figure size (shape + export), independent of any data/axes aspect.** `SheetModel.fig_width_cm`
  / `fig_height_cm` (`None`, `None` by default = auto) drive two things from one pair of
  numbers: (1) **on-screen shape** — `PlotSheet` wraps the canvas in `_AspectCanvasHost`, which
  fills its tab as before when either is blank, but when both are set keeps the canvas
  letterboxed (centered, capped to fit) at that width:height *ratio* on every resize —
  `render()` recomputes the ratio and calls `canvas_host.set_ratio(...)` each pass; the host's
  own `resizeEvent` reapplies it live. This is how to make a plot "look square" regardless of
  panel shape or which columns/scales are plotted, without touching `SubplotModel.aspect`
  (which is data units, not figure shape, and only applies to axes without an image). (2)
  **export size** — "Copy to clipboard" / "Export as SVG" (`_savefig_at_export_size`)
  temporarily `fig.set_size_inches(cm/2.54, ...)` at the *absolute* cm values and drop
  `bbox_inches="tight"` so the saved file is exactly that size, then restore the figure
  afterward. UI: the **"Figure"** inspector tab (`FigureStyleWidget`, bound to the active sheet
  by `sync_active_subplot_inspector`) — same widget class embedded in `FigureDialog` (double-
  click the figure background) — with "Make square" (mirrors whichever side is set, else
  defaults to 10 cm) / "Auto (fill panel)" buttons alongside the W/H fields.
- **Auto axis labels.** While a subplot's own `x_label` / `y_label` is blank, `render` fills
  it in (an explicit label always wins): from the image's `axis_names` for the two
  `display_axes` (so labels follow the Row/Col/Slice dropdowns), else from the plotted
  columns via `PlotSheet._auto_trace_labels` — the shared x column name across the enabled
  traces (`"index"` for the row index; blank if they disagree) and, only when a single trace
  is shown, its y column name.
- `x_col == "" or "__index__"` (`model.INDEX_COL`) means "use the row index".
- `TraceRef.scale_factor` (the "Y transform" combo) is one of `Y_TRANSFORMS` (`1x`, the fixed
  decades, `Log10`, `Norm`) or an arbitrary constant multiplier as
  `f"{CUSTOM_FACTOR_PREFIX}<number>"` (`"custom:2.5"`) — `apply_y_transform` checks that prefix
  first. UI: picking "Custom factor..." in `TraceStyleWidget`'s combo (index
  `Y_TRANSFORMS.index("custom")`) enables a text field for the number; `set_trace` reverses the
  mapping (`scale_factor.startswith(CUSTOM_FACTOR_PREFIX)` -> select that combo entry, populate
  the field with the numeric part) so it round-trips.
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

- **IPC threading.** `IPCBridge._listen_loop` runs on a daemon thread whose only job is
  `accept()`; each accepted connection is immediately handed to its **own** daemon
  thread (`_serve`, with a 10s socket timeout) rather than being read/handled/closed
  inline in the accept loop. This matters because Jupyter kernels are independent
  processes each opening their own short-lived connection — with a single shared
  accept-then-handle loop (the previous design), one slow or stuck connection (a kernel
  that connects but is slow to send, a client that never closes) blocked `accept()`
  from ever running again, wedging *every other* kernel's calls behind it until that
  one call finally gave up or timed out; per-connection threads make kernels
  independent of each other the way separate processes are supposed to be. *Mutations*
  (`append_dataframe`, `append_trace`, `append_image`, `add_to_sheet`,
  `add_image_to_sheet`, `remove_data`, `clear`) are re-emitted as the `data_received`
  Qt signal (a cross-thread queued emit — fire-and-forget, doesn't block the serving
  thread) and handled on the GUI thread by `SciSuiteWindow.handle_incoming_ipc`.
  *Queries* (`list_data`, `list_images`, `list_traces`, `get_data`) are answered
  directly from `IPCBridge.snapshot` on whichever thread is serving that connection —
  safe for concurrent reads because the GUI thread only ever replaces `self.snapshot`
  (or one of its top-level keys) wholesale, never mutates a shared list/dict in place,
  so a concurrent reader sees one atomic version or another, never a partial one. Two
  refresh methods: `_refresh_ipc_snapshot()` (cheap metadata — call after any change)
  and `_refresh_ipc_data()` (rebuilds `snapshot["data_full"]`, the full column values
  that back `client.get_data()` — call only on data-object add/update/remove/clear/load).

- **Jupyter round-trip is pull-edit-push, never automatic.** There's no live link between a
  notebook DataFrame and the pool's `DataObject` — editing one does nothing to the other until
  explicitly pushed back. `client.get_data(name)` → DataFrame from `data_full`;
  `client.get_image(name)` → ndarray from `image_full` (both rebuilt by `_refresh_ipc_data`).
  Edit and `push_dataframe(df, name=name, mode="update")` / `push_image(arr, name=name,
  mode="update")` → `update_from` refreshes the object in place (id preserved), open sheets
  re-render, the `ColumnTree` / data pool rebuild. `client.edit_data(key)` is a context manager
  wrapping exactly that pull/push pair in one block (`with suite.edit_data("spectra") as df: df["ratio"] = ...`)
  — it resolves `key` (id or name) to the object's actual name up front so the push-back always
  lands on the same object even when looked up by id, and skips the push if the block raises.
  `list_data` / `list_images` / `list_traces` / `list_sheets` are the metadata listings
  (`list_sheets`: id, name, rows, cols, subplot count, tags, `duplicate_name` — flags a sheet
  whose name collides with another's, since `client.plot(..., sheet=<name>)` resolves by id
  first, then by *first* name match; check this or target by id when a name might not be
  unique).
- **Mutations ack immediately, before the GUI thread processes them** (`IPCBridge._handle`'s
  catch-all returns `{"status": "success"}` the instant the socket receives anything not a
  query — see IPC threading above), so a Jupyter-side call can never learn that e.g. `plot()`'s
  `data`/`sheet` didn't resolve to anything. `_handle_add_to_sheet` compensates by posting a
  `statusBar()` warning instead (visible in the GUI, not returned to the caller) when: the data
  object isn't found; the `sheet=` name matches more than one sheet (uses the first, states its
  id); or `subplot=` doesn't exist on the target sheet (falls back to the last valid one) — the
  three ways "I called plot() and nothing happened" usually shows up.

- **Rendering is full clear-and-rebuild.** `PlotSheet.render(repository)` does `fig.clf()`,
  applies the figure frame (`fig.patch` edge/width/style, only visible with `fig_frame_on` and
  width > 0), sets each subplot's spines straight from the `SubplotModel` (hidden when
  `spine_width <= 0`), and re-adds every subplot. It repopulates `self.artist_map` (`Artist ->
  TraceRef`) + `self._text_targets` / legend handles so `on_canvas_click` hit-tests
  `event.dblclick` (`_hit_test`) and emits `element_double_clicked` with a `{"kind": ...}`
  dict; `SciSuiteWindow.open_element_editor` maps that to a dialog. A **right-click** (not a
  double-click) on a `"trace"` hit instead emits `element_right_clicked(hit, guiEvent)` ->
  `SciSuiteWindow._on_canvas_right_click`, a small `QMenu` ("Edit style..." / "Delete trace")
  positioned at `guiEvent.globalPosition()`; `_delete_trace_ref` removes that exact `TraceRef`
  (by identity) from whichever subplot holds it. `render` also computes `text_scale` once per
  pass — `size_scale` (1.0 when `SheetModel.fig_width_cm`/`fig_height_cm` are blank, else this
  figure's cm diagonal over the default 8x6in figure's, `_REFERENCE_DIAG_CM`) times
  `grid_scale = 1 / max(rows, cols)` (1.0 for the default 1x1 grid), the product clamped to
  [0.3, 3.0] — and multiplies every *fontsize* (title/x-label/y-label/tick-label/legend) by it
  before handing them to matplotlib. Fontsizes are stored in points, an absolute unit deaf to
  both the figure's physical size *and* how many subplots divide it up, so without this a
  bigger grid or a smaller physical figure would keep full-size text and `tight_layout()`
  would squeeze every axes box down to fit it; `text_scale` keeps the text-to-cell proportion
  consistent regardless of grid size or physical size (line widths / spine widths are left
  alone — text is what actually reserves layout margin). Each subplot's
  `x_tick_format`/`y_tick_format` (`"auto"` | `"plain"` | `"scientific"`
  | `"fixed"`, with `*_tick_digits` for `"fixed"`) is applied right after the aspect ratio via
  `_apply_tick_format` (`ax.ticklabel_format(style=...)` or a `FormatStrFormatter`) — display
  only, independent of `TraceRef.scale_factor` (which rescales the underlying data) and of the
  axis's own data aspect.

- **Resizing re-runs `tight_layout()` too, not just `render()`.** `render()` ends with
  `fig.tight_layout()`, but the canvas gets resized far more often than the model
  actually changes — letterboxing (`_AspectCanvasHost`, a fixed figure aspect), a
  splitter drag, undocking, maximizing, or just resizing the window all resize the
  underlying `FigureCanvasQTAgg` without going through `render()`. Without a matching
  re-layout, the axes rect stays at the fraction-of-figure `tight_layout()` picked for
  the *previous* size, so labels/ticks sized for that size can end up clipped
  (invisible) at the new one — toggling something that forces a full `render()` (e.g.
  border width) was the only thing that "fixed" it, since `render()` recomputes the
  layout as a side effect. Fixed by `self.canvas.mpl_connect("resize_event",
  self._on_canvas_resize)` in `PlotSheet.__init__` — the handler just re-runs
  `fig.tight_layout()` (guarded, same as in `render()`) and `canvas.draw_idle()`.

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
  content in a capped-height `QScrollArea` with the button box outside it. `model.MARKERS` /
  `MARKER_LABELS` (index-aligned, like every other option-vocabulary pair) list every marker
  the trace style combos offer — all valid matplotlib marker codes, so adding one is just
  appending to both lists.

- **Error bars are an overlay, not a plot type.** `PLOT_TYPES` stays `Line` / `Scatter` /
  `Line+Scatter` / `Step` / `Bar` — error whiskers are `TraceRef.show_errorbar` (bool),
  independent of `plot_type`, so "Scatter + errorbar" is just plot type `"Scatter"` with
  that flag on (a `Line`/`Bar`/etc. trace can carry it too). `yerr_col` (symmetric, e.g.
  std. deviation) or `yerr_low_col`/`yerr_high_col` (asymmetric offsets from the mean,
  e.g. min/max span) hold the error data; `yerr_mode` (`"std"` | `"minmax"`,
  `model.ERROR_MODES`) picks which `TraceRef.resolve_yerr(data_object)` returns — a 1-D
  array or a `(2, N)` array — regardless of `show_errorbar` (that flag only gates
  *rendering*, so the data stays resolvable even while the overlay is off).
  `TraceRef.has_error_data` is `bool(yerr_col or (yerr_low_col and yerr_high_col))`,
  used to disable the "Show error bars" checkbox on a trace with nothing to show.
  `_yerr_scale` keeps `resolve_yerr` consistent with `scale_factor` for the constant-
  multiplier tokens and `REL_VALUE_PREFIX` (all linear); `REL_INDEX_PREFIX` and
  Log10/Norm aren't linear (or their reference isn't in the token), so they leave the
  error unscaled. In `sheet.py`, `render` draws each trace's normal `_draw_trace` first,
  then, if `show_errorbar` and `resolve_yerr` isn't `None`, layers whiskers on top via
  `_draw_error_overlay` — `ax.errorbar(x, y, yerr=..., fmt="none", ...)` (`fmt="none"`
  so it draws only the whiskers, not a second line/marker); the returned
  `ErrorbarContainer` isn't itself a picker-friendly `Artist`, so hit-testing maps every
  `container.lines[2]` bar (the vertical whiskers) to the `TraceRef` in `artist_map`.

  **Building one** — select trace(s) in the active subplot's trace list
  (`self.subplot_traces`) and click **"Combine into mean ± error trace..."**
  (`self.combine_btn`, enabled at 1+ selected — `app._combine_selected_traces_dialog`
  dispatches on the count):
  - **One trace selected** → `_split_single_trace_dialog` / `SplitAggregateDialog`:
    for a trace whose own data is *itself* several repeat measurements concatenated
    together, rather than a genuinely single series. Two ways that can be laid out —
    pick one from the dialog's "Layout" combo (`model.SPLIT_LAYOUTS` /
    `SPLIT_LAYOUT_LABELS`) alongside the repeat count `n`:
    - **`"block"`** (default): `n` repeats *per condition*, blocks back-to-back —
      `[a1..an, b1..bn, ..., z1..zn]` — each block collapses to one point, so an
      `a*n`-point trace becomes `a` points.
    - **`"sequence"`**: one full cycle of `n` conditions, the whole cycle repeated —
      `[a,b,...,z, a,b,...,z, ...]` — each *position in the cycle* collapses to one
      point (aggregated across every repeat of it), so an `a*n`-point trace becomes
      `n` points. This is the shape a single long instrument sweep across `n`
      conditions, repeated `a` times, actually comes back as — reading it as `"block"`
      instead silently mixes different conditions' values together into one "block",
      giving wrong x positions and meaningless statistics.
    `model.split_into_repeats(values, n, layout=...)` (Qt-free) does the work: both
    layouts reshape to `(len(values)/n, n)` and only differ in *which axis* is
    aggregated (`axis=1` per-row for `"block"`, `axis=0` per-column for `"sequence"`);
    raises `ValueError` for `n < 1`, a length that isn't an exact multiple of `n`, or
    an unrecognized `layout`. The trace's x values are reduced the same way in
    `_split_and_aggregate_trace` (same reshape, same axis) — for `"block"` that's one
    x per block (mean within it, so x must be ~constant within a block); for
    `"sequence"` that's one x per cycle position (mean *across* repeats of it, so x
    must repeat the same per-position values every cycle — e.g. a condition index or
    a within-cycle time axis).
  - **2+ traces selected** → `_combine_multiple_traces_dialog` / `CombineTracesDialog`:
    for separate repeat traces already added individually (e.g. via the column
    picker), sharing the same x values — resolves each via its own
    `TraceRef.resolve(data)` (so each one's *own* `scale_factor` is already baked in —
    see the per-series normalization note below), rejects the set if their x arrays
    don't match (`np.allclose`, a `QMessageBox.warning`, nothing changed), then
    `model.aggregate_series(arrays)` (Qt-free: row-wise mean/std/min-max across
    equal-length arrays; raises `ValueError` for fewer than 2 arrays or mismatched
    lengths) does the same job across traces instead of within one.

  Either path's summary dict is `{"mean", "std", "err_min", "err_max"}`
  (`err_min`/`err_max` already mean-relative offsets, ready for an asymmetric
  errorbar) and lands the same way: a **new derived `DataObject`** (`"<label> (n=<n>
  mean±err)"`, columns `x`/`mean`/`std`/`err_min`/`err_max`, `source="split"` or
  `"combined"` — computed once, not live-linked back to the source) plus one new
  `TraceRef` (`plot_type` from the dialog's "Base plot style" combo, `show_errorbar =
  True`, colour = the source trace's / first selected trace's) that by default
  replaces the original(s) at the earliest one's position (each dialog's "Remove the
  original trace(s)" checkbox, on by default). `model.common_label(names)` (used for
  the *combine* path's label) takes a common prefix, else a common suffix, else the
  first name, e.g. `["a_1","a_2","a_3"] -> "a"`.

  **`yerr_mode`** is editable afterwards from the Trace Style tab/dialog
  (`TraceStyleWidget.errbar_check` + `errbar_mode_combo`, shown/enabled only when
  `has_error_data`) — switching it is instant since both error columns already exist;
  it's in `_STYLE_FIELDS` (serializes, included in the Cancel-dialog snapshot) and in
  `app._TRACE_STYLE_BROADCAST_FIELDS` ("Apply this trace's style to all traces" can
  carry it over too — `show_errorbar` itself deliberately isn't broadcast, since
  copying "on" onto a trace with no error columns would be a no-op at best).

- **Relative-change Y-transforms** (`model.REL_VALUE_PREFIX` / `REL_INDEX_PREFIX`,
  tokens `Y_TRANSFORMS` entries `"rel_value"` / `"rel_index"`) express `(y-ref)/ref`,
  as a fraction or `*100` as a percent (`model.REL_MODES`/`REL_MODE_LABELS`, `"frac"` |
  `"pct"`) — token shape `f"{PREFIX}{mode}:{ref}"`, e.g. `"relval:pct:100"`.
  `REL_VALUE_PREFIX`'s `ref` is a fixed number, the same for every point.
  `REL_INDEX_PREFIX`'s `ref` is resolved from `y` itself (`y[row index]`), so applying
  it to each of several repeat traces *individually* (each keeps its own `scale_factor`)
  normalizes each to **its own** baseline before they're ever aggregated — exactly what
  "Combine into mean ± error trace..." needs for a meaningful spread when the repeats
  don't share one absolute baseline: set each trace's Y transform to "Relative to nth
  point..." first, *then* combine (resolving each via its own transform, per above) —
  aggregating raw values and rescaling only the combined mean afterward would blur that
  per-series difference. `TraceStyleWidget` shows a `relref_edit` (reference value or
  row index, depending which of the two is picked) and `relmode_combo` (frac/pct) only
  for these two transform choices (`_on_transform_changed` / `_set_row_visible`, the
  same show/hide pattern as the "Show error bars" row and the "custom factor" field).
  `_yerr_scale(factor, y=None)` derives the matching multiplier for an error column:
  `REL_VALUE_PREFIX` is linear in the token alone (`100/ref` or `1/ref`); `REL_INDEX_PREFIX`
  needs the trace's own *raw* y column to resolve `y[row index]` the same way
  `apply_y_transform` does, which `TraceRef.resolve_yerr` now fetches itself
  (`data_object.get(self.y_col)`, the pre-transform values) and passes through — so an
  error-bar trace normalized to "its own nth point" gets its std/min-max rescaled to
  match the mean, not left at the raw scale. Without a `y` array (or `ref == 0`),
  `_yerr_scale` degrades to `1.0` (unscaled) rather than raising.

  **Batch UI**: select 1+ traces in the trace list and click **"Normalize traces..."**
  (`self.normalize_btn`, enabled at 1+ selected, next to "Combine...") →
  `_normalize_selected_traces_dialog` / `NormalizeTracesDialog` — pick "No
  normalization" / "Fixed value" / "Each trace's own nth point" or plus fraction/percent,
  and it sets that one `scale_factor` token on *every* selected trace at once (the same
  field the Trace Style tab's Y-transform combo edits one at a time) — no new data
  object, so it's instant and trivially reversible ("No normalization" resets to
  `"1x"`), and it already works identically on a plain trace or one with an error-bar
  overlay via the `_yerr_scale` fix above (the dialog surfaces how many of the
  selection carry one, as a reminder that their error rescales too).

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
  (writes `project_path`, or falls back to Save As, **synchronously** — an explicit,
  infrequent action where blocking briefly for clear success/failure feedback is fine).
  A `QTimer` (`_AUTOSAVE_SECONDS`, 120s) fires `_periodic_autosave`, which **always**
  refreshes the crash-recovery session file (`~/.scisuite_session.json`) regardless of
  whether a project file is set, and also re-writes `project_path` when one is set —
  previously `save_session()` only ran from `closeEvent`, so a crash or killed process
  between saves lost everything since the last clean close; the timer now covers that
  gap too. Unlike Ctrl+S, **the periodic tick runs each save on a background thread**
  (`_background_save`): `_project_model()` (which touches the live model — `DataObject`
  columns, `TraceRef`s, etc.) still runs on the GUI thread to build the `ProjectModel`
  snapshot, but the actual `ProjectModel.save(path)` call — `to_dict()`'s
  `numpy.tolist()` conversions, `json.dump`, and the disk write itself — happens in a
  daemon thread, with the result reported back via the `_autosave_done` signal
  (queued across threads, the same pattern `IPCBridge.data_received` uses). This
  matters because the project can live on a synced/network drive (this repo does), where
  a single autosave can take long enough to freeze the whole window for its duration if
  done inline on the GUI thread — on a 2-minute timer, that reads as "the app hangs for
  a bit every so often." `_autosave_threads` (path -> `Thread`) skips starting a new
  save to a path whose previous one hasn't finished yet, rather than piling them up.
  `_on_autosave_done` only surfaces a status-bar message for the named project path (not
  the session file, which always saved silently). `closeEvent` still does one last
  **synchronous** `save_session()` (+ `_autosave_project()`, its synchronous counterpart,
  if a project file is set) — deliberately not backgrounded, since a daemon thread still
  writing when the process actually exits would be killed mid-write.

### UI layout (`app.py`)

Left is a vertical splitter: **data pool** list (`DataPoolList` — DataObjects + ImageObjects,
different icons; `ExtendedSelection` — Ctrl/Shift click to multi-pick, Delete or the
"Delete N selected" context action removes them all via `_delete_selected_data`; right-click a
multi-selection of two-or-more DataObjects for **"Add common column(s) to sheet..."** —
`CommonColumnsDialog` lists the columns common to every selected object (`set.intersection`
over `column_order`; a plain info box if there are none) with the same tick-one-X/tick-many-Y
`QTreeWidget` pattern as `ColumnTree`, then `_add_common_columns` adds one `TraceRef` per
`(object, ticked y column)` pair — same x column, each labeled `"<object name>: <y column>"` —
to the active or a new sheet, each getting its own colour-cycle colour via `_next_trace_color`;
right-click a **single** DataObject instead for rename/tags/delete)
→ **middle `QStackedWidget`**: page 0 = **column picker** (`ColumnTree` X/Y ticks + `head()`
preview + "Add to active/new sheet"; valid X+Y is draggable, MIME
`application/x-scisuite-cols`; also `ExtendedSelection` on the column rows — Ctrl/Shift-click
several rows then tick one row's Y box to tick (or untick) that box on every selected row at
once, so many traces can be queued in one go; the row selection itself never enters the
drag/add payload, only the X/Y checkbox state does), page 1 = **`ImageAxesPanel`** (row/col
axis combos + add buttons) — `_on_data_selected` picks the page by object type →
**project tree** (`ProjectTree`: folders + sheets, **single-click** to open a tab, right-click
"Set tags…", accepts column drops; also `ExtendedSelection` — multi-pick folders/sheets,
Delete or "Delete N selected" → `_delete_selected_tree_items`, which folds a selected child
into its selected ancestor folder and confirms once; `_on_activate` won't switch tabs while >1
is selected. **Drag a sheet/folder to reparent or reorder it** — `dropEvent` hands anything
that isn't a `MIME_COLS` column-drop to `_move_dragged_items`/`_reparent_items`, which
reparents the dragged item(s) itself via `takeChild`/`insertChild` (drop `OnItem` on a folder
nests inside it; `AboveItem`/`BelowItem` reorders as a sibling; dropping on empty space sends
it to the root) rather than falling back to `QTreeWidget`'s default same-widget move. That
default path is only reliable under `DragDropMode.InternalMove`; this tree needs plain
`DragDrop` instead (so a `ColumnTree` column-drag can also land on it), and under that mode
Qt's generic MIME round trip can come back with a moved sheet's `ITEM_ID_ROLE` stale --
`_open_sheet_tab` then opens the wrong (looks "empty") sheet on the next click. `_reparent_items`
takes plain items + a `QAbstractItemView.DropIndicatorPosition`, not a `QDropEvent`, precisely
so it's testable without one -- synthesizing real `QDropEvent`/`dragMoveEvent` objects outside
an actual OS drag session is unreliable (observed native crashes) in this offscreen setup).
Center: one tab per open sheet, each a `QSplitter(Vertical)` — plot →
image `slider_bar` → collapsible **Notes** pane (`current_sheet()` still returns the
`PlotSheet`). Right, top→bottom: **rows/cols spinboxes**
(`on_grid_changed`), the **subplot-order strip** (`ReorderList`, drag to reorder
`SheetModel.subplots`; its multi-selection is the colormap "Selected subplots" scope), the
active subplot's `TraceRef` list (`self.subplot_traces`, also a `ReorderList` —
`ExtendedSelection` + `InternalMove`; checkbox = `enabled`; >1 selected swaps `TraceStyleWidget`
for `BulkTraceEditWidget`; drag one or several selected rows to reorder `sub.traces` —
`_on_traces_reordered` reads the post-drop `UserRole` order, validates it's a permutation,
rebuilds `sub.traces`, then reassigns every trace's `color` from `_color_for_index(sm, i)` (the
colour cycle is positional, so it now tracks the new order too, same as a freshly-added trace
would get) before `_sync_subplot_trace_list`, then re-selects the moved trace(s) by object
identity, since their positions changed. Trace order drives both z-order — later entries draw
on top — and legend order, so this is how to fix "right traces, wrong order/stacking".
Selecting any trace(s) here — one or several — also flips `inspector_tabs` to "Trace Style",
so the style/Y-transform controls are visible regardless of which tab was open; any
selection also enables two buttons below "Remove selected trace(s)": **"Combine into
mean ± error trace..."** (one selected -> split its own data into repeats; 2+ ->
combine them — see "Error bars are an overlay, not a plot type" above) and
**"Normalize traces..."** (batch-sets a relative-change `scale_factor` on the whole
selection — see "Relative-change Y-transforms" above)), the
**`ColormapPanel`**, and the
Axes / Trace Style / **Figure** inspector tabs (`inspector_tabs`; the Figure tab is
`FigureStyleWidget` bound to the *active sheet* via `sync_active_subplot_inspector`, the same
widget class `FigureDialog` wraps for the figure-background double-click editor. Both the
Axes/Panel and Trace Style tabs carry a **broadcast button** below the widget itself, outside
its scroll area: "Apply this style to all subplots" (`_apply_axes_style_to_all_subplots`)
copies every `SubplotModel` field in `_AXES_STYLE_BROADCAST_FIELDS` (everything except
title/labels — content — and the axis limits — data-range specific) from the active subplot
to every other subplot in the sheet; "Apply this trace's line style to all traces"
(`_apply_trace_style_to_all_traces`, enabled only when exactly one trace is selected) copies
`_TRACE_STYLE_BROADCAST_FIELDS` (plot type, line style/width, marker, marker size, alpha —
not colour or `scale_factor`) from that trace to every trace in every subplot of the sheet.
Both confirm once with a count and leave the source unchanged). Checkbox
indicators in `ColumnTree` and `self.subplot_traces` get an explicit `_CHECKBOX_QSS`
stylesheet — the app-wide Fusion style (`__main__.main`) doesn't adapt to an OS dark theme on
its own, so a plain indicator can render almost invisible; the explicit colours make it visible
in any theme. Fuzzy search (`fuzzy.fuzzy_match`) filters the data pool and the tree; the tree
haystack per sheet is
`_sheet_haystack` (name + tags + notes + referenced data-object / image names + every subplot
title/x_label/y_label + trace labels).

## Display / export / zoom (sheet.py)

- `_AspectCanvasHost` is a `QScrollArea`: base canvas size comes from the host's *full* size (not
  the viewport -- scrollbars would nudge it) letterboxed to `fig_width_cm:fig_height_cm`, times a
  `zoom` (Ctrl+wheel / Ctrl+0 via an event filter on the canvas). `PlotSheet._sync_zoom_dpi` keeps
  the figure's size in *inches* constant by setting `fig.dpi = 100*zoom*devicePixelRatio`, so text
  and lines zoom with the plot; it re-runs from `_on_canvas_resize`, which matplotlib's own resize
  handling precedes (mpl derives inches = px / fig.dpi). Never set `fig.dpi` elsewhere.
- Exports go through `PlotSheet.savefig_export`: hides the dashed active-subplot cue (it is
  on-screen UI and used to land in clipboard/SVG output), and either saves WYSIWYG (current figure
  size, **no** `bbox_inches="tight"`) or at the sheet's cm size with `tight_layout()` re-run for it.
  Clipboard = WYSIWYG; SVG = cm size when set, else WYSIWYG.
- `TraceRef.sort_x` sorts x/y (and error arrays, `_yerr_sorted`) at *resolve* time only -- data
  objects are never reordered. `plot(..., plot_type=, style=, sort=)` rides in the `trace_style`
  key of `add_to_sheet` (GUI `_add_traces` + `OfflineBackend` both `apply_style` it).
- `model.copy_subplot_style` (style / +labels / +limits) backs the "all subplots" and "selected
  subplots" buttons; `model.duplicate_sheet_model` + `app._duplicate_tree_item` back tree Duplicate.

## Heatmaps (image objects with coordinates)

- A heatmap is **not a new object type**: it is an `ImageObject` with `axis_coords` (axis -> list of
  floats, or of strings for a categorical axis; `axis_positions(axis)` -> `(float positions, labels|None)`;
  serialized inline in `to_dict`, the array still goes to the sidecar `.npy`). `is_heatmap` = has any
  coords. Axis 0 = rows = y, axis 1 = columns = x.
- `ImageRef` gained heatmap fields (`use_coords`, `cmap_reverse`/`cmap_bins`/`cmap_boundaries`,
  `iso_*`, `{x,y}_tick_mode/_values/_every`); `ImageRef.STYLE_FIELDS` = everything settable via an
  `image_style` payload / `apply_style`. `model.make_image_ref(obj, axes, style)` builds a ref (heatmaps
  default to `aspect="auto"`) -- used by both `app._add_image_to_subplot` and `OfflineBackend`.
- Pure helpers in `model.py` (tested Qt-free): `heatmap_band_edges`, `heatmap_iso_levels` (explicit ->
  band edges -> `nice_levels`, then `iso_above`/`iso_below`), `resolve_ticks`, `heatmap_from_xyz`,
  `parse_float_list`.
- `PlotSheet._draw_image`: no coords -> `imshow` as before (index extent); coords -> `pcolormesh`
  (`shading="nearest"`, or `gouraud` for bilinear/bicubic interpolation) at the cell centres, `origin` is
  ignored (flip via reversed axis limits). Discrete colours = `BoundaryNorm` + `cmap.resampled(n)`.
  `_draw_isolines` contours at `heatmap_iso_levels`; `_apply_image_ticks` runs after the axis limits are
  set (it needs the final axes) using `_image_geom` recorded by `_draw_image`.
- API: `client.push_heatmap` / `push_heatmap_xyz` / `heatmap` (friendly args -> `_heatmap_style` -> the
  `image_style` key of `add_image_to_sheet`, plus `organize`). GUI controls: `ImageStyleWidget`.

## Custom tick labels

- `SubplotModel.x_tick_labels` / `y_tick_labels` hold a text mapping `"value=text; value=text"`
  (`model.parse_tick_labels` / `format_tick_labels` / `apply_tick_labels`, Qt-free). They count as
  *label* fields (`SUBPLOT_LABEL_FIELDS`: copied by "include labels", excluded from style broadcast,
  settable through the `organize` payload's `subplots`). `PlotSheet._apply_axis_ticks` runs after the
  limits are final: the heatmap tick mode first (categorical axes default to one tick per label),
  then the mapping -- a key that is an existing category label renames it; otherwise numeric keys
  define the ticks. Client args accept a dict, a list (positions 0..n-1) or the raw string.

## LaTeX documents (`latex.py`, `docview.py`, `latex_build.py`)

- A document is a `DocumentModel(doc_id, name, source)` in `ProjectModel.documents` (optional key, no
  version bump), a tree node `{"type": "document", "doc_id"}` (`TYPE_DOC`; `tree_move_sheet(..., kind=
  "document")`) and, when open, a `DocumentTab` in the same tab widget as the `PlotSheet`s -- so
  **`current_sheet()` returns `None` while a document tab is current**; `_active_sheet_model()` then falls
  back to `_last_sheet_id`. Never assume `tab_widget.currentWidget()` is a `PlotSheet`.
- Figures: `\noorsheet[opts]{key}` (from `noorsuite.sty`, written into the build dir) ->
  `\href{noorsuite://sheet/key}{\includegraphics{noorsuite-figures/key.pdf}}`; `key` = sheet id, or name slug
  (`latex.resolve_ref`). `sheet.export_sheet_pdf` renders a *copy* of the sheet at its cm size
  (`DEFAULT_FIG_CM` when unset) with `pdf.fonttype 42`. The preview finds the click target from PDF link
  annotations (`QPdfLinkModel`, rectangles in points, top-left origin) -- hyperref is therefore required.
- Build: tectonic/latexmk/xelatex/pdflatex via `latex.find_engine()` ($NOORSUITE_LATEX, config.json,
  PATH, a `noorlatex` conda env) in `~/.noorsuite/build/<doc_id>/`; figures are exported on the GUI thread,
  the engine runs in a worker thread (`_built` signal). Auto-compile is debounced and keyed on
  `sheet_fingerprint` (sheet model + referenced data hashes), so tab switches don't rebuild.
- Headless: `python -m NoorSuite latex build|export|engine` (`latex_build.py`, offscreen Qt; keep the
  `QApplication` referenced or the process dies silently). Client/IPC: `push_document`, `list_documents`,
  `get_document`, `figure_snippet`; mirrored in `OfflineBackend`.
- In test files write LaTeX with raw strings (`r"\noorsheet{..}"`): `"\n"`/`"\u"` silently corrupt it.

## Search, Zotero, figure panel

- **Search**: `fuzzy.smart_score(query, fields)` with `fields = [(kind, weight, text)]`, kind `name` /
  `tags` / `content`. All tokens must match (AND): whole word 3.0, word prefix 2.4, substring 1.5 (x field
  weight); subsequence ("rspns") 0.8 only inside a single *name/tag word* with the same first letter and
  token length >= 3 -- never over the content blob. `-x` excludes (no subsequence), `#t`/`tag:`/`name:`/
  `note:` scope, `"phrase"`. `SciSuiteWindow._sheet_fields` builds the sheet fields; old `fuzzy_match` is
  kept for non-search uses. Don't go back to one concatenated haystack + subsequence.
- **Zotero library access = Better BibTeX JSON-RPC** (`POST /better-bibtex/json-rpc`, verified live
  against Zotero 10.0.6 + BBT 9.0.71): `item.search("")` returns the *whole library* as CSL-JSON with
  `citation-key` (5000 items ~5 s), `item.search(term)` filters; `user.groups` lists collections (no
  per-collection item method exists). Wrappers: `zotero.rpc`, `rpc_search`, `rpc_find_keys` (exact citation-key
  match). The UI searches live (thread + `_zgen` generation counter so the newest search wins) and
  `DocumentTab` resolves unknown `\cite` keys through it (`_lookup_tried` stops retry loops). The local
  Zotero `/api` is off by default (403) -- do not depend on it. Never write a UI that needs the BBT *export*.
- **Zotero** (`zotero.py`, stdlib): `cayw_pick()` = GET `http://127.0.0.1:23119/better-bibtex/cayw?
  format=json` (blocks until the user confirms in Zotero's popup -> call it off the GUI thread; Zotero
  core has no `/cayw` and the local `/api` answers 403 unless enabled -- `local_search` documents that).
  `normalize()` accepts CSL-JSON *and* Zotero-JSON shapes into one record (`key`, `type`, `title`,
  `authors` [[family, given]], `year`, `container`, ..., `zotero_key`; organisation names carry a trailing
  `*`), `to_bibtex()` writes entries, `cite_keys()` scans `\cite*`. Records persist in
  `ProjectModel.references` (optional key); `DocumentTab.compile` writes `references.bib` for the cited
  keys only; `latex_build` does the same headless. IPC: `references` / `list_references`.
- **Thin records**: items from the CAYW picker arrived with only `key/type/title/zotero_key` (all of a
  user's 7 references; entries printed as a bare title and natbib failed with "Bibliography not compatible
  with author-year citations" on APA). `zotero.is_complete` (authors/editors AND year) decides;
  `DocumentTab._complete_records` re-fetches by exact citation key through the JSON-RPC and
  `merge_record` fills the gaps while keeping the cited key. Runs on tab open, after a pick and at every
  build. Publisher placeholders (`n/a`, `0`, `-`) are not values (`zotero.normalize`'s `val`). The ACS entry
  layout is pinned by `test_entry_matches_the_acs_reference_example...` -- the user's own example string.
  Insertions are redirected in front of `\end{document}`; `number_citations` leaves text behind it alone.
- **Switching formats must be reversible** (regression: apalike.sty + natbib = "\bibhang already
  defined", every later format failed). `latex.MANAGED_PACKAGES` is the set of citation packages
  (`natbib, achemso, apalike, apacite`); `apply_bib_style` (explicit switch) replaces them with the preset's,
  fixes/inserts the `\bibliographystyle` line before the `\bibliography{..}` marker, and
  `normalize_for_style` does the same non-destructively for the build copy. The `apalike` preset is natbib +
  `apalike.bst` (never `apalike.sty`). Numeric BibTeX presets (`NUMERIC_BST_ONLY`) turn `\citep/\citet` into
  `\cite` *before* `ensure_bibliography`, otherwise natbib gets added and breaks `ieeetr.bst`. tectonic's
  `--only-cached` makes BibTeX print `?` for a not-yet-downloaded `.bst` (exit code 0!): `build_pdf` retries
  online when the output says "issued by BibTeX". `DocumentModel.cite_mode` (`refstyle.CITE_MODES`) picks
  superscript / `(1)` / `[1]`. Test with a chain of switches (`test_every_format_builds_and_...`).
- **Bibliography format**: `latex.BIB_STYLES` (id, label, bst, preamble packages; `native` marks formats
  NoorSuite writes itself). Default `acs-guide` = `refstyle.number_citations`: `\cite*` -> superscript
  numbers by first appearance (runs of 3+ as `1--3`), `\bibliography{..}` marker -> a `thebibliography`
  with `(n)` labels, entries from `refstyle.format_reference` (ACS Guide, April 2025 revision: no 2026
  revision exists in public sources -- change rules *only* there). `latex.prepare_build_source` is the single
  entry for GUI + headless builds. BibTeX presets (`acs` = natbib `super,sort&compress` + `achemso.bst`, ...)
  remain; the format is stored per document (`DocumentModel.bib_style`, or the text's own
  `\bibliographystyle`) and the combo ignores the wheel -- a global default may only change on an
  explicit "Use as default". The user default is stored in
  `~/.noorsuite/config.json` (`bib_style`, overridable by `$NOORSUITE_BIB_STYLE`). `latex.
  ensure_bibliography(source)` returns a *build copy* with the reference list / natbib added when the text
  cites but lacks them -- never edit the user's source for that (a bare `[?]` was the symptom). The tectonic
  engine needs the real HOME (a fake one fails with "Unable to find standard directories").
  Import: `zotero.parse_bibtex` / `load_file` (.bib, CSL-JSON) / `local_library` + `local_collections`
  (Zotero local API, 403 unless enabled); `merge_record` keeps the already-cited key for the same
  `zotero_key`.
- **DocumentTab side panel**: `FigurePanel` (thumbnails via `host.sheet_thumbnail` -> `sheet.export_
  sheet_png`, cached in `~/.noorsuite/thumbs/<id>-<fingerprint>-<px>.png`; items need an explicit
  `setSizeHint`, uniform item sizes clip icons) and `ReferencePanel`. Drops: `LatexEditor.dropEvent`
  inserts `text/plain` and forces `CopyAction` (a Move would make the source tree delete the item);
  `ProjectTree.mimeData` adds the figure snippets (a folder yields its sheets).
- **Build speed**: figures are re-exported only when `sheet_fingerprint` changed (`_fig_fp` + file
  exists); tectonic runs with `--only-cached` and retries without it on failure; the status shows
  timings. One figure export costs ~1 s (PlotSheet + render + PDF), LaTeX ~3 s with tectonic.
- **Themes**: `docview.THEMES` / `_stylesheet` pin editor, log, lists and preview colours (white ground,
  black text by default; QSettings `doc/dark`). Don't rely on the app palette in the Document tab.
- While a document tab is current the main splitter collapses the inspector (`_saved_split` restores it).

## Presentations (`templates.py` "beamer", `pptximport.py`)

- `TEMPLATES["beamer"]` (class beamer, 16:10, `t` alignment, `default_body`, `tail`); `detect` -> `"beamer"`;
  `base_of(id) == "beamer"` separates presentation layouts from document layouts (project-tree menus, layout combo,
  `FigurePanel.beamer_provider` -> `latex.frame_snippet` instead of a figure environment).
  `extract_parts` drops the generated `\titlepage` frame so layout switches do not double it.
- `pptximport.parse(path)`: geometry of master / layouts / slides (EMU -> cm), fills with theme colours resolved,
  pictures with `srcRect` crop, static text, placeholder sizes. `import_pptx` writes
  `~/.noorsuite/presentations/<slug>/beamertheme<slug>.sty` (TikZ overlays at absolute positions scaled
  16 cm / slide width; background = master, frametitle = title placeholder in the bar, footline = number + source
  line, `title page` = the layout of slide 1) + converted media + `template.json`, and a user layout
  `~/.noorsuite/templates/<slug>.json` (base beamer). Frame bodies start at the master body placeholder's y.
- `pptximport.support_files(source)` (called by `latex.prepare_workdir` and thus by GUI build, headless build and
  `write_bundle`) copies the theme of every `\usetheme{name}` found in `presentations_dir()` next to `main.tex`.
  Env overrides for tests: `NOORSUITE_PRESENTATIONS`, `NOORSUITE_TEMPLATES`.
- Never commit corporate `.pptx` files / logos (`*.pptx` is git-ignored); the converted theme stays in the user's
  home directory.

- Public repo rule: no corporate artwork. `NoorSuite/themes/noorclean/` is the only bundled theme (text only,
  `TEMPLATES["beamer-clean"]`, base `beamer`, detected by `\usetheme{noorclean}`); `pptximport.support_files`
  looks in `presentations_dir()` first, then `bundled_themes_dir()`. Portable bundles: `export_bundle(theme, out)` /
  `import_bundle(path)` (`.noortheme` zip: `bundle.json`, `layout.json`, `theme/*`).

## PowerPoint export and slide show (`pptxexport.py`, `docview.PresentationView`)

- `pptxexport.parse_presentation(source)` -> meta + frames (title frame = one with `\titlepage`); frame bodies
  are parsed by `_blocks` (itemize / enumerate with levels, columns, tabular, figure / center transparent,
  `\noorsheet` / `\noorfile` / `\includegraphics` pictures with the `width=` fraction, block titles bold,
  `\framesource`, `\note`, `\frametitle`). `plain()` maps LaTeX text + math to Unicode (Greek, super/subscripts).
- `export_pptx` (python-pptx, optional dependency `NoorSuite[pptx]`): starts from the theme's `reference.pptx`
  (kept at import together with the layout names in `template.json["pptx"]`: title / content / two-content /
  title-only layout and the placeholder idx order of the title layout) or from python-pptx's default template
  stretched to 16:10 (`_to_16_10` scales only shapes with their own xfrm). Sample slides of the reference are
  dropped; slide-number placeholders are cloned onto every slide; unused placeholders are removed so no
  "Click to add text" boxes remain. Callers pass `sheet_png` / `asset_png` (window host or headless).
- Pictures are fitted into the body placeholder's rectangle; text + picture splits the area 46 / 54.
- Effects: `_overlays(text)` turns `\pause`, `\item<n->`, `[<+->]`, `\uncover/\only/\visible<n>{..}`,
  `\begin{env}<n>` into private marker characters (`\x02` pause, `\x07` scoped `+`, `\x03n\x04` ... `\x05`
  scoped step, `\x06n\x04` pause[n]); `_Steps.feed` reads them in document order (counter `n`, hide level
  after `\pause`, scoped `floors`). Blocks get a trailing step only when > 0 (`("text", s, step)`, list =
  `("list", items, steps)`), so older shapes still compare equal. `export_pptx(effect=)` -> `_animate`
  (p:timing, one click per distinct step; paragraph targets for list items, `bldP build="p"`),
  `_transition` (`\trans...` in the frame or before it). Unspecified content after `[<+->]` stays visible
  (Beamer counter vs. pause level are two variables).
- `PresentationView(fade=True)` cross-fades slides (set by `\transfade` / `\transdissolve` in the text).
- `findbar.FindBar` (Ctrl+F / Ctrl+H / F3 in `DocumentTab`): Python `re` over `toPlainText()` (UTF-16 offsets
  handled), highlights as `ExtraSelection`s, replace-all as one edit block back to front.
- `PresentationView` = frameless full-screen QPdfDocument viewer (keys documented in the class);
  `DocumentTab.present` builds first (`_ensure_built`).
- `latex.write_bundle` writes a `README.txt` (compile + run instructions).

## Author lists and SI documents (`templates.py`, `authorsdialog.py`)

- `parse_authors(source)` -> `{"authors": [{name, affils[keys], email, corresponding}], "affiliations": {key: text}}`
  from authblk / elsarticle / achemso / plain `\and` front matter (only text before the first section /
  abstract / `\maketitle`). `render_author_block(tid, authors, affiliations)` -> placeholder texts
  (`@AUTHORBLOCK@`, `@ACSAUTHORS@`, `@ELSAUTHORS@`, `@RSCAFFIL@`, `@AUTHORS@`) + preamble lines; keys are renumbered
  1..n by first use. `extract_parts` returns `author_info` / `affiliations`; `apply_template(source, tid,
  authors=None, affiliations=None)` keeps them across layouts. `authblk` is added only for the article base.
- `normalize_authors(source)` (called first in `latex.prepare_build_source`): converts authblk in achemso /
  elsarticle to native commands, `\affiliation` -> `\affil` next to authblk, adds authblk for `\author[..]`.
- `si_document(source, title)`: surgery on the main text (title, abstract, body replaced; preamble verbatim; S
  counters via `\renewcommand{\thefigure}{S\arabic{figure}}` ...). `docexport.counter_prefixes` reads those
  renewcommands so Word captions / refs / equation numbers carry the `S`.

## Data tables (`tables.py`, `tabledialog.py`)

- `TableModel` (`ProjectModel.tables`, optional key): `data_id`, `columns` [{col, header, fmt, digits, align,
  scale}], `row_start/row_end/step/max_rows`, `sort_by/descending`, `booktabs`. The document only holds
  `\noortable[opts]{id-or-name-slug}`; `tables.expand_tables(source, tables, repository)` replaces it by the
  rendered `tabular` in the *build copy* (GUI compile, headless build/export, Word export, LaTeX bundle) and
  adds `\usepackage{booktabs}` when needed. `normalize_columns` is the shared spec normaliser (client push,
  GUI `_handle_table`, `OfflineBackend._table`). Numbers: math minus, NaN -> `--`, scientific as
  `m\,$\times$\,10$^{e}$`. Data objects hold numeric columns only.
- Fingerprints (`table_fingerprint`) feed `DocumentTab._fingerprint` so edits of the data rebuild the document.
  Double-click on the reference line emits `table:<key>` -> `DocumentTab._open_ref` -> editor dialog.
- `docexport.swap_figures` skips `%` comments (the template preamble comment mentions the macros).

## Journal templates (`templates.py`)

- `TEMPLATES` (article / acs / rsc / elsevier): `preamble`, `front` (with `@TITLE@ @AUTHORS@ @ABSTRACT@`,
  `@ACSAUTHORS@`, `@ELSAUTHORS@`), figure `widths` [(label, cm, spans_both_columns)], `two_column`, `word`
  look, `known` packages. `extract_parts(source)` -> title / authors / abstract / body / extra preamble;
  `apply_template` = extract + `build_document`; `compose` wraps body-only text (`push_document(template=)`).
  `detect(source)` is derived from `\documentclass` / `\usepackage{rsc}` -- there is no stored template id.
- Classes that bring natbib (achemso, elsarticle, rsc.sty): `latex.own_citations(source)`; the citation
  package logic (`normalize_for_style`, `ensure_bibliography`, `apply_bib_style`) leaves them alone.
- Authors: article `A \and B`, others `A and B`; ACS / Elsevier get one `\author` each
  (`_split_authors`). RSC front matter is the `\twocolumn[...]` block.
- `DocumentTab.set_layout` (undoable edit, then re-applies the document's bib style), `layout_combo`;
  `FigurePanel.width_combo` / `match_check` (`host.set_sheet_width`) / `figure*` for double-column widths
  in two-column templates. Word: `docexport.make_reference_docx` restyles pandoc's reference.docx.
- User layouts: `make_user_template(source, label, widths)` writes `<user_dir>/<slug>.json`
  (`$NOORSUITE_TEMPLATES` or `~/.noorsuite/templates`; preamble minus front-matter commands + `@EXTRA@`, the
  base template's front matter, `tail`, widths, word look, `known` packages); ids are `user:<slug>`,
  `get / list_templates / base_of / delete_user_template`; everything that takes a template id accepts them.
  `DocumentModel.layout` remembers the choice (shown only while the text still has the base class).
- Verified with tectonic: achemso, elsarticle, rsc.sty compile; `rsc.sty` loads natbib itself (do not add
  `\usepackage{natbib}` -- option clash).

## Word / LibreOffice export (`docexport.py`)

- pandoc LaTeX -> docx/odt. `prepare_office_source`: native numbered ACS text (`prepare_build_source`
  with `acs-guide`, so the Word file always carries that list), `
oorsheet`/`
oorfile` -> PNG
  (`sheet.export_sheet_raster` at 300 dpi, `assets.export_png`), `thebibliography` -> numbered paragraphs,
  `number_references` (figure/table/section numbers, caption prefixes, `\ref`/`\autoref`/`\eqref`).
  Callers inject `sheet_png` / `asset_png` (window host vs. headless `latex_build._office`).
- Equations: `equations_to_tables` turns each numbered `equation` env into a pandoc `longtable` with p-columns (0.08/0.80/0.08 of the line: empty | `$\displaystyle ..$` | `(n)`), `style_equation_tables` (docx post-process of `word/document.xml`) centres / right-aligns the cells and removes borders. Numbering = order of `equation` envs outside comments, shared with `number_references` so `\eqref` matches. `align` / `eqnarray` rows -> 4-column table (marker | left part right-aligned | right part left-aligned | number; rows split by `_split_top`, nested `aligned` stays one row), `gather` rows -> 3-column; starred envs are not numbered; unresolved labels print as `[label]`.

## Objects (shapes) and imported figures

- `model.ShapeRef` (kinds `rect ellipse line arrow xspan yspan text picture`) lives in
  `SubplotModel.shapes`; **traces and shapes share one stack**: `SubplotModel.stack()` interleaves them
  (a shape sits above `layer_pos` traces), `set_stack()` adopts a reordered list, `remove_trace()` keeps
  shapes in place. The GUI layer list's `UserRole` is the index into `stack()` -- never into `traces`.
  `render` draws traces first with `zorder = 2 + 0.01*stack_index`, then shapes (limits frozen first, so
  objects never rescale the axes); artist -> `ShapeRef` in `artist_map`, hit test kind `"shape"`.
- Gradients: `model.gradient_rgba(stops, mode, ...)` (pure numpy; stops `[pos, "#hex", alpha]`) -> RGBA
  image clipped to the shape's outline patch (so rotation/ellipse/bands work); `fill_alpha` multiplies the
  stops' own opacity. Text goes through a mathtext check (a stray `$` must not kill the canvas).
- Objects come in through the `organize` payload (`shapes`, `clear_shapes`; `apply_sheet_annotations`, one
  implementation for GUI and offline). Client: `add_shape`, `clear_shapes`.
- Hierarchy: `ShapeRef.shape_id` / `parent_id`; `SubplotModel.children_of / descendants / depth_of /
  set_parent / remove_shape`. Moving a root carries its descendants: `model.apply_group_transform(child,
  old_geom, new_geom, fwd, inv)` (geom = `ShapeRef.geom()` = effective centre/size/angle; `fwd`/`inv` map
  to display pixels so rotations stay rotations). Numeric edits call `host.shape_geometry_edited`, canvas
  drags do the same inside `PlotSheet`. `scale_x/scale_y` multiply the box around its centre
  (`ShapeRef.bounds()` is the *effective* box -- never use raw x0..x1 for drawing).
- Rotation of rect/ellipse/picture is applied in **display space** (`sheet._DynRot`, composed as
  `tf + _DynRot(tf, cx, cy, angle)`), so what the mouse rotates is what is drawn on any axes scales.
- Canvas editing (`PlotSheet`): `set_selected_shape` -> figure-level handle artists placed in `_on_draw`
  (signature-checked, hidden during exports); press on a handle / object starts `_drag`
  (move | scale | rotate | point), motion re-renders throttled (~25 fps) from the drag-start snapshot,
  release emits `shape_edited`. `shape_selected` selects the entry in the layer list. Never edit a
  drag from the *current* model state -- always `_restore(start)` first (idempotent).
- `SubplotModel.bare` ("content only"): `set_axis_off`, no title/legend/face; `PlotSheet._layout()`
  replaces every `tight_layout()` call and removes all margins when every subplot is bare.
- `AssetModel` (`ProjectModel.assets`, base64 in the JSON) = imported SVG/PDF/PNG/JPG; `assets.py` finds
  Inkscape, writes the working file `~/.noorsuite/assets/<slug>-<id>.<ext>`, converts (`export_for_latex`:
  SVG -> PDF via Inkscape CLI, QtSvg fallback) and rasterises (`raster`, cached by fingerprint).
  `SciSuiteWindow.edit_asset` opens it and watches the file (`QFileSystemWatcher`, editors replace files
  so the path is re-added); `_asset_changed` re-renders pictures + notifies documents.
- LaTeX: `\noorfile[opts]{key}` -> `noorsuite-figures/asset-<key>.{pdf,png,jpg}` with link
  `noorsuite://sheet/asset:<key>`; the editor / preview emit `asset:<key>` which `DocumentTab._open_ref`
  routes to `host.edit_asset`. IPC: `ACTION_ASSET`, `ACTION_LIST_ASSETS`; client `push_asset`, `list_assets`.

## Claude skill & offline insertion

- `NoorSuite/skill/SKILL.md` is the skill (package data; `{{PYTHON}}` is substituted with
  `sys.executable` on install). `skill.py` (stdlib only) installs it to `~/.claude/skills/noorsuite/`,
  and `update`/`ensure` compare `git ls-remote` HEAD with pip's `direct_url.json` commit, `pip install
  --upgrade git+https://github.com/RSRekers/Noorsuite.git` (skipped for editable installs), then re-render
  the skill. `ensure` is throttled to 1/day via `~/.noorsuite/state.json`. CLI: `python -m NoorSuite
  skill|insert` (dispatched in `__main__` before PyQt is imported).
- `connect()` (client.py) returns the live `SciSuiteClient` if a GUI answers on the port, else an
  `OfflineClient` whose `_ipc` is `offline.OfflineBackend` -- it applies the *same payloads* the GUI
  receives to a `ProjectModel` file (default `~/.scisuite_session.json`, which the GUI loads at start).
  When adding an IPC action, implement it in **both** `app.handle_incoming_ipc` and `OfflineBackend`.
- `ACTION_ORGANIZE` + the `organize` key on `add_to_sheet` carry sheet name/tags/notes/folder/subplot
  titles+labels (`model.apply_sheet_annotations`, `tree_move_sheet`); `DataObject.notes` is new.
- Never run insertion tests against the default port while the user's GUI is up -- it is their live session.

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
- `_sync_subplot_trace_list`'s `keep_selection=True` default re-selects rows *by index*, which
  is only correct when the trace list itself didn't change identity underneath (a same-subplot
  edit/reorder/bulk-apply refresh). Anything that changes *which subplot or sheet* is active
  (`on_subplot_selection_changed`, `on_sheet_tab_changed`, `on_grid_changed`, `_on_strip_selection`)
  must pass `keep_selection=False` — otherwise a coincidentally-same row index in the
  newly-active (unrelated) subplot gets carried over and silently pops the inspector to
  "Trace Style" (`_on_subplot_trace_selection`).
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
