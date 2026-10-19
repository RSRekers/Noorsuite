"""``python -m NoorSuite api ...`` -- every client action reachable from the command line (offline)."""
import subprocess
import sys

import numpy as np
import pandas as pd

from NoorSuite.model import ProjectModel


def _run(tmp, *args):
    return subprocess.run([sys.executable, "-m", "NoorSuite", "api", "--project",
                           str(tmp / "p.sciproj"), "--port", "59123", *args],
                          capture_output=True, text=True)


def _ok(tmp, *args):
    r = _run(tmp, *args)
    assert r.returncode == 0, (args, r.stdout, r.stderr[-600:])
    return r.stdout


def _setup(tmp):
    x = np.linspace(0, 5, 6)
    pd.DataFrame({"x": x, "r1": x, "r2": x + 0.2, "r3": x - 0.1}).to_csv(tmp / "d.csv", index=False)
    r = subprocess.run([sys.executable, "-m", "NoorSuite", "insert", str(tmp / "d.csv"), "--name", "d",
                        "--plot", "x:r1", "--new-sheet", "--title", "S", "--project",
                        str(tmp / "p.sciproj"), "--port", "59123"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def _pm(tmp):
    return ProjectModel.load(tmp / "p.sciproj")


def test_list_get_annotate_organize_shape_remove(tmp_path):
    _setup(tmp_path)
    assert "d" in _ok(tmp_path, "list", "data")
    assert '"name":"S"' in _ok(tmp_path, "list", "sheets", "--json").replace(" ", "")
    assert "r1" in _ok(tmp_path, "get", "d")
    _ok(tmp_path, "get", "d", "--out", str(tmp_path / "back.csv"))
    assert len(pd.read_csv(tmp_path / "back.csv")) == 6
    _ok(tmp_path, "annotate", "d", "--tags", "a,b", "--notes", "hello")
    obj = next(o for o in _pm(tmp_path).data_objects if o.name == "d")
    assert {"a", "b"} <= set(obj.tags) and "hello" in obj.notes
    _ok(tmp_path, "organize", "S", "--title", "Renamed", "--tags", "t1", "--folder", "A/B",
        "--subplot-title", "Top")
    sm = next(iter(_pm(tmp_path).sheets))
    assert sm.name == "Renamed" and "t1" in sm.tags and sm.subplots[0].title == "Top"
    _ok(tmp_path, "shape", "Renamed", "rect", "--x0", "1", "--x1", "2", "--y0", "0", "--y1", "1",
        "--fill", "#ff0000", "--name", "box")
    assert len(next(iter(_pm(tmp_path).sheets)).subplots[0].shapes) == 1
    _ok(tmp_path, "clear-shapes", "Renamed")
    assert not next(iter(_pm(tmp_path).sheets)).subplots[0].shapes
    _ok(tmp_path, "remove", "sheet", "Renamed")
    assert not _pm(tmp_path).sheets
    _ok(tmp_path, "remove", "data", "d")
    assert not _pm(tmp_path).data_objects
    r = _run(tmp_path, "clear")
    assert r.returncode == 1 and "--yes" in r.stdout


def test_mean_heatmap_document_table_snippets(tmp_path):
    _setup(tmp_path)
    _ok(tmp_path, "mean", "d", "--x", "x", "--y", "r1,r2,r3", "--band", "sem", "--new-sheet")
    tr = [t for sm in _pm(tmp_path).sheets for sub in sm.subplots for t in sub.traces][-1]
    assert tr.show_band and tr.yerr_mode == "sem"
    bad = _run(tmp_path, "mean", "d", "--y", "r1", "--new-sheet")
    assert bad.returncode == 1 and "mean failed" in bad.stdout
    grid = pd.DataFrame(np.arange(12.0).reshape(3, 4), index=[0.0, 1.0, 2.0], columns=[10, 20, 30, 40])
    grid.to_csv(tmp_path / "g.csv")
    _ok(tmp_path, "heatmap", str(tmp_path / "g.csv"), "--name", "hm", "--cmap", "viridis",
        "--new-sheet", "--isolines", "3")
    assert any(i.name == "hm" for i in _pm(tmp_path).images)
    pd.DataFrame({"a": [1, 1, 2, 2], "b": [1, 2, 1, 2], "c": [1., 2., 3., 4.]}).to_csv(
        tmp_path / "xyz.csv", index=False)
    _ok(tmp_path, "heatmap", str(tmp_path / "xyz.csv"), "--xyz", "a,b,c", "--name", "hm2", "--new-sheet")
    assert any(i.name == "hm2" for i in _pm(tmp_path).images)
    _ok(tmp_path, "document", "Paper", "--text", r"\documentclass{article}\begin{document}Hi\end{document}")
    assert "Hi" in _ok(tmp_path, "get-document", "Paper")
    _ok(tmp_path, "document", "Paper", "--text", "\n% more", "--mode", "append")
    assert "% more" in _ok(tmp_path, "get-document", "Paper")
    _ok(tmp_path, "table", "T1", "d", "--columns", "x,r1", "--digits", "2")
    assert any(t.name == "T1" for t in _pm(tmp_path).tables)
    assert r"\noortable" in _ok(tmp_path, "snippet", "table", "T1", "--caption", "C")
    assert r"\noorsheet" in _ok(tmp_path, "snippet", "figure", "S", "--caption", "C")
    assert "T1" in _ok(tmp_path, "list", "tables") and "Paper" in _ok(tmp_path, "list", "documents")
    assert "article" in _ok(tmp_path, "list", "templates")
    (tmp_path / "logo.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>')
    _ok(tmp_path, "asset", str(tmp_path / "logo.svg"), "--name", "logo")
    assert "logo" in _ok(tmp_path, "list", "assets")
    (tmp_path / "r.bib").write_text("@article{k1, title={T}, author={Doe, J.}, year={2020}, journal={J}}")
    _ok(tmp_path, "references", "--file", str(tmp_path / "r.bib"))
    assert "2020" in _ok(tmp_path, "list", "references")
    _ok(tmp_path, "si-document", "Paper")
    assert "SI" in _ok(tmp_path, "list", "documents")
