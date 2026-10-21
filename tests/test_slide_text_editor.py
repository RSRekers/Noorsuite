r"""Slide editor: click ordinary slide text to restyle it, slide-wide sizes, the panel is never grayed."""
import pytest

from NoorSuite import latex
from NoorSuite import slideobjects as so
from tests.test_gui_features import _doc_win, app, win  # noqa: F401  (window fixtures)

CLEAN = "\n".join([
    r"\documentclass[aspectratio=1610,11pt,t]{beamer}", r"\usetheme{noorclean}",
    r"\usepackage{noorsuite}", r"\title{T}\author{A}\date{D}", r"\begin{document}",
    r"\begin{frame}[plain]\titlepage\end{frame}",
    r"\begin{frame}{Results here}", r"\begin{itemize}", r"\item First point", r"\item Second point",
    r"\end{itemize}", r"\framesource{Source: me}", r"\end{frame}", r"\end{document}", ""])


@pytest.mark.skipif(latex.find_engine() is None, reason="no LaTeX engine installed")
def test_click_slide_text_restyles_it_and_the_panel_is_never_grayed(win):
    ids, dm, tab = _doc_win(win, 1)
    src = CLEAN
    tab.editor.setPlainText(src)
    latex.prepare_workdir(tab.workdir, src)
    res = latex.build_pdf(tab.workdir)
    assert res.ok, res.log[-800:]
    tab._pdf = res.pdf
    tab.built_source = src
    ed = tab.slide_editor
    ed.set_frame(1)
    # nothing selected: the slide-level controls are usable, the object form is simply not shown
    assert not ed.props.isVisibleTo(ed)
    assert ed.title_size.isEnabled() and ed.body_size.isEnabled()
    # click the item text on the compiled page (cm from the page's top-left)
    page = ed._frame_page(src)
    text = ed._doc.getAllText(page).text()
    i = text.find("Second point")
    r = ed._doc.getSelectionAtIndex(page, i, 6).boundingRectangle()
    k = 2.54 / 72.0
    ed._page_clicked((r.x() + r.width() / 2) * k, (r.y() + r.height() / 2) * k, False)
    assert ed._text_line is not None
    assert "Second point" in tab.source().split("\n")[ed._text_line]
    assert ed.t_size.isEnabled() and ed.canvas.marker is not None
    ed.t_size.setValue(30)
    line = tab.source().split("\n")[ed._text_line]
    assert line.startswith(r"\item {\fontsize{30}") and "Second point" in line
    ed.t_bold.setChecked(True)
    assert r"\bfseries" in tab.source().split("\n")[ed._text_line]
    # sizes for the whole slide
    ed.title_size.setValue(28)
    ed.body_size.setValue(22)
    s = tab.source()
    assert r"{\fontsize{28}" in s.split("\n")[6] and r"\noorfont{body=22}" in s
    ed._sizes_for_all()
    setup = so.read_setup(tab.source())
    assert setup.get("title-size") == "28" and setup.get("body-size") == "22"
    # an object shows its form once it exists / is selected
    ed.add_object("rect")
    assert ed.props.isVisibleTo(ed)


def test_canvas_reports_clicks_on_empty_page_and_outlines_what_is_under_the_mouse():
    from PyQt6.QtCore import QPointF, Qt
    from PyQt6.QtGui import QMouseEvent
    from PyQt6.QtWidgets import QApplication
    from NoorSuite.slidecanvas import SlideCanvas
    QApplication.instance() or QApplication([])
    c = SlideCanvas()
    c.resize(800, 500)
    c.set_state([{"kind": "rect", "x": 2.0, "y": 2.0, "w": 3.0, "h": 2.0, "arg": ""}], (160.0, 100.0))
    c._layout()
    seen = []
    c.pageClicked.connect(lambda x, y, d: seen.append((round(x, 1), round(y, 1), d)))
    far = c.to_px(100, 80)                                         # empty part of the page (mm)
    over = c.to_px(35, 30)                                         # inside the rectangle (mm)

    def ev(kind, p):
        return QMouseEvent(kind, p, p, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                           Qt.KeyboardModifier.NoModifier)
    c.mouseMoveEvent(QMouseEvent(QMouseEvent.Type.MouseMove, over, over, Qt.MouseButton.NoButton,
                                 Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
    assert c.hover == 0
    c.mousePressEvent(ev(QMouseEvent.Type.MouseButtonPress, far))
    c.mouseReleaseEvent(ev(QMouseEvent.Type.MouseButtonRelease, far))
    c.mouseDoubleClickEvent(ev(QMouseEvent.Type.MouseButtonDblClick, far))
    assert seen == [(10.0, 8.0, False), (10.0, 8.0, True)] and c.sel == -1
