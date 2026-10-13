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


@pytest.fixture(scope="module")
def app_qt():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])
