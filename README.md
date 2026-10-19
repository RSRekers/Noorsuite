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
  and inserts `\cite{key}`. The *References* tab lists the project's references (cite / `\citep` / `\citet` /
  link back to Zotero) and inserts the reference list in the style you choose (plain, unsrt, ieeetr, alpha,
  plainnat, apalike ...). `references.bib` is generated when building. Make sure to activate the local API of Zotero under Settings>Advanced>Miscellaneous>Allow other Applications on this computer to communicate with Zotero.
- **Numbering**: citations are numbered in order of first appearance and the reference list is in that order.
  The default format is **ACS Guide** (the April 2025 revision of the *ACS Guide to Scholarly Communication*,
  chapter 4.3): superscript numbers in the text, `(1)` labels in the list, authors as `Family, A. B.`, bold
  year, italic volume, the DOI as a hyperlinked `https://doi.org/...` URL; journal titles use Zotero's
  abbreviation where there is one (`J. Electrochem. Soc.`). Example of an entry:
  `Daubner, S.; Nestler, B. Microstructure Characterization ... Tortuosity. J. Electrochem. Soc. 2024, 171
  (12), 120514. https://doi.org/10.1149/1945-7111/ad9a07.` Items picked in Zotero's picker sometimes arrive as
  little more than a title; such records are completed from the library automatically (when Zotero runs). NoorSuite writes the list itself (see `refstyle.py`; all
  rules are in that one file). BibTeX formats (the older `achemso.bst`, unsrt, ieeetr, plain, plainnat,
  apalike, ...) stay available in the References tab. The format belongs to the *document*; the mouse wheel
  never changes it. Switching format is fully reversible: the document's own natbib / achemso / apalike /
  apacite lines are replaced (not accumulated), a missing `\bibliographystyle` is put back, and the build
  copy ignores leftovers of another format, so APA -> ACS -> APA -> ... always compiles. In-text numbers
  can be superscript (default), italic in parentheses `(1)` or `[1]`; both are ACS Guide variants. If a text cites but has no reference list, the build adds one to its built copy, so you
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

## Presentations (Beamer)

A presentation is a LaTeX document of class `beamer`: right-click in the project tree -> *New presentation* (plain
Beamer 16:10, or one of your imported corporate templates). Everything of the document tab applies - live PDF
preview (one page per slide), figure panel (a figure is inserted as a **frame** with the sheet, width options:
slide width / two thirds / half), tables, references, F5 compile, PDF export. Edit a sheet by double-clicking it
in the preview.

**Import PowerPoint template...** (same menu, or `python -m NoorSuite presentation import file.pptx --name X`)
converts a corporate `.pptx` into a Beamer theme: the slide master's bars, logos (SVG / EMF converted to PDF through
Inkscape, photos cropped and compressed), static texts, the title bar with the title placeholder's size and colour,
slide number and source line positions, bullet style, the font (Open Sans Light falls back to the TeX Live
`OpenSans-*.ttf`), and the *title layout* as the title page (picture, logo block, title / author / date). The result
is stored under `~/.noorsuite/presentations/<name>/` (the theme files are copied next to the document when it is
built, so exported folders compile anywhere) and listed as a layout. In the text: `\framesource{Source: ...}`
sets the footer line of a frame, `\imagecredit{...}` and `\titleimage{<file>}` the title-page picture credit and
picture (the other title pictures of the template are listed in `template.json`).

**Running a talk.** *Present* (F11, document toolbar) shows the built PDF full screen: arrow keys / space /
click = next, Backspace / right click = back, `B` black screen, `W` white screen, number + Enter jumps, Esc leaves.
Any PDF viewer works as well (the exported PDF is the talk). To run it on another machine, *Export LaTeX folder...*
writes `main.tex`, the theme, figures and a `README.txt`; `xelatex main.tex` (or `tectonic main.tex`) builds the
PDF there.

**Effects.** Beamer overlays work as usual and are exported: `\pause`, `\item<2->`, `\begin{itemize}[<+->]`,
`\uncover<3->{...}`, `\only<2>{...}`, `\begin{block}<2->`, `\setbeamercovered{transparent}` (dims what has not appeared).
The *Effects* menu in the document toolbar inserts them (reveal a list item by item, highlight the current item,
selection appears from step n, dimming, slide transitions `\transfade` / `\transdissolve` / `\transwipe` ... for one
or all slides). In the PDF every step is one page, so *Present* shows them click by click (a short cross-fade is
used when `\transfade` / `\transdissolve` is in the text). The PowerPoint export turns the steps into click
animations (Appear, Fade or Wipe, chosen at export) and the `\trans...` commands into slide transitions.

**Slide editor.** The *Slide editor* tab next to the PDF preview shows one slide (the compiled page) and lets
you place and edit free objects like in PowerPoint: rectangle, ellipse, line, arrow, text box, NoorSuite graph and
SVG / PNG / JPG / PDF pictures. Drag to move, handles to scale (Shift keeps the proportions), the round handle
rotates (Shift: 15 degree steps), arrow keys nudge, Del removes, Ctrl+D duplicates, Ctrl+[ / ] changes the stacking
order, *Snap* aligns to the slide centre and a 5 mm grid. A side panel holds the numbers (position, size, rotation),
fill (solid or two-colour linear / radial gradient), line colour / width / dash, opacity, text colour / size /
alignment and the label. Double-click a graph to edit it in NoorSuite, an SVG to edit it in Inkscape (also in the PDF
preview), text to edit the label. Objects are plain `\noorobj[x=..,y=..,w=..,h=..]{kind}{content}` lines inside the
frame (positions in cm from the top-left corner), so everything stays editable in the text, in undo and in the
PowerPoint export (native shapes, connectors, pictures, text boxes).

**Error bars from the API / CLI.** `suite.plot("d", "x", "y", yerr="y_err")` (also `yerr_low` / `yerr_high`, `band=True`,
`errorbars=False`, `yerr_mode`; with several y columns `yerr={"y1": "e1", "y2": "e2"}`) and on the command line
`python -m NoorSuite insert data.csv --plot x:y1,y2 --yerr e1,e2 [--band] [--yerr-mode sem]`. Missing error columns are reported
instead of silently dropped.

**Fast documents.** After the window opens, NoorSuite prepares the documents in the background (figure PDFs, thumbnails, cited Zotero records), and an unchanged document is not rebuilt in the next session.

**Command line.** `python -m NoorSuite insert ...` puts a file into the project; `python -m NoorSuite api <command>` covers the rest
of the client: `list`, `get`, `remove`, `clear`, `annotate`, `organize`, `shape`, `clear-shapes`, `heatmap`, `mean`, `document`,
`get-document`, `si-document`, `asset`, `table`, `snippet`, `references` (`python -m NoorSuite api --help`).

**Mean with a shaded band.** *Combine into mean ± error trace...* (several selected traces, or one trace that holds
repeats) now asks how to show the spread: **shaded band** (default), error bars, or both, and from which source: standard
deviation, standard error of the mean (std / sqrt(n)) or min / max. The result is one trace in the colour of the first
original (the derived data object also keeps `std`, `sem`, `err_min`, `err_max`). Every trace with error data has the same
controls in its *Trace Style* tab (*Show shaded band*, *Band opacity*, *Error source*), so the choice can be changed
afterwards. From a notebook: `suite.plot_mean(["run1", "run2", "run3"], "x", "y", band="sem", errorbars=False)` or
`suite.plot_mean("wide", "t", ["a", "b", "c"], band="minmax")`.

**Window and sidebar.** The window is never larger than the screen (on a laptop the plot sidebar used to sit beyond the
screen edge). The sidebar can no longer be dragged to zero width; *Sidebar* in the toolbar (F4) shows / hides it and
*Reset layout* brings back the default panel widths. Documents hide the sidebar while their tab is open and sheets bring
it back (unless you switched it off).

**Default fonts.** New subplots start with tick labels 13 pt, axis labels 15 pt, title 16 pt, legend 11 pt, and new sheets
use real point sizes (a 15 pt label prints as 15 pt when the figure is placed at its own width). Saved sheets keep their
own sizes and their older scaled text; switch *Fonts in real points* on in the Figure tab to convert one.

**Deleting through the API.** `suite.remove_data(name_or_id_or_list)` and `suite.remove_sheet(name_or_id_or_list)` delete data
objects / images and sheets (with their tab and tree entry; data stays in the pool when a sheet goes, traces disappear
with their data). Ids win over names; a name that is not unique raises `ValueError` before anything is deleted. Works
live and offline.

**Autocomplete and editing help (LaTeX editor).** While typing, a list opens under the caret: `\be` offers `\begin`,
`\begin{` offers the environments (choosing `equation` writes the whole `\begin{equation} ... \label{eq:} ... \end{equation}`
block), `\end{` offers the environments that are still open, `\cite{` the project's references, `\ref{` the labels of the
text, `\noorsheet{` / `\noorfile{` / `\noortable{` the sheets, figure files and tables, `\usepackage{` common packages, and
the option keys of `\noorobj[` / `\includegraphics[`. Up / Down choose, **Tab** fills (Enter fills only after you navigated with
the arrows, otherwise it is a newline), Esc closes. A filled snippet puts the caret in its first field; **Tab** jumps to the
next field. Enter after `\begin{env}` adds the matching `\end{env}`. Typing `( [ {` closes the bracket, typing the closer
steps over it, Backspace in an empty pair removes both. With text selected, typing `( [ { < " ' ` $ * _ ~ | =` **wraps** the
selection instead of replacing it (the inner text stays selected, so `$` twice gives `$$text$$`). Both helpers can be switched
off in *Format*.

**Pasting pictures.** Ctrl+V with a picture (or copied picture files) on the clipboard stores it in the project and puts it
into the text: a figure in a document, a free, movable object on the current slide in a presentation. In the slide editor
Ctrl+V (or *Paste picture*) does the same. *Format -> Paste picture from clipboard* works without a keyboard shortcut.

**Figure and font sizes.** Every sheet has one physical size: its own cm size, else 12 x 8 cm. The screen, the PDF, the PNG
exports and the clipboard all use it, so a label has the same proportion to the figure everywhere (it used to depend on the
window size on screen). *Figure* tab -> **Fonts in real points** makes a 10 pt label exactly 10 pt on paper when the figure is
placed at its own width (older sheets keep the scaled sizes).

**Large projects.** Data above ~4000 numbers is saved as binary files in `<project>/_data/` (and imported figure files in
`<project>/_files/`) instead of JSON: a 64 MB data pool saves in 0.3 s instead of 38 s, loads in 0.4 s instead of 7 s and
the autosave skips everything that did not change. Long lines (> 20 000 points) are drawn as a min / max envelope at the
resolution of the figure (peaks are kept, exports use a finer envelope); sheets in hidden tabs are drawn when you show
them, pushing a new data frame no longer redraws any sheet, one request draws a sheet once.

**Master layout.** Choose *Master layout (all slides)* at the top of the slide editor's frame list to edit the
template itself: the title, body text area, slide number and source line are dashed placeholder boxes (their
starting positions are measured in the compiled PDF) that you drag and resize, with font size and colour in the
side panel; rectangles, ellipses, lines, text, graphs and SVG logos you add there appear on every slide
(`\noormasterobj` lines in the preamble). The first change takes the four placeholders over and writes them into
the `\noorslide{title-x=..,title-y=..,title-w=..,body-x=..,body-y=..,body-w=..,num-x=..,num-y=..,src-x=..}` line,
so what the editor shows is what LaTeX produces.

**Build speed.** With tectonic, a rebuild reuses the auxiliary files of the previous one and runs TeX a second
time only when positions / numbers changed (half the time for ordinary text edits; first build and structural
changes still take two passes). `NOORSUITE_SLOW_BUILD=1` switches back to tectonic's own rerun logic.

**Slide setup.** *Slide setup...* in the slide editor writes one `\noorslide{...}` line: title shift / size, space
above the body, left / right text margins, and *slide number on the title slide* (position and size). It works on top
of whatever theme the document uses.

**Figures in text documents.** Right-click a figure in the PDF preview (or in the editor) -> *Transform...*: width
(cm or % of the text width), height, scale, rotation and shift (`\noorsheet[width=..,angle=..,xshift=..]{..}`).

**Formatting.** *Format* in the toolbar (and Ctrl+B / I / U) wraps the selection: bold, italic, underline, colour,
size, super / subscript, highlight, lists, alignment.

**Presenter view.** *Presenter view* (Shift+F11) opens a console next to the full-screen show (on a second
monitor when there is one): current slide, next slide (or the next step of the same slide), your `\note{...}`
text, a timer with an optional target time (turns yellow in the last minute, red when over), the clock, and
a **laser pointer** - press `L` or the button and move the mouse over the slide (in the console's slide view or on
the audience screen). Keys in either window: Right / Space next, Left back, B black screen, L laser, Esc ends.
Every overlay step (`\pause`, `\item<2->` ...) is one click; the console shows "step 2 / 3".

**Preview to text.** Double-click a word in the PDF preview and the editor jumps to (and selects) that line
(SyncTeX; compile once after updating). Double-click on a figure still opens it for editing.

**Find and replace.** Ctrl+F in the LaTeX editor opens a search bar (all matches highlighted, count, F3 / Shift+F3 for
next / previous, Esc closes); Ctrl+H adds the replace row. Options: match case, whole words, regular expression
(`\1` in the replacement refers to a group). *Replace all* is one undo step.

**PowerPoint export.** *Export PowerPoint...* (or `python -m NoorSuite latex pptx --doc Talk --out talk.pptx`) writes
native, editable slides: frame titles in title placeholders, bullet lists with their levels, paragraphs, sheets and
figure files as pictures, tables as PowerPoint tables, columns in the two-content layout, the footer
`\framesource` line, `\note{...}` as speaker notes and slide numbers. When the theme was imported from a
PowerPoint template, **that template's master and layouts are reused** (the original `.pptx` is kept in the theme
folder), so the talk carries the real corporate design. Math is approximated as Unicode text (Greek letters,
super / subscripts, fractions as (a)/(b)); anything else is listed in the log. Needs `pip install python-pptx`.

**Where templates live.** Nothing corporate is part of the repository. NoorSuite ships one neutral theme
(*Beamer - NoorSuite clean theme*, `NoorSuite/themes/noorclean/`: bar + accent rule, no logos, plain title page,
colours editable at the top of the `.sty`). Your own layouts are files in your home folder:
`~/.noorsuite/templates/<name>.json` (layouts for documents and presentations, saved with *Save layout...* or by the
PowerPoint import) and `~/.noorsuite/presentations/<name>/` (the Beamer theme `.sty` and its pictures). A
converted template is one portable file: `python -m NoorSuite presentation export <name> --out my.noortheme` and
`... presentation import my.noortheme` (or *Import PowerPoint template...* in the GUI accepts `.pptx` and `.noortheme`).
`*.pptx` and `*.noortheme` are git-ignored.

## Author lists and Supporting Information

*Authors...* (document toolbar) edits the author list in a table: names, affiliation keys (`1,2`), e-mail,
*corresponding*; affiliations are a second table. The front matter is written in the convention of the document's
layout - `authblk` (`\author[1,2]{..}` + `\affil[1]{..}`) in the plain article, `\author{}` + `\affiliation{}` /
`\alsoaffiliation{}` / `\email{}` for ACS, `\author[1]{..}` + `\address[1]{..}` + `\ead{}` for Elsevier, names with
superscripts plus affiliation lines for RSC - and the list survives layout switches. Hand-written author blocks are read
in all of these conventions. A build copy repairs the common mistakes (the text is never changed): `authblk` together
with achemso / elsarticle is converted to the class's own commands, `\affiliation[..]` next to authblk is used as
`\affil`, `\author[..]` without authblk gets the package.

*New SI document* (document toolbar; `suite.push_si_document("Paper")`) creates the Supporting Information of a paper:
same class, preamble, macros, title block and authors, the title "Supporting Information for: ...", an empty body
skeleton, and figure / table / equation / page counters prefixed with `S` (Figure S1, (S1), page S1). References and
sheets are project-wide, so the SI cites and shows the same items; the Word export keeps the `S` numbers.

## Tables from data

*Insert table...* (document toolbar) builds a table of a data object: tick and order the columns, give each a
header (default: name + unit), number format (automatic, fixed decimals, significant digits, scientific, integer),
alignment and scale factor; choose the row range, step, maximum rows and sort column. The LaTeX preview is shown
live. The document holds `\noortable{name}` inside a normal `table` environment (caption / label are yours): the
table is **live** - it is regenerated at build time, so it follows the data like a figure follows its sheet - and
the Word / LibreOffice export gets a real table. Double-click the `\noortable` line to edit it again. From code:
`suite.push_table("IV summary", "IV A3", ["V", ("I", "Current (mA)")], rows=(1, 20), digits=2, fmt="fixed")` and
`suite.table_snippet("IV summary", caption="...")`.

## Journal layouts

*My layouts*: **Save layout...** (document toolbar) keeps the open document's class, packages and macros as your own
layout (name + figure widths in cm) in `~/.noorsuite/templates/`; it then appears in the Layout box, in *New journal
manuscript*, in the figure-width list and as `push_document(template="user:<name>")` (`suite.list_templates()`).
The text is never part of it; **Delete layout...** removes one.

A document's *Layout* box (toolbar) re-lays it out for **ACS (`achemso`)**, **RSC (`rsc.sty`, two
columns)**, **Elsevier (`elsarticle`)** or a plain article. Switching regenerates the preamble and front
matter from the document's title / authors / abstract and keeps your text, figures, macros and extra
packages (one undoable edit; ACS `acknowledgement` / `suppinfo` turn into plain sections and back).
New documents: right-click in the project tree -> *New journal manuscript*. The figure panel offers the
journal's widths (ACS 8.25 / 17.78 cm, RSC 8.3 / 17.1 cm, Elsevier 9 / 14 / 19 cm); *Resize sheet* also
sets the sheet's own size to it (aspect kept) so text comes out at its real size, and a double-column
width in RSC becomes a `figure*`. The Word export takes the layout's font, size, line spacing and margins.
From code: `suite.push_document("Paper", body, template="acs", title=..., authors=[...], abstract=...)`.
Classes that bring their own natbib (achemso, elsarticle, rsc) are never given a second one.

## Word / LibreOffice export

*Export Word...* / *Export LibreOffice...* in the document toolbar (or `python -m NoorSuite latex docx|odt
--doc "Paper" --out paper.docx`) writes an editable `.docx` / `.odt` through pandoc: sheets and figure
files are embedded as 300 dpi PNGs at their figure size, citations are the superscript numbers with the
numbered ACS list (journal italic, year bold, DOI as a link), figures / tables / sections are numbered
("Figure 2. ...") and `\ref` / `\autoref` resolved, equations become native Word / ODF equations.
Numbered equations (`\begin{equation}`) become a small borderless three-column table: empty left cell,
the equation centred in the middle, `(n)` right-aligned - `\eqref` / `\ref` use the same numbers (Word
only; ODT keeps the table without the alignment). `align` / `eqnarray` rows become a four-column table (number per row, split at the first `&` so the relation signs line up; `\nonumber` rows stay unnumbered), `gather` one centred row per line; `equation*` / `align*` stay plain display math.
Not carried over: custom macros, BibTeX-style formats other than the numbered ACS list
(the Word file always gets that list). pandoc is found via `$NOORSUITE_PANDOC`, `config.json`
`{"pandoc": ...}`, PATH or the `noorlatex` conda env (`conda install -n noorlatex -c conda-forge pandoc`).

## Objects on plots and imported figures

* **Objects** (rectangle, ellipse, line, arrow, highlight band, text, picture) are drawn on a subplot from
  the layer list under the traces (*Add object*). Traces and objects form **one back-to-front stack**:
  drag, or use *To back / Down / Up / To front* (also right-click an object on the plot). Double-click an
  object to edit it: geometry in data or axes coordinates, outline, rotation, and a fill that is solid, a
  **linear or radial gradient** through any number of key colours with per-colour transparency (load a
  colormap's colours, or save yours as a colormap). From code: `suite.add_shape(...)`, see the skill.
* **Direct manipulation**: click an object on the plot to select it - drag the body to **move**, a
  corner handle to **scale** (Shift = keep proportions; pictures keep theirs), the round handle on top to
  **rotate** (Shift = 15 degree steps). Line / arrow end points and band edges have their own handles.
  The Object tab holds the same as numbers: position, rotation and **scale factors** (width / height).
* **Hierarchy**: *Make child of...* pools objects under a root - children move, rotate and scale along with
  it, are shown indented in the layer list and travel with it in the layer order.
* **Content only** (Axes tab -> Cosmetics): hide axes, ticks, labels, title, legend and background of a
  subplot so just the image / picture and its objects remain; when every subplot of a sheet is
  content-only the margins go as well.
* **Figure files** (SVG, PDF, PNG, JPG): *Figures* panel of a document -> *Add figure file...*; they are
  stored in the project. Insert with a click/drag (`\noorfile[width=..]{name}`); SVG is converted to a
  vector PDF by Inkscape (or Qt as a fallback). **Double-click the figure in the PDF preview (or the
  `\noorfile` line) to edit it in Inkscape** - saving there reloads it everywhere. The same files can be
  placed inside a plot as a *Picture / SVG* object (rasterised at high resolution in the plot).
* Inkscape is found via `$NOORSUITE_INKSCAPE`, `~/.noorsuite/config.json` `{"inkscape": "<path>"}`, PATH or
  the default install folder.

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
