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

## Tests

```bash
pytest
```
