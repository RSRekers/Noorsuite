"""Presenter console (notes, timer, laser) and preview -> source jumps (SyncTeX)."""
import pytest

from NoorSuite import latex, synctex
from NoorSuite import pptxexport as X
from NoorSuite.presenter import format_time
from tests.test_gui_features import app, win, _doc_win  # noqa: F401  (window fixtures)

_TALK = "\n".join([
    r"\documentclass{beamer}", r"\begin{document}",
    r"\begin{frame}{One}", r"\begin{itemize}[<+->]", r"\item a", r"\item b", r"\item c",
    r"\end{itemize}", r"\note{Say hello}", r"\end{frame}",
    r"\begin{frame}{Two}", "Plain text", r"\pause", "More text", r"\end{frame}", "",
    r"\begin{frame}{Three}", r"\only<2>{x}", r"\end{frame}",
    r"\end{document}", ""])


def test_page_map_counts_overlay_steps():
    P = X.parse_presentation(_TALK)
    assert [f["steps"] for f in P["frames"]] == [3, 2, 2]
    rows = X.page_map(P, 7)
    assert rows[0] == (0, 0, 3) and rows[2] == (0, 2, 3) and rows[3] == (1, 0, 2)
    assert rows[6] == (2, 1, 2)
    # page count that does not match -> one page per frame, never an exception
    assert X.page_map(P, 3) == [(0, 0, 1), (1, 0, 1), (2, 0, 1)]


def test_timer_format():
    assert format_time(65) == "01:05"
    assert format_time(3725) == "1:02:05"
    assert format_time(-30) == "-00:30"


def test_synctex_matching_and_locate(tmp_path):
    build = ["A", r"\begin{document}", "Hello world", "", "Tail"]
    src = ["A", "extra line", r"\begin{document}", "Hello world", "", "Tail"]
    assert synctex.match_line(build, src, 3) == 3             # shifted by an inserted line
    assert synctex.match_line(build, src, 4) in (3, 4, 5)     # blank line: nearby text
    data = {"inputs": {1: "/x/main.tex", 2: "/x/other.sty"},
            "pages": {1: [("g", 1, 5, 100.0, 200.0, 0, 0, 0), ("g", 2, 9, 100.0, 200.0, 0, 0, 0),
                          ("g", 1, 8, 100.0, 300.0, 0, 0, 0)]}}
    assert synctex.locate(data, 1, 105, 202) == 5             # other.sty is ignored
    assert synctex.locate(data, 1, 100, 299) == 8
    assert synctex.locate(data, 2, 0, 0) is None


@pytest.mark.skipif(latex.find_engine() is None, reason="no LaTeX engine installed")
def test_real_build_gives_synctex_and_jump(win, tmp_path):
    from PyQt6.QtGui import QTextCursor
    ids, dm, tab = _doc_win(win, 1)
    src = "\n".join([r"\documentclass{article}", r"\begin{document}",
                     "First paragraph line.", "", r"\section{Heading}", "Second paragraph here.",
                     r"\end{document}", ""])
    tab.editor.setPlainText(src)
    tab.workdir.mkdir(parents=True, exist_ok=True)
    latex.prepare_workdir(tab.workdir, src)
    res = latex.build_pdf(tab.workdir)
    assert res.ok, res.log[-500:]
    data = synctex.parse(tab.workdir / "out" / "main.synctex.gz")
    assert data is not None
    ys = [(r[4], r[2]) for r in data["pages"][1] if r[0] == "g" and r[2] in (3, 6)]
    y3 = next(y for y, ln in ys if ln == 3)
    tab.jump_to_source(0, 200.0, y3 - 3.0)
    cur = tab.editor.textCursor()
    assert cur.selectedText() == "First paragraph line."
    assert isinstance(QTextCursor, type)


def _pdf(path, n=3):
    from PyQt6.QtCore import QMarginsF, QSizeF
    from PyQt6.QtGui import QPageLayout, QPageSize, QPainter, QPdfWriter
    w = QPdfWriter(str(path))
    w.setPageSize(QPageSize(QSizeF(160, 100), QPageSize.Unit.Millimeter))
    w.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Millimeter)
    p = QPainter(w)
    for i in range(n):
        if i:
            w.newPage()
        p.drawText(100, 100, f"Slide {i + 1}")
    p.end()


def test_presenter_console_follows_the_show(app, tmp_path):
    from NoorSuite.docview import PresentationView
    from NoorSuite.presenter import PresenterConsole
    pdf = tmp_path / "s.pdf"
    _pdf(pdf, 3)
    view = PresentationView(str(pdf), 0)
    view.resize(480, 300)
    view.show()
    pages = [(0, 0, 2), (0, 1, 2), (1, 0, 1)]
    con = PresenterConsole(view, str(pdf), pages, {0: "Remember the demo", 1: ""},
                           {0: "Intro", 1: "Results"})
    con.show()
    assert "Slide 1 / 3" in con.info_label.text() and "step 1 / 2" in con.info_label.text()
    assert con.notes_view.toPlainText() == "Remember the demo"
    assert "same slide" in con.next_label.text()
    view.go(2)                                          # the audience view moved on
    assert "Slide 3 / 3" in con.info_label.text()
    assert con.notes_view.toPlainText() == ""
    # laser: only drawn once switched on, position comes from the console or the show itself
    assert view._laser_pos is None
    con.laser_btn.setChecked(True)
    view.laser_at(0.25, 0.5)
    assert view._laser and view._laser_pos == (0.25, 0.5)
    view.laser_hide()
    assert view._laser_pos is None
    # timer: pause keeps the value, reset starts again
    con.pause_btn.setChecked(True)
    t = con.elapsed()
    assert con.elapsed() == t
    con.reset_timer()
    assert con.elapsed() == 0.0
    con.target.setValue(1)
    con._update_clock()
    assert con.timer_label.text() == "01:00"
    con.close()
    assert not view.isVisible()
