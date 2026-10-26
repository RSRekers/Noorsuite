"""Starter Jupyter notebook next to a project (Qt-free, stdlib only).

NoorSuite does not need Jupyter: the GUI, ``noorsuite api ...`` and ``noorsuite insert ...`` work on their
own. A notebook is an optional workbench; this writes one into the project's folder, already wired to the
project (``connect`` talks to the running GUI, or edits the project file when no GUI is open).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

PROJECT_SUFFIX = ".sciproj"


def project_for(path) -> Path:
    """Resolve what the user pointed at to a ``.sciproj`` path (it need not exist yet).
    A ``.sciproj`` file is used as is; a folder uses the project inside it (the only / newest one) or
    ``<folder name>.sciproj`` when there is none; anything else (``paper``, ``paper.tex``) is a new project
    of that name next to it."""
    p = Path(path).expanduser()
    if p.suffix.lower() == PROJECT_SUFFIX:
        return p.resolve()
    if p.is_dir():
        found = sorted(p.glob("*" + PROJECT_SUFFIX), key=lambda f: f.stat().st_mtime, reverse=True)
        return (found[0] if found else p / (p.name + PROJECT_SUFFIX)).resolve()
    return p.with_suffix(PROJECT_SUFFIX).resolve()


def _cell(kind: str, text: str) -> dict:
    cell = {"cell_type": kind, "metadata": {}, "source": text.splitlines(keepends=True)}
    if kind == "code":
        cell.update(execution_count=None, outputs=[])
    return cell


def notebook_json(project: Path) -> dict:
    """The starter notebook as a dict (nbformat 4)."""
    name = project.name
    cells = [
        _cell("markdown", f"# {project.stem}\n\nNoorSuite workbench. `connect` uses the open NoorSuite window "
                          f"when there is one and otherwise edits `{name}` directly, so the cells below work "
                          "either way. Start the window with `noorsuite " + name + "`."),
        _cell("code", "import numpy as np\nimport pandas as pd\nfrom NoorSuite import connect\n\n"
                      f"suite = connect({name!r})"),
        _cell("code", 'x = np.linspace(0, 10, 200)\ndf = pd.DataFrame({"x": x, "y": np.sin(x)})\n'
                      'suite.push_dataframe(df, name="example")\n'
                      'suite.plot("example", "x", "y", new_sheet=True, title="Example")'),
        _cell("markdown", "Everything the window can do is available here (`help(suite)`) and on the command "
                          "line (`noorsuite api --help`)."),
    ]
    return {"cells": cells, "nbformat": 4, "nbformat_minor": 5,
            "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                         "language_info": {"name": "python"}}}


def notebook_path(project: Path, name: str | None = None) -> Path:
    """Where a new notebook goes: next to the project, never over an existing file (``name-2.ipynb`` ...)."""
    stem = re.sub(r'[<>:"/\\|?*]+', "_", name or project.stem).strip() or "notebook"
    if stem.lower().endswith(".ipynb"):
        stem = stem[:-6]
    base = project.parent / (stem + ".ipynb")
    n = 2
    while base.exists():
        base = project.parent / f"{stem}-{n}.ipynb"
        n += 1
    return base


def create_notebook(project, name: str | None = None) -> Path:
    """Write a starter notebook beside ``project`` and return its path."""
    project = Path(project)
    project.parent.mkdir(parents=True, exist_ok=True)
    path = notebook_path(project, name)
    path.write_text(json.dumps(notebook_json(project), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def jupyter_command(path) -> list[str] | None:
    """Command that opens the notebook, or None when Jupyter is not installed."""
    import importlib.util
    import sys
    for mod, args in (("jupyterlab", ["-m", "jupyterlab"]), ("notebook", ["-m", "notebook"])):
        if importlib.util.find_spec(mod):
            return [sys.executable, *args, str(path)]
    return None
