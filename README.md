<<<<<<< HEAD
# Noorsuite
a scientific plotting suite for data management

<img width="1309" height="1318" alt="NOORSUITE_ICON_old" src="https://github.com/user-attachments/assets/7b46cb80-27a4-401c-a0bf-1b489b085eac" />
=======
# NOORSUITE

(Python package `NoorSuite`.) A desktop tool for **organizing and re-plotting datasets**
from a large research project, with a Jupyter-driven exploratory workflow.

- **Jupyter** is the exploratory side: read data, poke at it with pandas, and push
  the DataFrames worth keeping into the running GUI (each becomes one *data object*).
- **The GUI** is where you pick x/y columns from a data object, place them as traces on
  sheets and subplots, style them (double-click any line, title, legend, axis, or
  border to edit it), organize sheets into folders, save `.sciproj` projects, and
  export to share with colleagues.

The two run as **separate processes** and can be used simultaneously; they talk over
a loopback TCP socket on port 55555.

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

From a notebook:

```python
from NoorSuite import SciSuiteClient
suite = SciSuiteClient().launch()

suite.push_dataframe(df, name="IV_sweep", tags=["run_4"])      # register a data object
suite.plot("IV_sweep", x="time_s", y=["current_mA", "voltage_V"], new_sheet=True)
suite.list_data()            # what is in the pool
suite.list_traces()          # what is on the sheets
suite.push_dataframe(df, name="IV_sweep", mode="update")       # refresh data in place
```

See [`noorgraph.ipynb`](noorgraph.ipynb) for a full walk-through.

## Tests

```bash
pytest
```
>>>>>>> cb54828 (main commit)
