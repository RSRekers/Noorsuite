---
name: noorsuite
description: Insert datasets (DataFrames, CSV/Excel files, arrays, images) into a NOORSUITE research-plotting project and plot them on sheets/subplots - works with the NoorSuite GUI running (live) or not running (writes the project/session file). Use when the user wants to put data into NoorSuite / NOORSUITE / SciSuite, plot or organize research data there, or mentions .sciproj files.
---

# NOORSUITE

NoorSuite is a PyQt6 + matplotlib app for organizing and re-plotting research datasets. A
*data object* is one named table of numeric columns (one DataFrame); a *trace* plots an
x/y column pair from a data object onto a subplot of a *sheet*. Images are named ND arrays.

All commands use the interpreter NoorSuite is installed in:

    PY = {{PYTHON}}

## 1. Keep it current (do this first, every time)

```bash
{{PYTHON}} -m NoorSuite skill ensure
```

Quiet and throttled (checks GitHub at most once a day, upgrades the package from
`github.com/RSRekers/Noorsuite` if there is a newer commit, refreshes this skill file). If it
reports a pip failure, continue with the installed version and tell the user.
`{{PYTHON}} -m NoorSuite skill status` shows version, paths, and whether the GUI is running.

## 2. Insert data - `connect()` picks the route

```python
from NoorSuite import connect
suite = connect()            # GUI running  -> live insert, appears immediately
                             # GUI not running -> writes ~/.scisuite_session.json,
                             #                    which the GUI loads on its next start
suite = connect(project=r"C:\path\to\study.sciproj")   # offline: edit a specific project file
```

`connect()` returns the same client API in both cases:

```python
suite.push_dataframe(df, name="IV_sweep", tags=["run_4"], units={"I": "mA"})  # one data object
suite.push_dataframe(df, name="IV_sweep", mode="update")   # refresh in place (traces re-render)
suite.push_image(arr, name="z_stack", axis_names=["z", "y", "x"])
suite.plot("IV_sweep", x="t", y=["I", "V"], new_sheet=True)   # x=None -> row index
suite.plot("IV_sweep", x="t", y="I", sheet="Overview", subplot=1)
suite.plot("IV_sweep", x="V", y="I", new_sheet=True, plot_type="Scatter",   # trace look:
           style={"color": "#d62728", "marker_size": 5}, sort=True)         # plot_type, style, sort
suite.show_image(arr, name="z_stack", axes=(1, 2), new_sheet=True)
suite.list_data(); suite.list_sheets(); suite.list_traces(); suite.list_images()
df = suite.get_data("IV_sweep")                  # pull, then edit and push back:
with suite.edit_data("IV_sweep") as df: df["P"] = df["I"] * df["V"]
suite.remove_data("IV_sweep")                    # data / images by id or name (list ok); traces using it go too
suite.remove_sheet("Overview")                   # sheet by id or name (list ok): tab + tree entry, data stays
```
Deleting is irreversible and only for things the user asked to delete. A name shared by several objects raises
`ValueError` and deletes nothing: look at `list_sheets()` / `list_data()` and pass the id.

### Organize as you insert (always do this - the user searches by name/tag/notes)

Every insertion should leave the project self-explanatory:
- **Data object `name`**: short, specific, human-readable - what was measured and the condition,
  e.g. `"IV sweep - sample A3, 4K"`, not `"df"`, `"data1"` or a file name like `"run_0042.csv"`.
  Unique per dataset (pushing an existing name needs `mode="update"`).
- **`tags`**: 3-6 lowercase keywords: kind of measurement (`iv`, `xrd`), sample/material, key
  condition (`4k`), campaign/date (`2026-10`). Reuse tags already in the project: call
  `list_data()` / `list_sheets()` first and match their spelling.
- **`notes`**: 1-3 sentences - what the data is, instrument/source file, units, any processing
  (`notes="Keithley 2400, source file run_0042.csv, current in mA, background subtracted"`).
  Put `units={"I": "mA"}` on `push_dataframe` too.
- **Sheet `title`**: says what the plot shows (`"IV curves vs temperature"`), not `"Sheet 3"`.
  Give the subplot a `subplot_title` and axis labels with units (`x_label="Time (s)"`,
  `y_label="Current (mA)"`) - the GUI otherwise falls back to bare column names.
- **`folder`**: group related sheets by project/sample/campaign, e.g. `"Sample A3/Transport"`
  (`/` nests; created if missing; reuse an existing folder name from `list_sheets()["folder"]`).
- **Grouping**: related traces that share axes/units go on one subplot; different quantities
  or very different scales get their own subplot (`suite.organize_sheet(sid, ...)` for an
  existing sheet; a sheet grid is changed in the GUI).

```python
suite.push_dataframe(df, name="IV sweep - A3, 4K", tags=["iv", "a3", "4k"],
                     units={"I": "mA", "V": "V"}, notes="Keithley 2400, run_0042.csv")
suite.plot("IV sweep - A3, 4K", x="V", y="I", new_sheet=True,
           title="IV curve, sample A3 @ 4 K", tags=["iv", "a3"], folder="Sample A3/Transport",
           notes="Hysteresis visible above 0.8 V.", subplot_title="Current vs voltage",
           x_label="Voltage (V)", y_label="Current (mA)")
suite.organize_sheet("IV curve, sample A3 @ 4 K", tags=["draft"], notes="Checked 2026-10-01")
suite.annotate_data("IV sweep - A3, 4K", tags=["verified"])
```

**Trace look** (`plot(...)`, applied to every trace the call adds): `plot_type` is one of
`"Line"` (default), `"Scatter"`, `"Line+Scatter"`, `"Step"`, `"Bar"` - use `"Scatter"` for
measured points without an implied curve, `"Line+Scatter"` for a few points on a trend, `"Bar"`
for counts/histograms. `style={...}` takes `color`, `line_style`, `line_width`, `marker`,
`marker_size`, `alpha`, `edge_color`. **`sort=True` draws the points in ascending-x order** - set it
when x is disordered (check `df[x].is_monotonic_increasing`) and the quantity is a single-valued
function of x, otherwise the line zig-zags. Do NOT sort loops, hysteresis, IV sweeps that go
up-and-down, or parametric/trajectory data: there the point order *is* the curve.

### Heatmaps (2-D grids with real axis values)

A *heatmap* is an image whose cells sit at real x / y coordinates (a measured map, a
parameter sweep, a correlation matrix). Rows = y, columns = x.

```python
suite.heatmap(df_grid,                    # DataFrame: index = y values, columns = x values
              name="Conductivity map - A3, 4K", new_sheet=True,
              title="Conductivity vs T and f", folder="Sample A3/Maps",
              x_label="Frequency (Hz)", y_label="Temperature (K)",
              cmap="RdBu", reverse_cmap=True, vmin=-10, vmax=40,
              boundaries=[-10, 0, 10, 20, 30, 40],   # discrete colour bands (or bins=5)
              isolines=True, iso_above=10, iso_labels=True,   # lines only at levels >= 10
              xticks=2, yticks="data")                # every 2nd x value / every y value
suite.push_heatmap(z2d, x=freqs, y=temps, name="...", x_name="f (Hz)", y_name="T (K)")
suite.push_heatmap_xyz(long_df, "f", "T", "sigma", name="...")   # long format -> grid (mean per cell)
suite.show_image("...", style={"cmap": "magma", "iso_show": True})   # restyle / place an existing one
```

- **Colours**: `cmap` is any matplotlib name; `reverse_cmap`; `vmin`/`vmax`. **Discrete**: `bins=N`
  equal bands, or `boundaries=[...]` for your own band edges (one colour per band, colourbar shows bands).
  Use discrete colours for classes/thresholds (pass/fail, ranges); continuous for measured fields.
- **Isolines**: `isolines=True` (automatic), an int (about that many) or a list of levels. `iso_above` /
  `iso_below` keep only levels in that range ("only show lines over X"). With discrete colours and no
  explicit levels the lines follow the band edges. `iso_labels=True` prints the value on the lines.
- **Ticks**: `xticks`/`yticks` = `"data"` (a tick at every coordinate - good for few categories), an int
  N (every Nth coordinate - for dense grids), or a list of positions. Categorical axes (string labels,
  e.g. a correlation matrix from a DataFrame with string index) automatically tick at every label.
- Always set `x_label`/`y_label` with units and name the heatmap by what it shows. y points up for
  numeric coordinates; set the y limits reversed in the GUI to flip.

### Custom tick labels (any plot or heatmap)

`x_tick_labels` / `y_tick_labels` replace the numeric tick text with your own strings - on
`plot(...)`, `heatmap(...)`, `show_image(...)` and `organize_sheet(...)`:

```python
# value -> text: ticks appear at those x values
suite.plot("runs", x="cond", y="R", new_sheet=True,
           x_tick_labels={0: "RT", 1: "77 K", 2: "4 K", 3: "1.5 K"})
suite.plot("runs", x="cond", y="R", x_tick_labels=["RT", "77 K", "4 K", "1.5 K"])  # list -> 0, 1, 2, 3
# categorical heatmap axes (string labels): key = existing category -> display name
suite.heatmap(corr_df, name="Correlation matrix", cmap="RdBu", vmin=-1, vmax=1,
              x_tick_labels={"sig_S_cm": "sigma", "f_eff": "f_eff (Hz)"},
              y_tick_labels={"sig_S_cm": "sigma", "f_eff": "f_eff (Hz)"})
suite.organize_sheet("Correlation matrix", x_tick_labels={...})   # change them later
```

Use it when an axis is a *condition/category index* (0, 1, 2 meaning temperatures, samples,
batches): encode the condition as a number column, plot against it, and map the numbers to readable
text. Keys are data coordinates; the entries are stored as `"0=RT; 1=77 K"` (so labels can't contain
`;`). A string-labelled heatmap axis shows one tick per label by itself; the mapping only renames.
In the GUI: Axes tab -> "X / Y tick labels" (same `value=text; value=text` syntax).

### Presentations (Beamer)

`suite.push_document("Talk", frames_text, template="beamer", title="...", authors=["..."])` makes a presentation
(`template="user:<name>"` for an imported corporate PowerPoint template; `suite.list_templates()` lists them).
Write frames: `\begin{frame}{Title} ... \end{frame}`; a figure frame is
`\begin{frame}{Title}\centering\noorsheet[width=0.85\textwidth]{<sheet>}\end{frame}`; footer line
`\framesource{Source: ...}`. The user imports PowerPoint templates in the GUI (*New presentation* ->
*Import PowerPoint template...*) or `python -m NoorSuite presentation import file.pptx`.

Layouts for presentations: `"beamer"` (plain), `"beamer-clean"` (NoorSuite's neutral theme), `"user:<name>"`
(imported corporate templates, kept in the user's home folder - never put them into a repository).

Slide objects (tell the user): the document tab's *Slide editor* places and edits free objects on a slide
(rectangle, ellipse, line, arrow, text, graphs, SVG / pictures) as `\noorobj[x=,y=,w=,h=,angle=,fill=,...]{kind}{content}`
lines (cm from the top-left); *Slide setup...* writes `\noorslide{title-dx=,title-size=,body-top=,margin-left=,
number-on-title=true,...}`. Figures in any document take `[width=,angle=,xshift=,yshift=]`.

Error bars / bands on any plot: `suite.plot("d", "x", "y", yerr="y_std")` (symmetric column), `yerr_low="lo", yerr_high="hi"`
(asymmetric offsets), several y columns -> `yerr={"y1": "e1", "y2": "e2"}` or a list in y order, `band=True` adds a shaded band of the
same error (`band_alpha`), `errorbars=False` hides the whiskers, `yerr_mode="std"|"sem"|"minmax"`; with a DataFrame the error may be an array.
A column that does not exist raises `ValueError` listing the available ones. CLI: `insert data.csv --plot x:y1,y2 --yerr e1,e2` (or
`--yerr y1=e1,y2=e2`), `--yerr-low/--yerr-high`, `--yerr-mode`, `--band --band-alpha 0.3`, `--no-errorbars`.

Mean with band: `suite.plot_mean(["run1","run2"], "x", "y", band="std"|"sem"|"minmax"|None, errorbars=False, new_sheet=True,
title=..., label=..., style={"color": ...})` (several data objects, one y column) or `suite.plot_mean("wide", "t", ["a","b","c"], ...)`
(one object, repeat columns) pushes a derived object "<label> (n=.. mean±err)" (x, mean, std, sem, err_min, err_max) and plots it
as one trace with a translucent band (`band_alpha`). In the GUI the same is *Combine into mean ± error trace...*; the Trace Style tab
switches band / bars / source afterwards.

Defaults (tell the user): new subplots use 13 pt ticks / 15 pt axis labels / 16 pt title and real point sizes
(`SheetModel.fig_real_points`); saved sheets keep theirs. The window fits the screen; F4 toggles the plot sidebar.

Editor help (tell the user): the LaTeX editor autocompletes commands / environments / cite keys / labels / sheet keys
(Tab fills, arrows choose), wraps selected text in brackets / quotes / `$`, closes brackets, adds `\end{env}` after Enter, and
pastes clipboard pictures (document: figure; presentation: movable slide object). Sheets have one physical size on screen /
PDF / PNG; *Fonts in real points* (Figure tab) makes point sizes literal. Large projects are stored as binary sidecars
(`<project>/_data`, `_files`); push numpy arrays (the client already does).

Master layout (tell the user): in the slide editor pick *Master layout (all slides)* to drag the title / body / number /
source placeholders and add logos or decorations that appear on every slide.

Presenter view (tell the user): Shift+F11 in the document tab = console with current / next slide, notes
(`\note{..}`), timer + target time, laser pointer (L); the audience view goes full screen on a second monitor.
Double-click in the PDF preview jumps to the matching line of the text (SyncTeX).

Effects (tell the user): Beamer overlays (`\pause`, `\item<2->`, `[<+->]`, `\uncover<n->{..}`, `\only<n>{..}`,
`\setbeamercovered{transparent}`) and `\transfade` / `\transdissolve` / `\transwipe` are supported; the document
toolbar's *Effects* menu inserts them, the PowerPoint export turns them into click animations (`--effect
appear|fade|wipe`) and slide transitions. The LaTeX editor has Ctrl+F find / Ctrl+H replace.

Running / exporting a talk (tell the user): *Present* (F11) in the document tab runs the PDF full screen;
*Export PowerPoint...* or `{{PYTHON}} -m NoorSuite latex pptx --doc "Talk" --out talk.pptx` makes an editable .pptx
(needs `python-pptx`; the imported corporate template's master / layouts are reused); *Export LaTeX folder...*
(or `latex export --folder DIR`) gives a folder that compiles anywhere with `xelatex main.tex`.

### Author lists and Supporting Information

With a layout template the author list is structured: `suite.push_document("Paper", body, template="acs",
title="...", authors=[{"name": "A. One", "affils": ["1"], "email": "a@b.org", "corresponding": True},
{"name": "B. Two", "affils": ["1", "2"]}], affiliations={"1": "Univ A, City", "2": "Institute B, Town"})` - it is
written in the layout's convention (authblk / achemso / elsarticle / RSC). Hand-written `authblk` front matter
(`\author[1,2]{Name}`, `\affil[1]{University}`) is understood in every layout and converted when the user
switches layout; in ACS / Elsevier classes the built copy converts it automatically (never mix `authblk` into
those classes by hand). `suite.push_si_document("Paper")` creates "Paper - SI" (same layout and authors,
S-numbered counters).

### Tables from data objects

`suite.push_table("IV summary", "IV A3", columns=["V", ("I", "Current (mA)"), {"col": "R", "fmt": "sci",
"digits": 2, "align": "r", "scale": 1e-3}], rows=(1, 20), sort_by="V", max_rows=10)` defines a live table;
`suite.table_snippet("IV summary", caption="...")` is the `table` environment to put into a document with
`push_document(..., mode="append")` (it contains `\noortable{iv-summary}`). Numbers use math minus signs, NaN
prints `--`; headers default to column name + unit. `suite.list_tables()` lists them.

### Journal layouts

`suite.push_document("Paper", body_text, template="acs", title="...", authors=["A. One", "B. Two"],
abstract="...")` - `template` is `"acs"` (achemso), `"rsc"`, `"elsevier"` (elsarticle) or `"article"`.
Body text only gets wrapped in the template; a complete document is switched to the template's preamble
with its text kept. The user switches layouts later with the *Layout* box of the document tab.

The user can save their own layouts (document toolbar -> *Save layout...*); `suite.list_templates()` lists the ids
(`user:<name>`) next to the built-ins - use them as `template=` too.

### Word / LibreOffice export of a document

`python -m NoorSuite latex docx --doc "Paper" --out paper.docx` (or `odt`; `--project file.sciproj` to
use a project file instead of the GUI session). Needs pandoc (`conda install -n noorlatex -c conda-forge
pandoc`); the user's GUI also has *Export Word...* / *Export LibreOffice...* buttons in the document tab.

### Highlights, arrows, text and pictures on a plot

`add_shape(sheet, kind, ...)` draws an object on a subplot of an existing sheet. Objects share one
back-to-front layer list with the traces in the GUI (move to back / front, per-object edit dialog):

```python
sid = "IV curve"                                     # sheet id or name
suite.add_shape(sid, "xspan", x0=0.8, x1=1.2, fill="#ffcc00", fill_alpha=0.25,
                name="Switching window", position="back")          # highlight band behind the data
suite.add_shape(sid, "rect", x0=0, x1=1, y0=0, y1=5, edge_color="#000000",
                fill="linear", colors=["#ffffff", "#d62728"], gradient_angle=90)  # gradient fill
suite.add_shape(sid, "ellipse", x0=2, x1=3, y0=1, y1=2, fill="radial",
                stops=[(0, "#ffffff", 0.0), (0.5, "#4477aa", 0.6), (1, "#4477aa", 1.0)])  # (pos, colour, opacity)
suite.add_shape(sid, "arrow", x0=1, y0=3, x1=2, y1=1.5, edge_color="#000000", arrow_head="-|>")
suite.add_shape(sid, "text", x0=2.5, y0=4, text="set", fontsize=9)
suite.add_shape(sid, "rect", coords="axes", x0=0.05, x1=0.3, y0=0.7, y1=0.95, cmap="viridis", fill_alpha=0.8)      # cmap=<matplotlib name> -> gradient through 5 key colours
suite.clear_shapes(sid)                              # remove all objects of a subplot
```

Hierarchy / size / bare panels: `add_shape(..., parent="frame")` makes an object move, rotate and scale
along with another one (its root); `scale=1.5` (or `scale_x` / `scale_y`) multiplies the size around the
centre; `suite.organize_sheet(sid, content_only=True)` (also an argument of `show_image`) hides axes, ticks,
labels, title, legend and background of the subplot so only an image / picture + objects remain.

Kinds: `rect`, `ellipse`, `line`, `arrow`, `xspan` (vertical band), `yspan` (horizontal band), `text`,
`picture`. Coordinates are data units; `coords="axes"` = 0..1 of the panel. `fill` is a colour, or
`"linear"` / `"radial"` with `colors=[...]` (evenly spaced key colours), `stops=[(pos, colour, opacity)]`
or `cmap=`. Pictures: `suite.push_asset("logo.svg", name="logo")` (file path, or bytes + `ext=`), then
`suite.add_shape(sid, "picture", asset="logo", x0=.., x1=.., y0=.., y1=..)`; in a LaTeX document the same
file is `\noorfile[width=6cm]{logo}` (SVG stays vector; the user double-clicks it in the preview to
edit it in Inkscape and it reloads on save).

### LaTeX articles with live figures

The project can hold LaTeX documents. Figures are *sheets*: `\noorsheet{<sheet id>}` includes a sheet
as a vector PDF at the sheet's own size (set it in the GUI Figure tab, e.g. 8.5 x 6 cm for one journal
column; default 12 x 8 cm), so figure text has its printed size. In the GUI a **Document tab** (tree ->
"New LaTeX Document") shows source + live PDF: double-click a figure to jump to its sheet; edits to the
sheet recompile the preview; "Export PDF" / "Export LaTeX folder" deliver the result.

```python
sheets = suite.list_sheets()                             # find the sheet ids / names
suite.push_document("Transport paper", r"""\documentclass{article}
\usepackage{hyperref}
\usepackage{noorsuite}      % provides \noorsheet (load it after hyperref)
\begin{document}
\section{Results}
""" + suite.figure_snippet("IV curve, sample A3 @ 4 K", caption="Current vs voltage at 4 K.")
    + "\n\\end{document}\n", folder="Papers")
suite.push_document("Transport paper", r"\section{Discussion} ...", mode="append")  # add text later
suite.get_document("Transport paper")                    # read the source back
```

**Citations (Zotero)**: references live in the project, not in a hand-maintained `.bib`. The user picks
items in Zotero's own popup (Zotero + Better BibTeX running; the plugin's *library export* is not needed):

```python
suite.zotero_search("battery cathode 2021")   # DataFrame key/label/year/doi from the WHOLE library
suite.zotero_add(["huangSolidStateBatteryCathode2021"])   # store by citation key (no picker needed)
keys = suite.zotero_pick()            # or: Zotero's picker window, waits for the user; returns cite keys
suite.push_references(csl_items)      # or add CSL-JSON / Zotero-JSON dicts you already have
suite.list_references()               # key, "Smith et al. (2020) - Title", zotero item key
text = r"As shown before \cite{%s}." % keys[0]      # \citep / \citet need \usepackage{natbib}
```

Citations are **numbered in order of first appearance** and the reference list follows that order. The
default format is **`acs-guide`**: NoorSuite itself writes superscript numbers and an ACS Guide (April 2025
revision) formatted list - authors `Smith, J. A.; Lee, K.`, bold year, italic volume, `DOI:` as a hyperlink,
journal names abbreviated only if every cited journal has an abbreviation. Other formats: `acs` (older
achemso.bst), `acs-notitle`, `plain`, `unsrt`, `ieeetr`, `abbrv`, `alpha`, `plainnat`, `abbrvnat`, `unsrtnat`,
`apalike` (`latex.set_bib_style(id)` sets the user default; a document keeps its own choice). You do not have to
write the reference list: put `\bibliography{references}` where it should go, or leave it out and the build adds
the list before `\end{document}` of its *built copy*. Write plain `\cite{key}`; never type numbers yourself.
`references.bib` is generated at build time from the stored items, only for the keys that are cited.
A citation showing `[?]` / `?` means exactly this missing reference list or an unknown key. In the GUI: **Cite (Zotero)...**
(Ctrl+Shift+Z) in the Document tab, and the References tab (cite / link / reference-list format). Items cited
with a key that is not in the project are looked up in Zotero automatically while building (Zotero running,
Better BibTeX installed); only keys that exist nowhere stay `?` and are listed as "unknown citation(s)".
Use real citation keys from `zotero_search` - never invent a key. Tell the user
when Zotero is not reachable instead of inventing references - never fabricate bibliographic data.

Build a PDF without the GUI: `{{PYTHON}} -m NoorSuite latex build --project study.sciproj --doc "Transport paper" --out paper.pdf`
(`latex export --folder DIR` writes main.tex + noorsuite.sty + figure PDFs; `latex engine` shows which
engine is used - tectonic / latexmk / xelatex / pdflatex). If no engine is installed, say so; the document
is still saved and can be built later. Reference figures by sheet **id** (stable across renames), keep one
`\begin{figure}` per sheet with a caption that says what is shown (with units), and remember that
`mode="append"` goes *after* `\end{document}` if you already closed it - write the whole body in one call
or use `replace`.

Rules that matter:
- Only **numeric** columns are kept (non-numeric are dropped); a *named numeric index* becomes a column.
- Data objects are matched by **name**; pushing an existing name with `mode="new"` makes a duplicate - use `mode="update"` to refresh.
- Run `suite.list_sheets()` first if you target `sheet=` by name: duplicate names resolve to the first match (use the id).
- **Never edit a `.sciproj`/session file by hand or while the GUI has it open** - go through `connect()`. With a GUI running it always inserts live; an offline write to a project the GUI also has open would be overwritten by the GUI's autosave.
- With the GUI running, mutations are fire-and-forget (no error comes back); verify with `suite.list_data()` / `list_traces()`. Offline calls validate and return `{"status": "error", ...}` on a missing data object or column.
- Start the GUI only if the user asks to see it: `{{PYTHON}} -m NoorSuite --gui 55555` (background process; needs a display).

## 3. One-shot CLI (no Python code needed)

```bash
{{PYTHON}} -m NoorSuite insert data.csv --name "IV sweep - A3, 4K" --tags iv,a3,4k --notes "Keithley 2400" \n    --plot V:I --new-sheet --title "IV curve, sample A3" --folder "Sample A3/Transport" \n    --x-label "Voltage (V)" --y-label "Current (mA)"
{{PYTHON}} -m NoorSuite insert scan.npy --name z_stack --axes z,y,x --plot-image
{{PYTHON}} -m NoorSuite insert results.xlsx --sheet-name Sheet2 --project study.sciproj
```

Reads `.csv/.tsv/.txt/.xlsx/.json/.parquet` (DataFrame) and `.npy` (image). `--plot x:y1,y2`
(use `:y` or `index:y` for the row index), `--sheet NAME|ID`, `--new-sheet`, `--subplot N`,
`--title/--sheet-tags/--sheet-notes/--folder/--subplot-title/--x-label/--y-label` (sheet
organization), `--notes` (data), `--plot-type Scatter`, `--sort`, `--x-tick-labels "0=RT; 1=4 K"` / `--y-tick-labels`, `--update`, `--project FILE`, `--port N`.

### `api`: every other action from the command line

`{{PYTHON}} -m NoorSuite api <command>` mirrors the client (live GUI if one answers on `--port`, else the project file):
`list data|images|traces|sheets|documents|assets|tables|references|templates [--json]`, `get DATA [--out f.csv]`,
`remove data|sheet KEY...`, `clear --yes`, `annotate DATA --tags --notes`, `organize SHEET --title --tags --notes --folder
--subplot-title --x-label ...`, `shape SHEET rect|ellipse|line|arrow|xspan|yspan|text|picture --x0 --x1 --y0 --y1 --fill
--text --parent --scale ...`, `clear-shapes SHEET`, `heatmap grid.csv|grid.npy [--xyz X,Y,Z] --cmap --bins --isolines --xticks ...`,
`mean DATA --x x --y r1,r2,r3 --band std|sem|minmax --errorbars`, `document NAME --file f.tex|--text ... [--mode append --template acs]`,
`get-document KEY`, `si-document MAIN`, `asset FILE`, `table NAME DATA --columns a,b --rows 1:20`, `snippet figure|table KEY --caption`,
`references --file x.bib | --zotero-keys k1,k2 | --zotero-search Q | --zotero-pick`. Failures print `<command> failed: ...` and exit 1.

## 4. After inserting

Tell the user where the data went (live GUI vs. which file) and what exists now
(`list_data()` / `list_sheets()` output, trimmed). Styling, tagging, folders and export are done
in the GUI (double-click traces/axes); this skill only inserts and arranges data.
