"""The figure has one physical size everywhere: on screen, in the PDF / PNG and on the clipboard."""
import pytest

from NoorSuite.model import SheetModel
from NoorSuite.sheet import PlotSheet, logical_size_cm
from tests.test_gui_features import app  # noqa: F401  (QApplication fixture)


def _sheet(w=None, h=None, real=False):
    sm = SheetModel("S")
    sm.subplots[0].title = "Title"
    sm.subplots[0].title_fontsize = 12.0
    sm.fig_width_cm, sm.fig_height_cm, sm.fig_real_points = w, h, real
    return sm


def _screen(sm, px_w, px_h, app):
    ps = PlotSheet(sm)
    ps.resize(px_w, px_h)
    ps.show()
    app.processEvents()
    ps.render({}, {})
    ps._sync_zoom_dpi(draw=False)
    app.processEvents()
    return ps


def _export_title_ratio(sm):
    """Title font size / figure width (inches) of a fresh render at the export size."""
    from NoorSuite.model import duplicate_sheet_model
    c = duplicate_sheet_model(sm)
    w, h = logical_size_cm(c)
    c.fig_width_cm, c.fig_height_cm = w, h
    ps = PlotSheet(c)
    ps.render({}, {})
    ps.fig.set_size_inches(w / 2.54, h / 2.54, forward=False)
    ratio = ps._axes[0].title.get_fontsize() / ps.fig.get_figwidth()
    ps.deleteLater()
    return ratio


@pytest.mark.parametrize("size", [(None, None), (8.0, 6.0), (16.0, 10.0)])
@pytest.mark.parametrize("real", [False, True])
def test_screen_and_export_share_one_physical_size(app, size, real):
    sm = _sheet(*size, real=real)
    want = logical_size_cm(sm)
    ratios = []
    for px in ((520, 380), (1100, 760)):                 # two window sizes
        ps = _screen(sm, *px, app)
        w_in = ps.fig.get_figwidth()
        assert w_in == pytest.approx(want[0] / 2.54, rel=1e-6)           # physical width
        assert ps.fig.dpi * w_in == pytest.approx(ps.canvas.width() * ps.canvas.device_pixel_ratio, rel=1e-3)
        ratios.append(ps._axes[0].title.get_fontsize() / w_in)
        ps.close()
        ps.deleteLater()
    assert ratios[0] == pytest.approx(ratios[1], rel=1e-6)                # window size is irrelevant
    assert ratios[0] == pytest.approx(_export_title_ratio(sm), rel=1e-6)   # same as the PDF / PNG


def test_real_points_keep_the_font_size(app):
    sm = _sheet(10.0, 6.0, real=True)
    ps = _screen(sm, 700, 500, app)
    assert ps._axes[0].title.get_fontsize() == pytest.approx(12.0)         # no hidden scaling
    ps.close()
    legacy = _sheet(10.0, 6.0, real=False)
    ps = _screen(legacy, 700, 500, app)
    assert ps._axes[0].title.get_fontsize() < 12.0                         # older sheets scale down
    ps.close()


def test_new_field_survives_save_and_load():
    sm = _sheet(9.0, 6.0, real=True)
    again = SheetModel.from_dict(sm.to_dict())
    assert again.fig_real_points is True and again.fig_width_cm == 9.0
    assert SheetModel.from_dict({"name": "old"}).fig_real_points is False
