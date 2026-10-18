"""Window / sidebar behaviour on small screens, new default font sizes, and size agreement end to end
(screen figure <-> exported PDF <-> the PDF of a compiled document)."""
import numpy as np
import pytest

from NoorSuite import latex
from NoorSuite.model import DataObject, SheetModel, SubplotModel, TraceRef
from NoorSuite.sheet import PlotSheet, export_sheet_pdf
from tests.test_gui_features import app, win, _doc_win  # noqa: F401  (window fixtures)


# ------------------------------------------------------------------ sidebar
def test_window_fits_the_screen_and_the_sidebar_is_reachable(win):
    from PyQt6.QtGui import QGuiApplication
    scr = QGuiApplication.primaryScreen().availableGeometry()
    assert win.width() <= scr.width() and win.height() <= scr.height()
    win.resize(900, 600)                                    # a small laptop window
    win.show()
    from PyQt6.QtWidgets import QApplication
    QApplication.processEvents()
    sp = win._main_splitter
    assert win._right_wrap.isVisible() and sp.sizes()[2] >= 270
    win.toggle_sidebar(False)
    assert not win._right_wrap.isVisible()
    win.toggle_sidebar(True)
    assert win._right_wrap.isVisible() and sp.sizes()[2] >= 270
    # nothing can be dragged away to zero width
    sp.setSizes([100, 700, 0])
    QApplication.processEvents()
    assert sp.sizes()[2] >= 270 and sp.sizes()[0] >= 190


def test_documents_hide_the_sidebar_and_sheets_bring_it_back(win):
    from PyQt6.QtWidgets import QApplication
    win.resize(1000, 650)
    win.show()
    ids, dm, tab = _doc_win(win, 1)
    QApplication.processEvents()
    win.tab_widget.setCurrentWidget(tab)
    assert not win._right_wrap.isVisible()
    sheet = win.open_tabs[ids[0]]
    win.tab_widget.setCurrentWidget(sheet)
    QApplication.processEvents()
    assert win._right_wrap.isVisible() and win._main_splitter.sizes()[2] >= 270
    win.toggle_sidebar(False)                               # the user's choice survives tab changes
    win.tab_widget.setCurrentWidget(tab)
    win.tab_widget.setCurrentWidget(sheet)
    assert not win._right_wrap.isVisible()
    win.reset_layout()
    assert win._right_wrap.isVisible() and win.sidebar_act.isChecked()


# ------------------------------------------------------------------ defaults
def test_new_subplots_use_the_larger_default_fonts():
    sub = SubplotModel("s")
    assert (sub.tick_label_size, sub.xlabel_fontsize, sub.ylabel_fontsize) == (13.0, 15.0, 15.0)
    assert sub.title_fontsize > sub.xlabel_fontsize          # a title stays the largest text
    assert SheetModel("new").fig_real_points is True                 # new sheets: points are points
    assert SheetModel.from_dict({"name": "saved long ago"}).fig_real_points is False
    again = SubplotModel.from_dict(sub.to_dict())
    assert again.tick_label_size == 13.0
    old = SubplotModel.from_dict({**sub.to_dict(), "tick_label_size": 9.0, "xlabel_fontsize": 10.0})
    assert (old.tick_label_size, old.xlabel_fontsize) == (9.0, 10.0)      # saved sheets keep theirs


# ------------------------------------------------------------------ size agreement
def _sheet(real_points=False, cm=None):
    sm = SheetModel("Sz", sheet_id="szfig")
    sub = sm.subplots[0]
    sub.title, sub.x_label, sub.y_label = "Ttl", "Xlabelxlabel", "Ylabel"
    obj = DataObject("d", {"x": np.linspace(0, 10, 50), "y": np.sin(np.linspace(0, 10, 50))})
    sub.traces.append(TraceRef(obj.id, "x", "y"))
    sm.fig_real_points = real_points
    if cm:
        sm.fig_width_cm, sm.fig_height_cm = cm
    return sm, {obj.id: obj}


def _text_width_pt(pdf_path, needle):
    """Width of ``needle`` in the PDF in points, measured from the text layer."""
    from PyQt6.QtPdf import QPdfDocument
    from PyQt6.QtGui import QGuiApplication
    d = QPdfDocument(QGuiApplication.instance())
    d.load(str(pdf_path))
    for page in range(d.pageCount()):
        text = d.getAllText(page).text()
        i = text.find(needle)
        if i >= 0:
            r = d.getSelectionAtIndex(page, i, len(needle)).boundingRectangle()
            w = (r.width(), d.pagePointSize(page).width())
            d.close()
            return w
    d.close()
    return None


@pytest.mark.parametrize("real", [False, True])
@pytest.mark.parametrize("cm", [None, (8.0, 6.0)])
def test_screen_figure_and_exported_pdf_have_the_same_text_proportions(app, tmp_path, real, cm):
    sm, repo = _sheet(real, cm)
    pdf = tmp_path / "sheet.pdf"
    export_sheet_pdf(sm, repo, {}, str(pdf))
    got = _text_width_pt(pdf, "Xlabelxlabel")
    assert got is not None
    pdf_ratio = got[0] / got[1]                              # label width / figure width
    ps = PlotSheet(sm)
    ps.resize(900, 560)
    ps.show()
    app.processEvents()
    ps.render(repo, {})
    ps.canvas.draw()
    label = ps._axes[0].xaxis.label
    bbox = label.get_window_extent(ps.canvas.get_renderer())
    screen_ratio = bbox.width / ps.fig.bbox.width
    assert screen_ratio == pytest.approx(pdf_ratio, rel=0.05), (screen_ratio, pdf_ratio)
    ps.close()


@pytest.mark.skipif(latex.find_engine() is None, reason="no LaTeX engine installed")
def test_a_compiled_document_shows_the_figure_text_at_the_placed_scale(app, tmp_path):
    sm, repo = _sheet(False, (12.0, 8.0))
    figs = tmp_path / "noorsuite-figures"
    figs.mkdir()
    export_sheet_pdf(sm, repo, {}, str(figs / "szfig.pdf"))
    base_w, base_page = _text_width_pt(figs / "szfig.pdf", "Xlabelxlabel")
    src = "\n".join([r"\documentclass{article}", r"\usepackage[a4paper,margin=2cm]{geometry}",
                     r"\usepackage{noorsuite}", r"\begin{document}",
                     r"\noorsheet{szfig}", r"\newpage", r"\noorsheet[width=6cm]{szfig}",
                     r"\end{document}", ""])
    latex.prepare_workdir(tmp_path, src)
    res = latex.build_pdf(tmp_path)
    assert res.ok, res.log[-600:]
    from PyQt6.QtPdf import QPdfDocument
    from PyQt6.QtGui import QGuiApplication
    d = QPdfDocument(QGuiApplication.instance())
    d.load(str(tmp_path / "out" / "main.pdf"))
    widths = []
    for page in range(d.pageCount()):
        text = d.getAllText(page).text()
        i = text.find("Xlabelxlabel")
        widths.append(d.getSelectionAtIndex(page, i, len("Xlabelxlabel")).boundingRectangle().width())
    d.close()
    assert widths[0] == pytest.approx(base_w, rel=0.03)            # natural size: the same points
    assert widths[1] == pytest.approx(base_w * 0.5, rel=0.03)      # 6 cm of 12 cm: half the size
