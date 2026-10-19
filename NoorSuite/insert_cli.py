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


def _err_arg(text, ys):
    """``--yerr`` value -> what ``plot()`` takes: one column, a list in y order, or a dict
    (``y1=e1,y2=e2``)."""
    if not text:
        return None
    parts = [t.strip() for t in text.split(",") if t.strip()]
    if any("=" in p for p in parts):
        return dict(p.split("=", 1) for p in parts)
    return parts[0] if len(parts) == 1 and len(ys) == 1 else parts


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
    ap.add_argument("--x-tick-labels", help='custom tick text, e.g. "0=Low; 1=Mid; 2=High"')
    ap.add_argument("--y-tick-labels")
    ap.add_argument("--x-label")
    ap.add_argument("--y-label")
    ap.add_argument("--plot-type", help="Line | Scatter | Line+Scatter | Step | Bar")
    ap.add_argument("--sort", action="store_true", help="draw points in ascending-x order")
    ap.add_argument("--yerr", help="error column (one per --plot y column, comma-separated, or "
                                   "y=err pairs): error bars from the symmetric error")
    ap.add_argument("--yerr-low", help="column with the offset below the value (needs --yerr-high)")
    ap.add_argument("--yerr-high", help="column with the offset above the value")
    ap.add_argument("--yerr-mode", choices=["std", "sem", "minmax"],
                    help="which error the columns stand for (default std; minmax for low/high)")
    ap.add_argument("--no-errorbars", action="store_true",
                    help="with --yerr: no whiskers (use with --band)")
    ap.add_argument("--band", action="store_true", help="shaded band of the error around the line")
    ap.add_argument("--band-alpha", type=float, default=0.25)
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
            ycols = [c for c in ys.split(",") if c]
            try:
                resp = _plot(suite, name, x, ycols, a)
            except (ValueError, KeyError) as exc:
                print("plot failed:", exc)
                return 1
            if resp and resp.get("status") == "error":
                print("plot failed:", resp["message"])
                return 1
    return 0


def _plot(suite, name, x, ycols, a):
    return suite.plot(
        name, x=None if x in ("", "index") else x, y=ycols, sheet=a.sheet, subplot=a.subplot,
        new_sheet=a.new_sheet, title=a.title, tags=[t for t in a.sheet_tags.split(",") if t],
        notes=a.sheet_notes, folder=a.folder, subplot_title=a.subplot_title, x_label=a.x_label,
        y_label=a.y_label, plot_type=a.plot_type, sort=a.sort, x_tick_labels=a.x_tick_labels,
        y_tick_labels=a.y_tick_labels, yerr=_err_arg(a.yerr, ycols),
        yerr_low=_err_arg(a.yerr_low, ycols), yerr_high=_err_arg(a.yerr_high, ycols),
        yerr_mode=a.yerr_mode, errorbars=False if a.no_errorbars else None, band=a.band,
        band_alpha=a.band_alpha)
