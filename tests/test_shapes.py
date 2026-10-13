"""Drawn objects (shapes): model stack, gradients, offline API, and the GUI layer list."""
import os

import numpy as np
import pytest

from NoorSuite.model import (ShapeRef, SubplotModel, TraceRef, gradient_rgba, make_shape,
                             normalize_stops)


def _sub_with_traces(n=2):
    sp = SubplotModel()
    sp.traces = [TraceRef("d", "x", f"y{i}") for i in range(n)]
    return sp


def test_stack_interleaves_shapes_and_roundtrips():
    sp = _sub_with_traces(2)
    r = make_shape("rect", {"x0": 1, "x1": 3})
    sp.shapes = [r]
    assert sp.stack()[-1] is r                           # new shapes sit on top
    sp.set_stack([sp.traces[0], r, sp.traces[1]])
    assert r.layer_pos == 1 and sp.stack()[1] is r
    sp.set_stack([r, sp.traces[0], sp.traces[1]])        # to the back
    assert r.layer_pos == 0
    back = SubplotModel.from_dict(sp.to_dict())
    assert [type(i).__name__ for i in back.stack()] == ["ShapeRef", "TraceRef", "TraceRef"]


def test_removing_a_trace_keeps_shapes_between_the_others():
    sp = _sub_with_traces(3)
    r = make_shape("ellipse")
    sp.shapes = [r]
    sp.set_stack([sp.traces[0], sp.traces[1], r, sp.traces[2]])
    sp.remove_trace(sp.traces[0])
    st = sp.stack()
    assert [type(i).__name__ for i in st] == ["TraceRef", "ShapeRef", "TraceRef"]


def test_set_stack_rejects_a_foreign_list():
    sp = _sub_with_traces(2)
    with pytest.raises(ValueError):
        sp.set_stack([sp.traces[0]])


def test_gradient_interpolates_colour_and_transparency():
    img = gradient_rgba([[0.0, "#ff0000", 1.0], [1.0, "#0000ff", 0.0]], "linear", 0, n=8)
    left, right = img[4, 0], img[4, -1]
    assert left[0] > 0.85 and left[2] < 0.15 and left[3] > 0.85
    assert right[2] > 0.85 and right[0] < 0.15 and right[3] < 0.15
    # vertical direction runs bottom -> top
    up = gradient_rgba(["#000000", "#ffffff"], "linear", 90, n=8)
    assert up[0, 4, 0] < up[-1, 4, 0]
    # radial: centre = first stop, corner = last stop
    rad = gradient_rgba(["#ffffff", "#000000"], "radial", radius=0.5, n=9)
    assert rad[4, 4, 0] > 0.9 and rad[0, 0, 0] < 0.1
    assert normalize_stops(["#a", "#b", "#c"])[1][0] == 0.5


def test_offline_add_shape_and_front_back(tmp_path):
    import pandas as pd
    from NoorSuite.client import OfflineClient
    from NoorSuite.model import ProjectModel
    suite = OfflineClient(tmp_path / "p.sciproj")
    suite.push_dataframe(pd.DataFrame({"x": [1.0, 2.0, 3.0], "y": [1.0, 4.0, 9.0]}), name="d")
    suite.plot("d", "x", "y", new_sheet=True, title="Fig")
    suite.add_shape("Fig", "rect", x0=1, x1=2, y0=0, y1=5, fill="linear",
                    colors=["#ffffff", "#ff0000"], alpha=0.8, name="Highlight",
                    position="back")
    suite.add_shape("Fig", "circle", x0=1.5, x1=2.5, y0=1, y1=6, fill="radial", cmap="viridis")
    suite.add_shape("Fig", "arrow", x0=1, y0=1, x1=3, y1=9, edge_color="#000000")
    with pytest.raises(ValueError):
        suite.add_shape("Fig", "pentagon")
    sub = ProjectModel.load(tmp_path / "p.sciproj").sheets[0].subplots[0]
    assert [type(i).__name__ for i in sub.stack()] == [
        "ShapeRef", "TraceRef", "ShapeRef", "ShapeRef"]      # the rect went to the back
    assert sub.stack()[0].name == "Highlight" and sub.stack()[0].alpha == 0.8
    assert sub.shapes[1].kind == "ellipse" and sub.shapes[1].fill_mode == "radial"
    assert len(sub.shapes[1].grad_stops) == 5
    assert sub.shapes[0].grad_stops[1][1] == "#ff0000"
    suite.clear_shapes("Fig")
    assert ProjectModel.load(tmp_path / "p.sciproj").sheets[0].subplots[0].shapes == []


def test_render_draws_gradient_and_hit_test_reports_shape(app_qt):
    from NoorSuite.model import DataObject, SheetModel
    from NoorSuite.sheet import PlotSheet
    d = DataObject("d", {"x": np.arange(5.0), "y": np.arange(5.0) ** 2})
    sm = SheetModel("s")
    sub = sm.subplots[0]
    sub.traces = [TraceRef(d.id, "x", "y")]
    sub.shapes = [make_shape("rect", {"x0": 1, "x1": 3, "y0": 2, "y1": 9},
                             {"fill_mode": "linear", "fill_alpha": 1.0}),
                  make_shape("text", {"x0": 2, "y0": 12}, {"text": "$unclosed"})]
    ps = PlotSheet(sm)
    ps.render({d.id: d})
    ps.canvas.draw()                                       # an unbalanced $ must not raise
    assert any(isinstance(v, ShapeRef) for v in ps.artist_map.values())
    ax = ps._axes[0]
    assert len(ax.get_images()) == 1                       # the gradient layer
    # limits are driven by the data, not by the shapes
    assert ax.get_ylim()[1] < 20


from tests.test_gui_features import app, win  # noqa: E402,F401  (window fixtures)


@pytest.fixture(scope="module")
def app_qt():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


# ------------------------------------------------------------------ imported figure files
_SVG = (b'<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20" viewBox="0 0 40 20">'
        b'<rect width="40" height="20" fill="#2060d0"/><circle cx="20" cy="10" r="8" '
        b'fill="#ffd000"/></svg>')
_SVG2 = _SVG.replace(b"#ffd000", b"#00c000")


def test_asset_roundtrip_in_project_and_offline_picture(tmp_path):
    import pandas as pd
    from NoorSuite.client import OfflineClient
    from NoorSuite.model import ProjectModel
    suite = OfflineClient(tmp_path / "p.sciproj")
    suite.push_dataframe(pd.DataFrame({"x": [1.0, 2.0], "y": [1.0, 2.0]}), name="d")
    suite.plot("d", "x", "y", new_sheet=True, title="Fig")
    suite.push_asset(_SVG, name="logo", ext="svg")
    suite.add_shape("Fig", "picture", asset="logo", x0=1, x1=1.5, y0=1, y1=1.5)
    pm = ProjectModel.load(tmp_path / "p.sciproj")
    assert [a.name for a in pm.assets] == ["logo"] and pm.assets[0].data == _SVG
    pic = pm.sheets[0].subplots[0].shapes[0]
    assert pic.kind == "picture" and pic.asset_id == pm.assets[0].asset_id
    # pushing the same name again replaces the bytes and bumps the version
    suite.push_asset(_SVG2, name="logo", ext="svg")
    pm = ProjectModel.load(tmp_path / "p.sciproj")
    assert pm.assets[0].data == _SVG2 and pm.assets[0].version == 2
    assert list(suite.list_assets()["name"]) == ["logo"]
    with pytest.raises(ValueError):
        suite.add_shape("Fig", "picture")                 # no asset given
    with pytest.raises(ValueError):
        suite.push_asset(b"x", ext="exe")


def test_picture_shape_renders_and_svg_rasterises(app_qt):
    from NoorSuite import assets
    from NoorSuite.model import AssetModel, DataObject, SheetModel
    from NoorSuite.sheet import PlotSheet
    a = AssetModel("logo", "svg", _SVG)
    arr = assets.raster(a, 200)
    assert arr is not None and arr.shape[2] == 4 and max(arr.shape[:2]) <= 200
    assert arr.shape[1] > arr.shape[0]                    # 40x20 -> wider than tall
    d = DataObject("d", {"x": np.arange(5.0), "y": np.arange(5.0)})
    sm = SheetModel("s")
    sub = sm.subplots[0]
    sub.traces = [TraceRef(d.id, "x", "y")]
    pic = make_shape("picture", {"x0": 1, "x1": 3, "y0": 1, "y1": 3})
    pic.asset_id = a.asset_id
    sub.shapes = [pic]
    ps = PlotSheet(sm)
    ps.assets = {a.asset_id: a}
    ps.render({d.id: d})
    ps.canvas.draw()
    assert len(ps._axes[0].get_images()) == 1
    # a picture is hit-testable like any object
    assert any(v is pic for v in ps.artist_map.values())


def test_latex_references_to_figure_files():
    from NoorSuite import latex
    from NoorSuite.model import AssetModel
    src = r"\noorfile[width=5cm]{my-logo} text \noorfile{b} % \noorfile{ignored}"
    assert latex.asset_refs(src) == ["my-logo", "b"]
    a = AssetModel("My Logo", "svg", b"")
    assert latex.resolve_asset("my-logo", [a]) is a
    assert latex.resolve_asset(a.asset_id, [a]) is a
    assert r"\noorfile[width=0.8" in latex.asset_snippet(a.asset_id, "My Logo")
    assert r"\newcommand{\noorfile}" in latex.STY


def test_window_imports_edits_and_reloads_figure(win, tmp_path):
    from NoorSuite import assets
    from NoorSuite.model import ProjectModel
    svg = tmp_path / "logo.svg"
    svg.write_bytes(_SVG)
    a = win.import_asset(str(svg))
    assert a.name == "logo" and a.ext == "svg" and a.version == 1
    win.handle_incoming_ipc({"action": "append_dataframe", "name": "d",
                             "columns": {"x": [1., 2.], "y": [1., 2.]},
                             "column_order": ["x", "y"]})
    win.handle_incoming_ipc({"action": "add_to_sheet", "data_name": "d", "x_col": "x",
                             "y_cols": ["y"], "sheet": "__new__", "subplot_index": 0})
    sm = next(reversed(win.sheets.values()))
    win._open_sheet_tab(sm.sheet_id)
    win._add_picture(a.asset_id)
    fp1 = win.sheet_fingerprint(sm.sheet_id)
    # simulate the editor: it saves a new version of the working file
    work = assets.materialize(a)
    win._asset_paths[str(work)] = a.asset_id
    work.write_bytes(_SVG2)
    win._reload_asset_file(str(work))
    assert a.version == 2 and a.data == _SVG2
    assert win.sheet_fingerprint(sm.sheet_id) != fp1      # documents / thumbnails refresh
    # saved with the project and restored
    p = tmp_path / "proj.sciproj"
    win._project_model().save(p)
    assert ProjectModel.load(p).assets[0].data == _SVG2
