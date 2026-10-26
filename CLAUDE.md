# CLAUDE.md

Guidance for Claude Code (claude.ai/code) in this repository.

## What this is

NoorSuite (product **NOORSUITE**; class names still say `SciSuite*`) is a PyQt6 + matplotlib desktop tool
to organize and re-plot research datasets and to write LaTeX papers / Beamer talks around them. Two
cooperating processes talk over loopback TCP (default port 55555):

- **Client side** (`NoorSuite.client.SciSuiteClient`, Jupyter / scripts / CLI): push DataFrames
  (each becomes one *data object*), plot them, restyle, organize, export. Never pulls styles back.
- **GUI side** (`python -m NoorSuite --gui [port]`): pick columns, place traces on sheets / subplots,
  style them (inspector or double-click), organize in folders, save `.sciproj`, write documents / slides.

## Ground rules (read first)

1. **Everything the GUI can do must be available from the client AND the CLI, and be testable headless.**
   A new GUI feature is not done until it has: (a) a model-level implementation that is Qt-free where
   possible, shared by GUI and offline backend; (b) an IPC action / `ACTION_EDIT` op or an `organize`
   payload key, implemented in **both** `app.handle_incoming_ipc` and `offline.OfflineBackend`
   (re-export new constants in `ipc.py`); (c) a `client.py` method / keyword with validation *before*
   sending (a typo raises `ValueError` in the caller; the GUI acks mutations before processing them);
   (d) an `api_cli` command or option (shared look options live in `add_axes_args` / `axes_kwargs`);
   (e) tests for GUI, offline and CLI; (f) updates to `NoorSuite/skill/SKILL.md`, `README.md` and this file.
2. **Public repo, no corporate artwork.** `*.pptx` and `*.noortheme` are git-ignored; the only bundled theme
   is the neutral `noorclean`. After every push verify `git ls-files | grep -iE "\.(pptx|noortheme)$"` is empty.
3. **Never run insertion tests against port 55555** (the user's live GUI). Use another port (`--port 59123`).
   A port already bound is *not* an error: a GUI is running, the client reconnects. Never `taskkill` all
   Python processes.
4. **Commit / push / skill update only when the user asks** ("push to github and update the skill"):
   commit, `git push`, then `~/.noorsuite/venv/Scripts/python.exe -m NoorSuite skill update`; bump the
   version in `pyproject.toml` and `NoorSuite/__init__.py` per feature round.
5. **PyQt6 aborts the process when an exception escapes a slot.** GUI entry points catch and report
   (`handle_incoming_ipc` -> status bar + `~/.noorsuite/crash.log`; `__main__` installs
   `install_crash_guards`). Keep that when adding handlers.
6. **Editing on Windows**: heredocs in the Bash tool mangle backslashes (LaTeX!). Write patch scripts /
   files with the Write or Edit tool; in tests use raw strings (`r"\noorsheet{..}"`).
7. Style: match surrounding code; defaults on every new model field; columns are replaced, never edited
   in place (caches rely on it).

## Commands

```bash
pip install -e .                                   # editable (PyQt6, matplotlib, numpy, pandas)
python -m NoorSuite --gui 55555                    # GUI (needs a display)
python -m NoorSuite PROJECT|FOLDER [--document [TPL]] [--notebook]   # open/create a project directly, no Jupyter
python -m NoorSuite insert data.csv --plot x:y ... # one-shot insertion (GUI optional)
python -m NoorSuite api <command> ...              # every client action (see api --help)
python -m NoorSuite latex build|export|engine ...  # headless documents
python -m NoorSuite skill update                   # reinstall the Claude skill / upgrade the package
pytest                                             # ~320 tests, ~9 min
QT_QPA_PLATFORM=offscreen MPLBACKEND=Agg pytest tests/test_model.py::test_x   # single test, headless
```

Machine: no `python` on PATH; the anaconda env `developmentstuff` has PyQt6 + pytest
(`C:/Users/Rene/anaconda3/envs/developmentstuff/python.exe`). Pure-logic tests (`test_model`,
`test_transforms`, `test_client`, `test_protocol`, `test_slidetext`) need only numpy + pandas. LaTeX engine:
tectonic in conda env `noorlatex`. `noorgraph.ipynb` is the manual harness; restart the GUI after code changes.

## Architecture (`NoorSuite/`)

| Module | Role |
| --- | --- |
| `model.py` | All models (`DataObject`, `ImageObject`, `TraceRef`, `ImageRef`, `ShapeRef`, `SubplotModel`, `SheetModel`, `ColorMap`, `AssetModel`, `TableModel`, `DocumentModel`, `ProjectModel`), option vocabularies, `apply_y_transform`, aggregation, styling validators (`check_subplot_style`, `check_sheet_style`), tree helpers. numpy only. |
| `edits.py` | Qt-free edits of existing things shared by GUI / offline: trace style / remove / move / combine, colormaps, `check_edit`. |
| `protocol.py` / `ipc.py` | Wire protocol (8-byte length + pickle, action names, `IPCClient`) / `IPCBridge` server (Qt). `ipc.py` re-exports every constant explicitly. |
| `shortcut.py` | `noorsuite shortcut`: `.ico` from the app icon + Desktop / Start-menu `.lnk` (pip's exe stub cannot carry an icon). |
| `newproject.py` | Qt-free `create_project(parent, name, folders)`: own directory + `.sciproj` + empty tree folders (GUI *New Project*, `client.new_project`, `api new-project`). |
| `notebook.py` | Qt-free starter `.ipynb` next to a project (`project_for`, `create_notebook`, `jupyter_command`); Jupyter stays optional (GUI button, `--notebook`, `client.new_notebook`, `api notebook`). |
| `client.py` / `offline.py` | `SciSuiteClient` API; `OfflineBackend` applies the *same payloads* to a `.sciproj` file when no GUI runs (`connect()` picks). |
| `api_cli.py` / `insert_cli.py` | CLI: `api <command>` (all actions) / `insert FILE` (+ the same look options). |
| `app.py` | `SciSuiteWindow`, panels, tabs, inspector, save / load, IPC intake, warm-up. |
| `sheet.py` | `PlotSheet` render engine, hit-testing, exports (`export_sheet_pdf/png/raster/file`). |
| `dialogs.py`, `widgets.py` | Style widgets + dialogs (`TraceStyleWidget`, `AxesStyleWidget`, `ImageStyleWidget`, `FigureStyleWidget`, `ColormapPanel`), `CollapsibleSection`. |
| `colormap.py`, `fuzzy.py` | Colormap sampling / `.scicmap` / custom colormaps for images; fuzzy + smart search. |
| `latex.py`, `latex_build.py`, `docview.py`, `templates.py`, `refstyle.py`, `zotero.py`, `tables.py`, `docexport.py` | Documents: build, preview / editor tab, journal templates, bibliography, Zotero, tables, Word export. |
| `pptximport.py`, `pptxexport.py`, `presenter.py`, `synctex.py`, `slideobjects.py`, `slidetext.py`, `slidecanvas.py`, `slideeditor.py` | Presentations: theme import / PowerPoint export, presenter console, SyncTeX, slide editor. |
| `latexcomplete.py`, `latexeditor.py`, `findbar.py`, `textformat.py`, `figureopts.py` | Editor assist. |
| `skill.py`, `skill/SKILL.md` | Claude skill install / update (`{{PYTHON}}` is substituted). |

## Data model (project JSON v6, optional keys need no version bump)

- `DataObject` = named bag of columns; name is the stable pointer (`update_from` refreshes in place, bumps `_rev`).
  `ImageObject` = ND array + `axis_names` (+ `axis_coords` = heatmap); arrays go to sidecar files.
- `TraceRef(data_id, x_col, y_col)` + style + `enabled`; `x_col in ("", INDEX_COL)` = row index.
  `scale_factor` = `Y_TRANSFORMS` token, `custom:<x>`, `relval:<frac|pct>:<ref>` or `relidx:<mode>:<n>`.
- `ImageRef` (`SubplotModel.image`): `display_axes`, `slice_axis`, `index`, cmap (matplotlib name **or a custom
  colormap**), vmin / vmax, interpolation, origin, aspect (`equal` default; heatmaps with coordinates `auto`),
  colorbar, heatmap fields (`use_coords`, bins / boundaries, isolines, tick modes). `STYLE_FIELDS` = settable fields.
- `SubplotModel._FIELDS` (scales, grid, tick formats / labels, limits, fonts, spines, legend, `bare` ...) and
  `SheetModel` (`rows`, `cols`, `subplots` in grid order, figure fields `_FIG_FIELDS` incl. `fig_width_cm /
  fig_height_cm`, `fig_real_points`, **joint legend** fields, `tags`, `notes`, per-sheet `colormaps`, `active_colormap`).
- `ProjectModel` is the only serialization root (`.sciproj` and `~/.scisuite_session.json`).
- IDs are 8-char uuid slices. Data objects are matched by **name** for `push_dataframe(mode="update")`,
  `plot(name, ...)`, `remove_data` (id also accepted). Deleting a data object removes its traces.
- `SubplotModel.stack()` interleaves traces and shapes (z-order, legend order); GUI layer-list `UserRole` indexes
  `stack()`, never `traces`.

## Cross-cutting mechanisms

- **IPC.** `IPCBridge._listen_loop` only `accept()`s; each connection gets its own daemon thread (`_serve`, 60 s
  timeout). *Mutations* are re-emitted as the `data_received` Qt signal and handled on the GUI thread by
  `handle_incoming_ipc` -> `_deferred_renders()` -> `_dispatch_ipc`; they are **acked before processing**, so
  failures only reach the status bar (`_say`) -> validate in the client first. *Queries* are answered from
  `IPCBridge.snapshot` (replaced wholesale, never mutated). `_refresh_ipc_snapshot()` after any change;
  `_refresh_ipc_data()` only when data objects / images change. `protocol._recv_exactly` reads 1 MB chunks and
  `IPCClient.send` scales its timeout with the payload.
- **Offline.** `connect()` -> live client if a GUI answers, else `OfflineClient` (default file = session file the
  GUI loads at start). Headless export needs a `QApplication` (offscreen), created on demand.
- **Rendering** is full clear-and-rebuild (`PlotSheet.render`: `fig.clf()`, per-subplot axes, `artist_map` for
  hit-testing, `_text_targets`). One physical figure size: `sheet.logical_size_cm(model)` = own cm size, else
  `latex.default_size_cm(rows, cols)` (12 x 8 cm for one panel, +75 % per extra row / column);
  `_sync_zoom_dpi` sets `fig.dpi = canvas px / width` (never `100 * zoom`, never set dpi elsewhere); fonts are
  real points when `fig_real_points` (new sheets; `from_dict` defaults False for old files, which keep the scaled
  `text_scale`). Resizes re-run `_layout()` (`tight_layout`, with a rect that frees the joint-legend strip).
  The dashed active-subplot cue is a figure-level patch placed in `_on_draw` (never from `render`).
- **Joint legend** (`SheetModel.joint_legend` bottom / top / right / left, `joint_legend_ncol`, `_fontsize`,
  `_frame`): `PlotSheet._make_joint_legend` lists every distinct trace label once; per-panel legends are not
  drawn while it is on.
- **Display / export.** `_AspectCanvasHost` letterboxes to the figure ratio; zoom by Ctrl+wheel. Exports go through
  `PlotSheet.savefig_export` (WYSIWYG or exact cm size, no active-subplot cue); `export_sheet_file` writes png /
  pdf (`pdf.fonttype 42`) / svg by suffix.
- **Style widgets** live once in `dialogs.py`, embedded in the inspector and the pop-up dialogs; style changes go
  through `_FIELDS` / `_STYLE_FIELDS` tuples (drive (de)serialization and the Cancel snapshot). Add a field =
  default + tuple + control + CLI / client option.
- **Image slider bar**, **error bars as an overlay** (`show_errorbar` / `show_band`, `yerr_mode` std / sem / minmax,
  `_draw_error_overlay`, `_draw_band`), **relative Y transforms** (`_yerr_scale` keeps errors consistent),
  **combine into mean +- error** (`edits.combine_traces`, GUI dialogs `CombineTracesDialog` /
  `SplitAggregateDialog`, client `combine_traces` / `plot_mean`, CLI `combine`): derived data object
  `"<label> (n=N mean±err)"` with columns `x mean std sem err_min err_max`; split layouts `block` / `sequence`.
- **Heatmaps** are images with `axis_coords`; `pcolormesh` when coordinates exist, `imshow` otherwise. A plain
  image has *automatic* ticks (a label per pixel took ~10 s for 512 x 512). Custom tick labels: `"value=text; ..."`.
  Image colormaps resolve via `colormap.mpl_cmap(name, sheet)` (custom colours = continuous gradient;
  `colormap.set_library` is called by the window before every render / export; custom colours are part of
  `sheet_fingerprint`). `copy_image_style` = "apply to all images".
- **Objects (shapes)** (`ShapeRef`): kinds rect ellipse line arrow xspan yspan text picture; gradients, parent
  hierarchy (`apply_group_transform`), display-space rotation (`_DynRot`), canvas drag from the start snapshot
  (`_restore(start)` first). `bare` = content only. Assets (`AssetModel`) are SVG / PDF / PNG / JPG, edited in
  Inkscape through a watched working file.
- **Search**: `fuzzy.smart_score(query, [(kind, weight, text)])` (AND of tokens, `-x`, `#tag`, `"phrase"`);
  never one concatenated haystack + subsequence.

## UI layout (`app.py`)

Left: data pool (multi-select, "Add common column(s)") -> column picker / image axes panel -> project tree
(folders, sheets, documents; drag to reparent via `_reparent_items`, not Qt's default move). Center: tabs (sheet =
plot + slider bar + notes; document = `DocumentTab`; `current_sheet()` is None on a document tab). Right: grid
spinners, subplot strip, trace list (reorder = z-order; Combine / Normalize buttons), colormap panel, inspector tabs
(Axes / Trace Style / Figure) with broadcast buttons ("Apply to all subplots", "...all traces", "...all images").
`_sync_subplot_trace_list(keep_selection=False)` whenever the active subplot / sheet changes. Sidebar: `_right_wrap`,
F4 / `reset_layout`, never `setSizes([.., 0])`; the initial size is clamped to the screen. Explicit checkbox QSS
(Fusion does not follow dark themes). Autosave: 120 s timer, background thread (`_background_save`), synchronous
on close; `project_path` is set only by Save As / Open (or the `save_project` / `open_project` edits).

## Performance and robustness

- `prepare_save(path, cache)` / `SavePlan.run()`: data > 4096 values -> `<stem>/_data/<id>.npz`, assets ->
  `_files/`, atomic `.tmp` rename, per-path cache of `DataObject.signature()` so unchanged data is never rewritten;
  unchanged projects are not saved (`ProjectModel.signature()`).
- Hidden sheets render lazily (`_stale`, `_rendered_fp`); `_refresh_open_sheets(only_ids)`; long monotonic lines are
  min / max thinned (`thin_line`); error bars capped (`_MAX_WHISKERS`), bands via `thin_band`; digests cached for
  `sheet_fingerprint`; the client sends numpy arrays, not lists.
- **Start-up warm-up** (`start_warmup`, `_warmup_steps`): heavy imports, stale figure PDFs + thumbnails of every
  document, a once-per-session Zotero look-up (`_zotero_tried`). `DocumentTab` keeps `workdir/state.json`
  (version + `.sty` hash, `_fig_fp`, last built fingerprint; `_fingerprint` is an md5): an unchanged document is
  neither re-exported nor rebuilt. `PdfPreview` paints only pages near the viewport; document panes use `Ignored`
  size policy so splitters work on small screens.
- Build: `latex._build_tectonic_incremental` reuses aux files (`--reruns 0`, repeat until aux / out are stable;
  documents with `\bibliography{` keep the classic path). Tests: LaTeX engine tests skip without an engine.

## LaTeX documents

- `DocumentModel` in `ProjectModel.documents`; tree node `{"type": "document"}`; `DocumentTab` in the tab widget.
- Figures: `\noorsheet[opts]{key}` (key = sheet id or name slug) -> `\href{noorsuite://sheet/key}{\includegraphics}`
  from `noorsuite.sty` (written into the build dir); `\noorfile` for assets, `\noortable` for data tables. Slide
  objects `\noorobj{sheet|file}{key}` (and `\noormasterobj`) count as figure references too (`latex.figure_refs` /
  `asset_refs`). Build in `~/.noorsuite/build/<doc_id>/`, figures on the GUI thread, engine in a worker thread;
  auto-compile is debounced and keyed on fingerprints. Headless: `latex_build.py`.
- Engine order: `$NOORSUITE_LATEX`, `~/.noorsuite/config.json`, PATH, conda env `noorlatex`. tectonic needs the real
  HOME; `--only-cached` with retry online (BibTeX prints `?` for uncached `.bst`).
- **Bibliography**: default `acs-guide` = `refstyle` native numbering; BibTeX presets in `latex.BIB_STYLES`; the
  format is per document and switching must stay reversible (`MANAGED_PACKAGES`, `apply_bib_style`,
  `normalize_for_style`); `prepare_build_source` is the single entry for GUI and headless builds; never edit the
  user's source for natbib / the reference list (build copy only). Classes that bring natbib (achemso, elsarticle,
  rsc) are left alone.
- **Zotero** via Better BibTeX JSON-RPC (`zotero.rpc*`; the local `/api` is 403 by default, never use it, never
  depend on the BBT export). CAYW picker blocks -> thread. `is_complete` / `merge_record` fix thin records.
- **Templates**: `TEMPLATES` (article / acs / rsc / elsevier / beamer ...), `extract_parts`, `apply_template`,
  `compose`, user layouts `user:<slug>`; authors via `parse_authors` / `render_author_block`; `si_document`.
- **Tables**: `TableModel`; `expand_tables` rewrites `\noortable` in the build copy only.
- **Word / LibreOffice**: pandoc; figures -> PNG, bibliography -> numbered paragraphs, equations -> tables.
- **Editor**: `latexcomplete` (catalogues + snippets) / `AssistEditor` (popup, wrapping, auto-close, clipboard
  pictures -> assets), `FindBar` (Ctrl+F / Ctrl+H / F3), `figureopts` (width / angle / shift of figure calls).

## Presentations

- Beamer layouts (`templates.base_of == "beamer"`); `pptximport` converts a corporate `.pptx` into a theme in the
  user's home (never committed); `noorclean` is the bundled public theme. `pptxexport.export_pptx` (python-pptx):
  native shapes, effects (`\pause`, `<n->`, overlays -> p:timing), transitions, notes; `page_map` aligns PDF pages
  with frames. `PresentationView` / `PresenterConsole` (Shift+F11: notes, timer, laser).
- **Slide objects** (`slideobjects.py`, Qt-free) = `\noorobj[...]{kind}{arg}` lines (rect ellipse line arrow text
  sheet file); `\noorslide{...}` setup keys (title / body size, margins, slide-number placement, master placeholders
  `title-x ...`); `\noormasterobj` = master layout objects. `slidecanvas` (painter, handles, hover outline,
  `pageClicked`) + `slideeditor` (frame navigator, toolbar, side panel: "This slide", "Clicked text", object form).
  The side panel is never grayed out. Edits are minimal-diff source edits (`DocumentTab.apply_source`).
- **Slide text styling** (`slidetext.py`): `restyle_line` wraps a line's text in
  `{\fontsize{N}{1.2N}\selectfont\bfseries\itshape\color[HTML]{RRGGBB} text}` (recognised again, never nests; items,
  titles, block titles, plain lines); `set_frame_fonts` = `\noorfont{body=N}` after `\begin{frame}`;
  `\noorslide{body-size=N}` document-wide. A title size must be on the title text itself.
- **SyncTeX cannot locate lines inside a Beamer frame** (the body is typeset at `\end{frame}`), so the slide editor
  reads the visual PDF line under a click (`_pdf_line_at`) and matches it to source lines with
  `slidetext.match_pdf_text`. Elsewhere `synctex.locate` + `match_line` (difflib) maps preview clicks to source.

## Skill, CLI and API conventions

- `NoorSuite/skill/SKILL.md` is the skill; `skill.py` (stdlib only) installs it to `~/.claude/skills/noorsuite/`,
  compares `git ls-remote` HEAD with pip's `direct_url.json`, upgrades from GitHub (skipped for editable installs),
  `ensure` is throttled to once a day. Keep the skill in step with every API / CLI change.
- **Look options everywhere** (`client.plot / show_image / heatmap / organize_sheet(**axes_options, grid=, size_cm=,
  all_subplots=, joint_legend=...)`; CLI `--xscale --yscale --log --no-legend --font-size --xlim --panels --size-cm
  --all-panels --joint-legend --set FIELD=VALUE`): `model.expand_subplot_style` / `check_subplot_style` /
  `check_sheet_style`; they ride in the `organize` payload (`subplot_style`, `sheet_style`, `grid`).
  An unsized grid gets `default_size_cm`.
- **Edits of existing things** go through `ACTION_EDIT` (`op`: `trace_style remove_trace move_trace combine_traces
  new_sheet duplicate_sheet folder colormap save_project open_project`; the sub-action key is `do` because `action`
  is the IPC action) and `ACTION_EXPORT` (png / pdf / svg, atomic write, the client waits for the file). Client
  methods: `restyle_traces remove_traces move_trace combine_traces new_sheet duplicate_sheet folder add_colormap
  use_colormap save_project export_figure style_subplot style_sheet`.
- `plot(..., yerr / yerr_low / yerr_high / yerr_mode / errorbars / band / band_alpha)` resolve into per-y
  `trace_styles` (`_error_styles`); missing error columns raise. `plot_mean` pushes a derived mean object.
- Mutation acks are immediate; `_handle_add_to_sheet` reports unresolved data / sheet / subplot in the status bar.
- `list_sheets` flags `duplicate_name`; `sheet=<name>` resolves id first, then the first name match.
- Client round trip is pull-edit-push (`get_data`, `edit_data`), never automatic.

## Gotchas

- `apply_y_transform` matches `"1e-3"` before `"1e3"` on purpose; `apply_numeric_expr(cur, "-0.5")` means
  *subtract*.
- `_sync_subplot_trace_list(keep_selection=True)` re-selects by index: only valid when the list kept its identity.
- Closing a sheet tab does not delete the sheet. Branding lives in `app.py` (`APP_NAME`, `APP_ID`, icon).
- `ColormapPanel` talks to the window through a host protocol (`active_sheet_model`, `available_colormap_names`,
  `resolve_colormap`, `sample_colors`, `selected_subplots`, `copy_colormap_to_library`, `export_colormap`,
  `import_colormap`); only a sheet's own colormaps are deletable there.
- Exceptions in Qt slots abort the app: wrap and report. File-system paths: project assets live in `<stem>/`
  next to the `.sciproj` (managed folder; stale files are swept).
