"""Create a new, empty project (Qt-free): its own directory, a ``.sciproj`` and empty tree folders."""
from __future__ import annotations

import re
from pathlib import Path

from .model import ProjectModel, split_folder_path, tree_folder_edit

DEFAULT_FOLDERS = ("Data", "Figures", "Documents", "Presentations")


def clean_name(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*]+', "_", str(name)).strip(" .")
    if not name:
        raise ValueError("a project name is required")
    return name


def create_project(parent, name: str, folders=None) -> Path:
    """Make ``<parent>/<name>/<name>.sciproj`` with empty folders in the project tree and return the file.
    ``folders``: names or ``"A/B"`` paths (default ``DEFAULT_FOLDERS``; an empty list gives none).
    Refuses to touch an existing project."""
    name = clean_name(name)
    folder = Path(parent).expanduser() / name
    path = folder / (name + ".sciproj")
    if path.exists():
        raise ValueError(f"{path} already exists")
    wanted = DEFAULT_FOLDERS if folders is None else folders
    if isinstance(wanted, str):
        wanted = [f for f in wanted.split(",")]
    pm = ProjectModel()
    for f in wanted:
        parts = split_folder_path(str(f).strip())
        if parts:
            pm.tree = tree_folder_edit(pm.tree, parts, "create")
    folder.mkdir(parents=True, exist_ok=True)
    pm.save(str(path.resolve()), {})
    return path.resolve()
