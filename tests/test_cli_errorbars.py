"""``python -m NoorSuite insert ... --plot X:Y --yerr ...`` (offline project file)."""
import subprocess
import sys

import numpy as np
import pandas as pd

from NoorSuite.model import ProjectModel


def _run(tmp_path, *args):
    return subprocess.run([sys.executable, "-m", "NoorSuite", "insert", *args, "--project",
                           str(tmp_path / "p.sciproj"), "--port", "59123"],
                          capture_output=True, text=True)


def _csv(tmp_path):
    x = np.linspace(0, 5, 8)
    pd.DataFrame({"x": x, "a": x, "b": 2 * x, "ea": 0.1 + 0 * x, "eb": 0.2 + 0 * x,
                  "lo": 0.05 + 0 * x, "hi": 0.15 + 0 * x}).to_csv(tmp_path / "d.csv", index=False)
    return str(tmp_path / "d.csv")


def _traces(tmp_path):
    pm = ProjectModel.load(tmp_path / "p.sciproj")
    return [t for sm in pm.sheets for sub in sm.subplots for t in sub.traces]


def test_cli_adds_a_figure_with_errorbars(tmp_path):
    r = _run(tmp_path, _csv(tmp_path), "--plot", "x:a", "--new-sheet", "--yerr", "ea")
    assert r.returncode == 0, r.stderr + r.stdout
    (t,) = _traces(tmp_path)
    assert (t.y_col, t.yerr_col, t.show_errorbar, t.yerr_mode) == ("a", "ea", True, "std")


def test_cli_several_y_columns_asymmetric_and_band(tmp_path):
    f = _csv(tmp_path)
    r = _run(tmp_path, f, "--name", "d", "--plot", "x:a,b", "--new-sheet", "--yerr", "ea,eb",
             "--band", "--no-errorbars", "--band-alpha", "0.4", "--yerr-mode", "sem")
    assert r.returncode == 0, r.stderr + r.stdout
    a, b = _traces(tmp_path)
    assert (a.yerr_col, b.yerr_col) == ("ea", "eb")
    assert (a.show_band, a.show_errorbar, a.band_alpha, a.yerr_mode) == (True, False, 0.4, "sem")
    r = _run(tmp_path, f, "--name", "d", "--update", "--plot", "x:b", "--new-sheet",
             "--yerr-low", "lo", "--yerr-high", "hi")
    assert r.returncode == 0, r.stderr + r.stdout
    t = _traces(tmp_path)[-1]
    assert (t.yerr_low_col, t.yerr_high_col, t.yerr_mode, t.show_errorbar) == ("lo", "hi", "minmax", True)
    r = _run(tmp_path, f, "--name", "d", "--update", "--plot", "x:a,b", "--new-sheet",
             "--yerr", "a=ea,b=eb")                                        # explicit pairs
    assert r.returncode == 0, r.stderr + r.stdout
    assert [t.yerr_col for t in _traces(tmp_path)[-2:]] == ["ea", "eb"]


def test_cli_reports_mistakes_without_a_traceback(tmp_path):
    f = _csv(tmp_path)
    r = _run(tmp_path, f, "--plot", "x:a", "--new-sheet", "--yerr", "ghost")
    assert r.returncode == 1 and "plot failed" in r.stdout and "ghost" in r.stdout
    assert "Traceback" not in r.stderr
    r = _run(tmp_path, f, "--name", "d2", "--plot", "x:a,b", "--new-sheet", "--yerr", "ea")
    assert r.returncode == 1 and "several y columns" in r.stdout
