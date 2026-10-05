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
suite.remove_data("IV_sweep")
```

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

## 4. After inserting

Tell the user where the data went (live GUI vs. which file) and what exists now
(`list_data()` / `list_sheets()` output, trimmed). Styling, tagging, folders and export are done
in the GUI (double-click traces/axes); this skill only inserts and arranges data.
