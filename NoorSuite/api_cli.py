"""``python -m NoorSuite api <command> ...`` -- every client action from the command line.

Same behaviour as the Python client: a running GUI (``--port``) is used live, otherwise the project
file (``--project``, default: the GUI's session file) is edited. ``insert`` (in ``insert_cli``)
stays the shortcut for "file -> data object -> plot"; this module covers the rest: listing, getting,
removing, organizing, shapes, heatmaps, mean +- band, documents, assets, tables, snippets and
references."""
from __future__ import annotations

import argparse
import json
import os
import sys

from .protocol import DEFAULT_PORT

LISTINGS = {"data": "list_data", "images": "list_images", "traces": "list_traces",
            "sheets": "list_sheets", "documents": "list_documents", "assets": "list_assets",
            "tables": "list_tables", "references": "list_references", "templates": "list_templates"}


def _csv_list(text):
    return [t.strip() for t in (text or "").split(",") if t.strip()]


def _show(df, as_json: bool) -> None:
    if as_json:
        print(df.to_json(orient="records", force_ascii=False))
    elif len(df) == 0:
        print("(none)")
    else:
        print(df.to_string(index=False))


def _read_table(path, sheet=None):
    from .insert_cli import _read_table as read
    return read(path, sheet)


def _sheet_args(sp):
    sp.add_argument("--sheet", help="existing sheet name/id")
    sp.add_argument("--new-sheet", action="store_true")
    sp.add_argument("--subplot", type=int, default=0)
    sp.add_argument("--title", help="name for a new sheet")
    sp.add_argument("--tags", default="")
    sp.add_argument("--notes")
    sp.add_argument("--folder")
    sp.add_argument("--subplot-title")
    sp.add_argument("--x-label")
    sp.add_argument("--y-label")


def _sheet_kw(a):
    return dict(sheet=a.sheet, subplot=a.subplot, new_sheet=a.new_sheet or not a.sheet,
                title=a.title, tags=_csv_list(a.tags), notes=a.notes, folder=a.folder,
                subplot_title=a.subplot_title, x_label=a.x_label, y_label=a.y_label)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="noorsuite api", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", help="offline only: .sciproj file (default: session file)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="list what the project holds")
    p.add_argument("what", choices=sorted(LISTINGS))
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("get", help="print / save a data object as CSV")
    p.add_argument("key")
    p.add_argument("--out")

    p = sub.add_parser("remove", help="delete data objects / images or sheets")
    p.add_argument("what", choices=["data", "sheet"])
    p.add_argument("keys", nargs="+")

    p = sub.add_parser("clear", help="remove everything from the project")
    p.add_argument("--yes", action="store_true", help="required: this deletes all data and sheets")

    p = sub.add_parser("annotate", help="tags / notes of a data object")
    p.add_argument("key")
    p.add_argument("--tags", default="")
    p.add_argument("--notes")
    p.add_argument("--replace-notes", action="store_true")

    p = sub.add_parser("organize", help="name / tag / annotate / file an existing sheet")
    p.add_argument("sheet")
    p.add_argument("--title")
    p.add_argument("--tags", default="")
    p.add_argument("--notes")
    p.add_argument("--replace-notes", action="store_true")
    p.add_argument("--folder")
    p.add_argument("--subplot", type=int, default=0)
    p.add_argument("--subplot-title")
    p.add_argument("--x-label")
    p.add_argument("--y-label")
    p.add_argument("--x-tick-labels")
    p.add_argument("--y-tick-labels")
    p.add_argument("--content-only", action="store_true", default=None)

    p = sub.add_parser("shape", help="draw an object on a sheet")
    p.add_argument("sheet")
    p.add_argument("kind", choices=["rect", "ellipse", "line", "arrow", "xspan", "yspan", "text",
                                    "picture"])
    p.add_argument("--subplot", type=int, default=0)
    for k in ("x0", "x1", "y0", "y1"):
        p.add_argument(f"--{k}", type=float)
    p.add_argument("--text")
    p.add_argument("--coords", choices=["data", "axes"], default="data")
    p.add_argument("--position", choices=["front", "back"], default="front")
    p.add_argument("--name")
    p.add_argument("--fill", help="#rrggbb, or linear / radial with --colors / --cmap")
    p.add_argument("--colors", help="comma-separated gradient colours")
    p.add_argument("--cmap")
    p.add_argument("--gradient-angle", type=float)
    p.add_argument("--asset", help="pushed asset name (kind picture)")
    p.add_argument("--parent")
    p.add_argument("--scale", type=float)
    p.add_argument("--edge-color")
    p.add_argument("--line-width", type=float)
    p.add_argument("--line-style")
    p.add_argument("--alpha", type=float)
    p.add_argument("--fill-alpha", type=float)
    p.add_argument("--rotation", type=float)
    p.add_argument("--fontsize", type=float)

    p = sub.add_parser("clear-shapes", help="remove every drawn object of a subplot")
    p.add_argument("sheet")
    p.add_argument("--subplot", type=int, default=0)

    p = sub.add_parser("heatmap", help="show a grid (.csv: index = y, header = x; .npy) or long "
                                       "x,y,z samples as a heatmap")
    p.add_argument("file")
    p.add_argument("--name")
    p.add_argument("--xyz", metavar="X,Y,Z", help="the file holds one x, y, z triple per row")
    p.add_argument("--cmap")
    p.add_argument("--reverse-cmap", action="store_true", default=None)
    p.add_argument("--vmin", type=float)
    p.add_argument("--vmax", type=float)
    p.add_argument("--bins", type=int)
    p.add_argument("--boundaries", help="comma-separated band edges")
    p.add_argument("--no-colorbar", action="store_true")
    p.add_argument("--interpolation")
    p.add_argument("--isolines", help="'auto', a count, or comma-separated levels")
    p.add_argument("--iso-above", type=float)
    p.add_argument("--iso-below", type=float)
    p.add_argument("--iso-color")
    p.add_argument("--iso-width", type=float)
    p.add_argument("--iso-labels", action="store_true", default=None)
    p.add_argument("--xticks", help="'data', N, or comma-separated positions")
    p.add_argument("--yticks")
    _sheet_args(p)

    p = sub.add_parser("mean", help="plot repeats as their mean with a shaded band")
    p.add_argument("data", nargs="+", help="ONE data object (with --y a,b,c) or several (--y col)")
    p.add_argument("--x")
    p.add_argument("--y", required=True)
    p.add_argument("--band", choices=["std", "sem", "minmax", "none"], default="std")
    p.add_argument("--errorbars", action="store_true")
    p.add_argument("--band-alpha", type=float, default=0.25)
    p.add_argument("--label")
    p.add_argument("--plot-type")
    _sheet_args(p)

    p = sub.add_parser("document", help="create / replace / extend a LaTeX document")
    p.add_argument("name")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--file", help="a .tex file")
    g.add_argument("--text", help="the LaTeX source")
    p.add_argument("--mode", choices=["replace", "append"], default="replace")
    p.add_argument("--folder")
    p.add_argument("--template", help="article | acs | rsc | elsevier | beamer | user:<name>")
    p.add_argument("--doc-title")
    p.add_argument("--abstract", default="")
    p.add_argument("--authors", help="semicolon-separated names")

    p = sub.add_parser("get-document", help="print / save the LaTeX source of a document")
    p.add_argument("key")
    p.add_argument("--out")

    p = sub.add_parser("si-document", help="create the Supporting Information of a paper")
    p.add_argument("main")
    p.add_argument("--name")
    p.add_argument("--doc-title")

    p = sub.add_parser("asset", help="store a figure file (SVG / PDF / PNG / JPG)")
    p.add_argument("file")
    p.add_argument("--name")

    p = sub.add_parser("table", help="define a live table of a data object")
    p.add_argument("name")
    p.add_argument("data")
    p.add_argument("--columns", help="comma-separated column names")
    p.add_argument("--rows", metavar="FIRST:LAST")
    p.add_argument("--step", type=int, default=1)
    p.add_argument("--max-rows", type=int, default=0)
    p.add_argument("--sort-by", default="")
    p.add_argument("--descending", action="store_true")
    p.add_argument("--no-booktabs", action="store_true")
    p.add_argument("--digits", type=int)
    p.add_argument("--fmt", choices=["auto", "fixed", "sig", "sci", "int"])

    p = sub.add_parser("snippet", help="print the LaTeX figure / table environment")
    p.add_argument("what", choices=["figure", "table"])
    p.add_argument("key")
    p.add_argument("--caption", default="")
    p.add_argument("--label", default="")
    p.add_argument("--width", default="")

    p = sub.add_parser("references", help="add bibliography records")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--file", help=".bib or CSL-JSON file")
    g.add_argument("--zotero-keys", help="comma-separated citation keys from the Zotero library")
    g.add_argument("--zotero-search", metavar="QUERY", help="list matching library items")
    g.add_argument("--zotero-pick", action="store_true", help="open Zotero's picker")
    p.add_argument("--json", action="store_true")
    return ap


def run(a, suite) -> int:
    cmd = a.cmd
    if cmd == "list":
        _show(getattr(suite, LISTINGS[a.what])(), a.json)
    elif cmd == "get":
        df = suite.get_data(a.key)
        if a.out:
            df.to_csv(a.out, index=False)
            print(f"wrote {len(df)} rows to {a.out}")
        else:
            print(df.to_csv(index=False), end="")
    elif cmd == "remove":
        keys = a.keys if len(a.keys) > 1 else a.keys[0]
        (suite.remove_data if a.what == "data" else suite.remove_sheet)(keys)
        print(f"removed {a.what}: {', '.join(a.keys)}")
    elif cmd == "clear":
        if not a.yes:
            print("clear deletes everything in the project; repeat with --yes")
            return 1
        suite.clear()
        print("cleared")
    elif cmd == "annotate":
        suite.annotate_data(a.key, tags=_csv_list(a.tags), notes=a.notes,
                            replace_notes=a.replace_notes)
        print(f"annotated {a.key!r}")
    elif cmd == "organize":
        suite.organize_sheet(a.sheet, title=a.title, tags=_csv_list(a.tags), notes=a.notes,
                             folder=a.folder, subplot=a.subplot, subplot_title=a.subplot_title,
                             x_label=a.x_label, y_label=a.y_label, replace_notes=a.replace_notes,
                             x_tick_labels=a.x_tick_labels, y_tick_labels=a.y_tick_labels,
                             content_only=a.content_only)
        print(f"organized {a.sheet!r}")
    elif cmd == "shape":
        style = {k: v for k, v in dict(
            edge_color=a.edge_color, line_width=a.line_width, line_style=a.line_style,
            alpha=a.alpha, fill_alpha=a.fill_alpha, rotation=a.rotation,
            fontsize=a.fontsize).items() if v is not None}
        suite.add_shape(a.sheet, a.kind, subplot=a.subplot, x0=a.x0, x1=a.x1, y0=a.y0, y1=a.y1,
                        text=a.text, coords=a.coords, position=a.position, name=a.name,
                        fill=a.fill, colors=_csv_list(a.colors) or None, cmap=a.cmap,
                        gradient_angle=a.gradient_angle, asset=a.asset, parent=a.parent,
                        scale=a.scale, **style)
        print(f"added {a.kind} to {a.sheet!r}")
    elif cmd == "clear-shapes":
        suite.clear_shapes(a.sheet, subplot=a.subplot)
        print(f"cleared the objects of {a.sheet!r}")
    elif cmd == "heatmap":
        return _heatmap(a, suite)
    elif cmd == "mean":
        return _mean(a, suite)
    elif cmd == "document":
        src = a.text if a.text is not None else open(a.file, encoding="utf-8").read()
        suite.push_document(a.name, src, mode=a.mode, folder=a.folder, template=a.template,
                            title=a.doc_title, abstract=a.abstract,
                            authors=[s.strip() for s in (a.authors or "").split(";") if s.strip()])
        print(f"document {a.name!r}: {len(src)} characters ({a.mode})")
    elif cmd == "get-document":
        src = suite.get_document(a.key)
        if a.out:
            with open(a.out, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(src)
            print(f"wrote {len(src)} characters to {a.out}")
        else:
            print(src, end="")
    elif cmd == "si-document":
        suite.push_si_document(a.main, a.name, title=a.doc_title)
        print(f"created the SI document of {a.main!r}")
    elif cmd == "asset":
        resp = suite.push_asset(a.file, a.name)
        print(f"asset {resp.get('name', a.name or os.path.basename(a.file))!r} stored"
              if isinstance(resp, dict) else "asset stored")
    elif cmd == "table":
        rows = None
        if a.rows:
            first, _, last = a.rows.partition(":")
            rows = (int(first or 1), int(last) if last else None)
        suite.push_table(a.name, a.data, columns=_csv_list(a.columns) or None, rows=rows,
                         step=a.step, max_rows=a.max_rows, sort_by=a.sort_by,
                         descending=a.descending, booktabs=not a.no_booktabs, digits=a.digits,
                         fmt=a.fmt)
        print(f"table {a.name!r} defined")
    elif cmd == "snippet":
        if a.what == "figure":
            print(suite.figure_snippet(a.key, a.caption, a.label, a.width))
        else:
            print(suite.table_snippet(a.key, a.caption, a.label))
    elif cmd == "references":
        return _references(a, suite)
    return 0


def _heatmap(a, suite) -> int:
    import numpy as np
    name = a.name or os.path.splitext(os.path.basename(a.file))[0]
    ext = os.path.splitext(a.file)[1].lower()
    if a.xyz:
        x, y, z = (c.strip() for c in a.xyz.split(","))
        suite.push_heatmap_xyz(_read_table(a.file), x, y, z, name=name)
        data = name
    elif ext == ".npy":
        data = np.load(a.file, allow_pickle=False)
    else:
        import pandas as pd
        data = pd.read_csv(a.file, index_col=0)
        data.columns = [_num(c) for c in data.columns]
    iso = a.isolines
    if iso is not None:
        iso = True if iso == "auto" else ([float(v) for v in iso.split(",")] if "," in iso
                                           else int(float(iso)))
    ticks = {}
    for k, v in (("xticks", a.xticks), ("yticks", a.yticks)):
        if v is not None:
            ticks[k] = v if v == "data" else ([float(t) for t in v.split(",")] if "," in v
                                                else int(float(v)))
    suite.heatmap(data, name=name, cmap=a.cmap, reverse_cmap=a.reverse_cmap, vmin=a.vmin,
                  vmax=a.vmax, bins=a.bins,
                  boundaries=[float(v) for v in a.boundaries.split(",")] if a.boundaries else None,
                  colorbar=not a.no_colorbar, interpolation=a.interpolation, isolines=iso,
                  iso_above=a.iso_above, iso_below=a.iso_below, iso_color=a.iso_color,
                  iso_width=a.iso_width, iso_labels=a.iso_labels, **ticks, **_sheet_kw(a))
    print(f"heatmap {name!r} shown")
    return 0


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return str(v)


def _mean(a, suite) -> int:
    ys = _csv_list(a.y)
    many = len(a.data) > 1
    try:
        out = suite.plot_mean(a.data if many else a.data[0], x=a.x, y=ys[0] if many else ys,
                              band=None if a.band == "none" else a.band, errorbars=a.errorbars,
                              label=a.label, plot_type=a.plot_type or "Line",
                              band_alpha=a.band_alpha, **_sheet_kw(a))
    except ValueError as exc:
        print("mean failed:", exc)
        return 1
    print(f"plotted {out!r}")
    return 0


def _references(a, suite) -> int:
    import pandas as pd
    if a.file:
        from . import zotero
        keys = suite.push_references(zotero.load_file(a.file))
        print(f"{len(keys)} reference(s): {', '.join(keys)}")
    elif a.zotero_keys:
        keys = suite.zotero_add(_csv_list(a.zotero_keys))
        print(f"{len(keys)} reference(s): {', '.join(keys)}")
    elif a.zotero_search is not None:
        _show(suite.zotero_search(a.zotero_search), a.json)
    else:
        print("waiting for Zotero's picker ...")
        keys = suite.zotero_pick()
        print(f"{len(keys)} reference(s): {', '.join(keys)}")
    return 0


def main(argv: list[str]) -> int:
    a = build_parser().parse_args(argv)
    from .client import connect
    suite = connect(a.project, a.port)
    try:
        return run(a, suite)
    except (ValueError, KeyError, OSError) as exc:
        print(f"{a.cmd} failed: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
