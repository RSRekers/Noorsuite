"""Colour-map sampling and file I/O (needs matplotlib).

A "spec" is either a built-in matplotlib colormap name (see :data:`BUILTIN_NAMES`) or a
:class:`~NoorSuite.model.ColorMap` instance holding an explicit list of hex colours.
:func:`sample` turns a spec into a concrete list of ``n`` hex strings.
"""
from __future__ import annotations

import json

import numpy as np
from matplotlib import colormaps as _mpl_colormaps
from matplotlib.colors import to_hex

from .model import ColorMap

BUILTIN_NAMES = ["tab10", "tab20", "Set2", "Dark2", "viridis", "plasma",
                 "cividis", "coolwarm", "turbo"]

# Qualitative built-ins have a fixed, small palette -- take/repeat entries rather
# than interpolate across the full range.
_QUALITATIVE = {"tab10": 10, "tab20": 20, "Set2": 8, "Dark2": 8}


def _resolve_name(spec) -> str:
    return spec if isinstance(spec, str) else getattr(spec, "name", "")


def sample(spec, n: int) -> list[str]:
    """Return ``n`` hex colours for ``spec`` (a built-in name or a :class:`ColorMap`)."""
    n = max(int(n), 0)
    if n == 0:
        return []

    if isinstance(spec, ColorMap) and not spec.builtin:
        base = spec.colors or ["#1f77b4"]
        if len(base) >= n:
            return list(base[:n])
        # cycle the explicit palette
        return [base[i % len(base)] for i in range(n)]

    name = _resolve_name(spec) or "tab10"
    try:
        cmap = _mpl_colormaps[name]
    except (KeyError, ValueError):
        cmap = _mpl_colormaps["tab10"]

    if name in _QUALITATIVE:
        size = _QUALITATIVE[name]
        return [to_hex(cmap(i % size)) for i in range(n)]

    if n == 1:
        return [to_hex(cmap(0.0))]
    return [to_hex(cmap(t)) for t in np.linspace(0.0, 1.0, n)]


def available_specs(sheet_model=None, project_colormaps=None) -> list[str]:
    """Ordered list of selectable colormap names: built-ins, library, sheet-custom."""
    names = list(BUILTIN_NAMES)
    for cm in (project_colormaps or []):
        if cm.name not in names:
            names.append(cm.name)
    if sheet_model is not None:
        for cm in sheet_model.colormaps:
            if cm.name not in names:
                names.append(cm.name)
    return names


def resolve_spec(name: str, sheet_model=None, project_colormaps=None):
    """Return a built-in name or a :class:`ColorMap` for ``name`` (or ``None``)."""
    if not name:
        return None
    if name in BUILTIN_NAMES:
        return name
    if sheet_model is not None:
        for cm in sheet_model.colormaps:
            if cm.name == name:
                return cm
    for cm in (project_colormaps or []):
        if cm.name == name:
            return cm
    return None


def save_file(path: str, cmap: ColorMap) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"scicmap": 1, **cmap.to_dict()}, fh, indent=2)


def load_file(path: str) -> ColorMap:
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    return ColorMap(d["name"], d.get("colors", []), builtin=False)
