# NOORSUITE

A scientific plotting suite for data management — organize and re-plot datasets from a
large research project, with a Jupyter-driven exploratory workflow.

<img width="1309" height="1318" alt="NOORSUITE_ICON_old" src="https://github.com/user-attachments/assets/7b46cb80-27a4-401c-a0bf-1b489b085eac" />

(Python package: `NoorSuite`.)

- **Jupyter** is the exploratory side: read data, poke at it with pandas, and push the
  DataFrames worth keeping into the running GUI (each becomes one *data object*).
- **The GUI** is where you pick x/y columns from a data object, place them as traces on
  sheets and subplots, style them (double-click any line, title, legend, axis, or border to
  edit it), tag & annotate sheets, organize them into folders, save `.sciproj` projects, and
  export to share with colleagues.

The two run as **separate processes** and can be used simultaneously; they talk over a
loopback TCP socket on port 55555.

## Install

```bash
pip install -e .
```

Requires Python >= 3.10, PyQt6, matplotlib, numpy, pandas.

## Claude skill (insert data from any Claude session)

```bash
pip install "git+https://github.com/RSRekers/Noorsuite.git"
python -m NoorSuite skill install      # copies the skill to ~/.claude/skills/noorsuite/
```

Any Claude Code session can then insert/plot/organize data (tags, notes, named sheets,
folders) -- live if the GUI is running, otherwise into the session file / a `.sciproj` that the
GUI loads (`from NoorSuite import connect`; or `python -m NoorSuite insert data.csv ...`).
The skill runs `skill ensure` on each use: at most once a day it compares against GitHub,
`pip install --upgrade`s the package if there is a newer commit, and refreshes the skill file.
`python -m NoorSuite skill status | update --force` for manual control. (pip cannot write to
`~/.claude` at install time, hence the one explicit `skill install` step.)

## Use

Start the GUI (also started automatically by `SciSuiteClient().launch()`):

```bash
python -m NoorSuite --gui 55555
```

or the `noorsuite` / `scisuite` console script after `pip install -e .`.

In the window: the current project name shows in the toolbar; **Ctrl+S** saves to the
current `.sciproj` file (Save As… the first time) and the project autosaves every ~2 min
once a file is set. Each sheet has a **Notes** tab you can use as a lab notebook for that
graph — notes are saved with the project and searchable from the project tree.

From a notebook:

```python
from NoorSuite import SciSuiteClient
suite = SciSuiteClient().launch()

suite.push_dataframe(df, name="IV_sweep", tags=["run_4"])      # register a data object
suite.plot("IV_sweep", x="time_s", y=["current_mA", "voltage_V"], new_sheet=True)
suite.list_data()            # what is in the pool
suite.list_traces()          # what is on the sheets
suite.push_dataframe(df, name="IV_sweep", mode="update")       # refresh data in place

df = suite.get_data("IV_sweep")           # pull it back, edit, push -> GUI stays in sync
df["power_mW"] = df["current_mA"] * df["voltage_V"]
suite.push_dataframe(df, name="IV_sweep", mode="update")

suite.show_image(nd_array, name="z_stack", axes=(1, 2), new_sheet=True)  # ND image + z-slider
```

See [`noorgraph.ipynb`](noorgraph.ipynb) for a full walk-through.

## Heatmaps

A heatmap is a 2-D grid whose cells sit at real x / y values (a pivot table, a parameter sweep, a
correlation matrix). It lives in the data pool next to images and is placed on a sheet like one.

```python
suite.heatmap(df_grid, name="T vs f", new_sheet=True,        # index = y values, columns = x values
              cmap="RdBu", reverse_cmap=True,
              boundaries=[-10, 0, 10, 20, 30, 40],           # discrete colour bands (or bins=5)
              isolines=True, iso_above=10, iso_labels=True,  # contour lines only at levels >= 10
              xticks=2, yticks="data")                       # every 2nd x value / every y value
suite.push_heatmap_xyz(long_df, "f", "T", "sigma", name="sigma map")   # long format -> grid
```

In the GUI the same settings are in the **Image** section of the Axes tab (colour bands and edges,
reverse, *Isolines*, *Axis ticks*, "Use axis coordinates"). Categorical (string) axes work too.

## LaTeX articles with live figures

Write the article next to the plots. **Tree -> right-click -> New LaTeX Document** opens a Document tab:
the `.tex` source on the left, the compiled PDF on the right. Figures are NoorSheets:

```latex
\usepackage{noorsuite}          % after hyperref
...
\noorsheet{a1b2c3d4}            % the sheet with that id, as a vector PDF at its own size
```

- **Insert figure...** writes a ready `figure` environment for any sheet.
- **Double-click a figure** in the preview (or a `\noorsheet` line in the source) to jump to its sheet;
  change the plot and the document recompiles by itself (Auto-compile).
- The sheet's *Figure* tab size (cm) is the printed size, so figure text matches the article's text.
- **Export PDF...** / **Export LaTeX folder...** (main.tex + noorsuite.sty + figure PDFs, ready for a journal,
  arXiv or Overleaf). Headless: `python -m NoorSuite latex build --project p.sciproj --doc Name --out paper.pdf`.
- From code / Claude: `suite.push_document(name, source, mode="append")`, `suite.figure_snippet(sheet)`.
- **Figures panel**: thumbnails of every sheet, searchable (`#tag`, `"phrase"`, `-word`); double-click, *Insert
  selected*, or **drag** a thumbnail -- or drag a sheet / a whole folder straight from the project tree --
  into the source to insert the figure environments.
- **Zotero**: *Cite (Zotero)...* (Ctrl+Shift+Z) opens Zotero's picker, stores the chosen items in the project
  and inserts `\cite{key}`. No Better BibTeX library export needed (only its picker, like the Obsidian
  `Zoterolink` script). The *References* tab lists the project's references (cite / `\citep` / `\citet` /
  link back to Zotero) and inserts the reference list in the style you choose (plain, unsrt, ieeetr, alpha,
  plainnat, apalike ...). `references.bib` is generated when building.
- **Numbering**: citations are numbered in order of first appearance and the reference list is in that order.
  The default format is **ACS Guide** (the April 2025 revision of the *ACS Guide to Scholarly Communication*,
  chapter 4.3): superscript numbers in the text, `(1)` labels in the list, authors as `Family, A. B.`, bold
  year, italic volume, `DOI:` as a hyperlink; journal titles abbreviated only if all of them have an
  abbreviation (the Guide wants one convention). NoorSuite writes the list itself (see `refstyle.py`; all
  rules are in that one file). BibTeX formats (the older `achemso.bst`, unsrt, ieeetr, plain, plainnat,
  apalike, ...) stay available in the References tab. The format belongs to the *document*; the mouse wheel
  never changes it. If a text cites but has no reference list, the build adds one to its built copy, so you
  never get a bare `?`.
- **Library**: the *Zotero library* tab of the References panel searches your whole library live (author,
  title, year, citation key) and *Add & cite* stores the item in the project and inserts `\cite{key}`. It
  talks to Better BibTeX's JSON-RPC, so there is no picker window, no library export and no Zotero setting to
  switch. Typing a citation key by hand (or pasting one from Obsidian) works too: unknown keys are looked up
  in Zotero while building. *Pick in Zotero...* (the picker window), *Import whole library* and *Import
  file...* (`.bib` / CSL-JSON) are still there.
- The editor is **black on white** by default (toggle *Dark editor* for light-on-dark); the data panel is
  collapsed and the inspector hidden while a document tab is current, to give it the width.
- Builds only re-export the figures that changed; the status bar shows "figures N new / M cached".
- Needs a LaTeX engine: [tectonic](https://tectonic-typesetting.github.io) (`conda install -c conda-forge
  tectonic`), MiKTeX or TeX Live -- found automatically, or set `$NOORSUITE_LATEX`.

## Searching

The search boxes (data pool, project tree, figure list) are word based: **every word must match**, as a
whole word, a word prefix or a substring; names and tags count more than notes. Operators: `"exact
phrase"`, `-exclude`, `#tag`, `tag:x`, `name:x`, `note:x`. Abbreviations (`rspns` for *Response*) only match
inside a name or tag word, never across the long content text -- which is what used to let short queries
match almost everything.

## Custom tick labels

Any subplot (line plot or heatmap) can show its own tick text: `x_tick_labels={0: "RT", 1: "77 K",
2: "4 K"}` (ticks at those values) or a plain list `["RT", "77 K", "4 K"]` (positions 0, 1, 2 ...)
in `plot` / `heatmap` / `show_image` / `organize_sheet`. On a categorical heatmap axis a string key renames
that category (`{"sig_S_cm": "sigma"}`). In the GUI: Axes tab -> *X / Y tick labels*, written as
`0=RT; 1=77 K; 2=4 K`.

## GUI shortcuts worth knowing

- **Ctrl + mouse wheel** over a plot zooms the whole figure (text and lines scale with it);
  **Ctrl+0** resets. Zoom is display-only -- exports and clipboard copies are unaffected.
- Right-click a sheet or folder in the project tree -> **Duplicate** (a folder is copied with
  all its sheets; the copies reference the same data objects).
- Axes tab -> **Apply this style to selected subplots...** copies the active subplot's style to the
  subplots Ctrl/Shift-selected in the subplot strip, optionally including title/labels and limits.
- Trace style -> **Sort points by x** (or `plot(..., sort=True)`) for a disordered x column.
- "Copy to clipboard" is WYSIWYG: same proportions as on screen at 300 DPI (the cm size from the
  Figure tab is used by "Export as SVG").

## Tests

```bash
pytest
```
