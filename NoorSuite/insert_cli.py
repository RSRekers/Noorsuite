"""``python -m NoorSuite insert FILE ...`` -- one-shot data insertion (GUI optional)."""
from __future__ import annotations

import argparse
import os

from .protocol import DEFAULT_PORT


def _read_table(path: str, sheet_name=None):
    import pandas as pd
    ext = os.path.splitext(path)[1].lower()
    if ext == ".tsv":
        return pd.read_csv(path, sep="\t")
    if ext in (".csv", ".txt"):
        return pd.read_csv(path, sep=None, engine="python")   # sniff the delimiter
    if ext in (".xlsx", ".xls"):
        return pd.read_excel(path, sheet_name=sheet_name or 0)
    if ext == ".json":
        return pd.read_json(path)
    if ext == ".parquet":
        return pd.read_parquet(path)
    raise SystemExit(f"unsupported file type {ext!r}")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="noorsuite insert", description=__doc__)
    ap.add_argument("file")
    ap.add_argument("--name", help="data object name (default: file stem)")
    ap.add_argument("--tags", default="", help="comma-separated tags")
    ap.add_argument("--notes", default="", help="description stored on the data object")
    ap.add_argument("--title", help="name for the sheet")
    ap.add_argument("--sheet-tags", default="", help="comma-separated tags for the sheet")
    ap.add_argument("--sheet-notes", default="")
    ap.add_argument("--folder", help='folder path for the sheet, e.g. "Sample A3/Transport"')
    ap.add_argument("--subplot-title")
    ap.add_argument("--x-label")
    ap.add_argument("--y-label")
    ap.add_argument("--plot-type", help="Line | Scatter | Line+Scatter | Step | Bar")
    ap.add_argument("--sort", action="store_true", help="draw points in ascending-x order")
    ap.add_argument("--update", action="store_true", help="refresh an existing object in place")
    ap.add_argument("--plot", metavar="X:Y1,Y2", help="also plot these columns (X may be 'index')")
    ap.add_argument("--plot-image", action="store_true", help="show an inserted .npy image")
    ap.add_argument("--axes", help="comma-separated axis names for an image")
    ap.add_argument("--sheet", help="existing sheet name/id to plot on")
    ap.add_argument("--new-sheet", action="store_true")
    ap.add_argument("--subplot", type=int, default=0)
    ap.add_argument("--sheet-name", help="Excel worksheet to read")
    ap.add_argument("--project", help="offline only: .sciproj file (default: session file)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    a = ap.parse_args(argv)

    from .client import connect
    suite = connect(a.project, a.port)
    name = a.name or os.path.splitext(os.path.basename(a.file))[0]
    tags = [t for t in a.tags.split(",") if t]
    mode = "update" if a.update else "new"

    if a.file.lower().endswith(".npy"):
        import numpy as np
        arr = np.load(a.file, allow_pickle=False)
        suite.push_image(arr, name=name, tags=tags, mode=mode,
                         axis_names=a.axes.split(",") if a.axes else None)
        if a.plot_image:
            suite.show_image(name, sheet=a.sheet, subplot=a.subplot, new_sheet=a.new_sheet,
                             axes=(arr.ndim - 2, arr.ndim - 1))
        print(f"inserted image {name!r} {arr.shape}")
    else:
        df = _read_table(a.file, a.sheet_name)
        suite.push_dataframe(df, name=name, tags=tags, mode=mode, notes=a.notes)
        print(f"inserted data object {name!r}: {len(df)} rows")
        if a.plot:
            x, _, ys = a.plot.partition(":")
            resp = suite.plot(name, x=None if x in ("", "index") else x,
                              y=[c for c in ys.split(",") if c], sheet=a.sheet,
                              subplot=a.subplot, new_sheet=a.new_sheet, title=a.title,
                              tags=[t for t in a.sheet_tags.split(",") if t],
                              notes=a.sheet_notes, folder=a.folder,
                              subplot_title=a.subplot_title, x_label=a.x_label,
                              y_label=a.y_label, plot_type=a.plot_type, sort=a.sort)
            if resp and resp.get("status") == "error":
                print("plot failed:", resp["message"])
                return 1
    return 0
