"""Working without Jupyter: ``noorsuite PROJECT``, notebook next to the project (module, client, CLI, GUI)."""
import json
import subprocess
import sys

import pytest

from NoorSuite import notebook as nb
from NoorSuite.client import OfflineClient
from NoorSuite.model import ProjectModel
from tests.test_gui_features import app, win  # noqa: F401  (window fixtures)


def test_project_for_resolves_files_folders_and_names(tmp_path):
    f = tmp_path / "a.sciproj"
    assert nb.project_for(f) == f.resolve()
    assert nb.project_for(tmp_path) == (tmp_path / (tmp_path.name + ".sciproj")).resolve()   # empty folder
    (tmp_path / "x.sciproj").write_text("{}")
    assert nb.project_for(tmp_path) == (tmp_path / "x.sciproj").resolve()                    # the one inside
    assert nb.project_for(tmp_path / "paper.tex") == (tmp_path / "paper.sciproj").resolve()
    assert nb.project_for(tmp_path / "new") == (tmp_path / "new.sciproj").resolve()


def test_create_notebook_is_valid_wired_and_never_overwrites(tmp_path):
    proj = tmp_path / "work" / "my proj.sciproj"
    p1 = nb.create_notebook(proj)
    assert p1 == proj.parent / "my proj.ipynb"
    data = json.loads(p1.read_text(encoding="utf-8"))
    assert data["nbformat"] == 4 and data["cells"][0]["cell_type"] == "markdown"
    code = "\n".join("".join(c["source"]) for c in data["cells"] if c["cell_type"] == "code")
    assert "connect('my proj.sciproj')" in code
    compile(code, "<nb>", "exec")                                                  # the cells are real Python
    p2 = nb.create_notebook(proj)
    p3 = nb.create_notebook(proj, "analysis.ipynb")
    assert p2.name == "my proj-2.ipynb" and p3.name == "analysis.ipynb" and p1.exists()


def test_client_new_notebook_offline_and_live_needs_project(tmp_path):
    suite = OfflineClient(tmp_path / "p.sciproj")
    path = suite.new_notebook(name="lab")
    assert path == str(tmp_path / "lab.ipynb")
    live = OfflineClient(tmp_path / "q.sciproj")
    live.project_path = None                                                      # what a live client has
    with pytest.raises(ValueError, match="project="):
        live.new_notebook()
    assert live.new_notebook(project=tmp_path).endswith(".ipynb")                 # a folder is enough


def test_cli_notebook_and_help(tmp_path):
    run = lambda *a: subprocess.run([sys.executable, "-m", "NoorSuite", *a], capture_output=True, text=True)  # noqa: E731
    r = run("api", "--project", str(tmp_path / "p.sciproj"), "--port", "59123", "notebook", "--name", "nb1")
    assert r.returncode == 0, r.stdout + r.stderr
    assert (tmp_path / "nb1.ipynb").exists()
    h = run("--help")
    assert h.returncode == 0 and "noorsuite [PROJECT" in h.stdout and "Jupyter is optional" in h.stdout


def test_gui_opens_or_creates_project_and_writes_notebook(win, tmp_path, monkeypatch):
    monkeypatch.setattr(nb, "jupyter_command", lambda path: None)                 # never launch Jupyter here
    proj = tmp_path / "paper.sciproj"
    assert win.open_project_path(proj, create=True)                              # missing -> new project
    assert win.project_path == str(proj) and proj.exists() and win.sheets
    assert ProjectModel.load(proj) is not None
    win.create_new_document(template="article", open_tab=True)
    assert win.documents
    path = win.new_notebook()
    assert path and path.endswith("paper.ipynb") and (tmp_path / "paper.ipynb").exists()
    assert win._write_project(str(proj))
    assert win.open_project_path(proj)                                           # existing file loads again
    assert win.documents


def test_new_project_creates_folder_file_and_empty_tree_folders(tmp_path):
    from NoorSuite import newproject
    p = newproject.create_project(tmp_path, "Thesis")
    assert p == (tmp_path / "Thesis" / "Thesis.sciproj").resolve() and p.exists()
    pm = ProjectModel.load(p)
    assert [n["name"] for n in pm.tree] == list(newproject.DEFAULT_FOLDERS)
    assert all(n["type"] == "folder" and n["children"] == [] for n in pm.tree)
    assert not pm.sheets and not pm.documents
    with pytest.raises(ValueError, match="already exists"):
        newproject.create_project(tmp_path, "Thesis")
    q = newproject.create_project(tmp_path, "Nested", "A/B, C")
    assert [n["name"] for n in ProjectModel.load(q).tree] == ["A", "C"]
    assert ProjectModel.load(q).tree[0]["children"][0]["name"] == "B"
    assert ProjectModel.load(newproject.create_project(tmp_path, "Bare", [])).tree == []
    with pytest.raises(ValueError, match="name"):
        newproject.create_project(tmp_path, "  ")


def test_client_and_cli_new_project(tmp_path):
    suite = OfflineClient(tmp_path / "s.sciproj")
    path = suite.new_project("viaClient", tmp_path, ["X"])
    assert ProjectModel.load(path).tree[0]["name"] == "X"
    run = lambda *a: subprocess.run([sys.executable, "-m", "NoorSuite", *a], capture_output=True, text=True)  # noqa: E731
    r = run("api", "--project", str(tmp_path / "s.sciproj"), "--port", "59123", "new-project", "viaCli",
            "--dir", str(tmp_path), "--folders", "Raw,Plots")
    assert r.returncode == 0, r.stdout + r.stderr
    assert [n["name"] for n in ProjectModel.load(tmp_path / "viaCli" / "viaCli.sciproj").tree] == ["Raw", "Plots"]
    bad = run("api", "--project", str(tmp_path / "s.sciproj"), "--port", "59123", "new-project", "viaCli",
              "--dir", str(tmp_path))
    assert bad.returncode == 1 and "already exists" in bad.stdout


def test_gui_new_project_switches_and_saves_current(win, tmp_path):
    first = tmp_path / "one.sciproj"
    assert win.open_project_path(first, create=True)
    win.create_new_document(template="article", open_tab=True)
    path = win.new_project(tmp_path, "Two", ["Data", "Figs"])
    assert path and win.project_path == path
    assert ProjectModel.load(first).documents                                    # the old project was saved
    assert [win.tree.topLevelItem(i).text(0) for i in range(win.tree.topLevelItemCount())
            if win.tree.topLevelItem(i).text(0) in ("Data", "Figs")] == ["Data", "Figs"]
    assert not win.documents
