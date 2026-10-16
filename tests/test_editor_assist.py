"""LaTeX autocomplete, bracket wrapping / closing and picture paste in the document editor."""
import pytest

from NoorSuite import latexcomplete as lc
from tests.test_gui_features import app, win, _doc_win  # noqa: F401  (window fixtures)
from tests.test_presentation import _png_bytes


def test_completion_contexts():
    start, items = lc.complete(r"text \beg")
    assert start == 5 and items[0].label == r"\begin"
    start, items = lc.complete(r"\begin{equ")
    assert [i.label for i in items][:2] == ["equation", "equation*"]
    assert items[0].insert.startswith("equation}") and r"\end{equation}" in items[0].insert
    # \end{ offers the environments that are still open, innermost first
    _s, items = lc.complete("\\begin{itemize}\n\\begin{figure}\n\\end{")
    assert [i.label for i in items] == ["figure", "itemize"]
    _s, items = lc.complete(r"as shown in \cite{ab", {"cite": [("abc2020", "2020 A"), ("xyz", "")]})
    assert [i.label for i in items] == ["abc2020"]
    _s, items = lc.complete(r"\cite{abc2020,x", {"cite": [("abc2020", ""), ("xyz", "")]})
    assert [i.label for i in items] == ["xyz"]
    _s, items = lc.complete(r"see \ref{fi", {"labels": ["fig:a", "tab:b"]})
    assert [i.label for i in items] == ["fig:a"]
    _s, items = lc.complete(r"\noorsheet{", {"sheets": [("s1", "Fig A")]})
    assert items and items[0].insert == "s1"
    _s, items = lc.complete(r"\usepackage{siu")
    assert items[0].label == "siunitx"
    _s, items = lc.complete(r"\noorobj[x=1,fil")
    assert [i.label for i in items] == ["fill=", "fill2="]
    assert lc.complete(r"% \begin{equ")[1] == []                       # comments are left alone
    assert lc.complete("plain text")[1] == []


def test_snippet_expansion_and_tab_stops():
    text, stops = lc.expand("\\frac{$1}{${2:b}}$0", "")
    assert text == "\\frac{}{b}"
    assert stops == [(6, 6), (8, 9), (10, 10)]
    text, stops = lc.expand("a\n  $1\nb$0", "    ")
    assert text == "a\n      \n    b" and stops[0] == (8, 8)


def _type(ed, text):
    from PyQt6.QtTest import QTest
    QTest.keyClicks(ed, text)


def test_wrapping_and_auto_closing(win):
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    ids, dm, tab = _doc_win(win, 1)
    ed = tab.editor
    ed.autocomplete = False

    def select(a, b):
        c = ed.textCursor()
        c.setPosition(a)
        c.setPosition(b, c.MoveMode.KeepAnchor)
        ed.setTextCursor(c)
    ed.setPlainText("hello world")
    select(0, 5)
    _type(ed, "$")
    assert ed.toPlainText() == "$hello$ world"
    _type(ed, "$")                                   # the inner text stays selected: nests
    assert ed.toPlainText() == "$$hello$$ world"
    ed.setPlainText("a word b")
    select(2, 6)
    _type(ed, '"')
    assert ed.toPlainText() == 'a "word" b'
    select(3, 7)
    _type(ed, "(")
    assert ed.toPlainText() == 'a "(word)" b'
    ed.setPlainText("x")
    select(0, 1)
    _type(ed, "q")                                   # ordinary characters still replace
    assert ed.toPlainText() == "q"
    # auto-closing
    ed.setPlainText("")
    _type(ed, "f(a")
    assert ed.toPlainText() == "f(a)"
    _type(ed, ")")                                   # types over the closer
    assert ed.toPlainText() == "f(a)"
    ed.setPlainText("")
    _type(ed, "{")
    assert ed.toPlainText() == "{}"
    QTest.keyClick(ed, Qt.Key.Key_Backspace)
    assert ed.toPlainText() == ""
    ed.autoclose = False
    _type(ed, "(")
    assert ed.toPlainText() == "("


def test_autocomplete_popup_navigation_and_tab(win):
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    ids, dm, tab = _doc_win(win, 1)
    ed = tab.editor
    ed.autoclose = True
    ed.setPlainText("")
    ed.setFocus()
    _type(ed, r"\begin{equ")
    assert ed.popup_visible()
    assert ed._popup.item(0).text().startswith("equation")
    QTest.keyClick(ed, Qt.Key.Key_Down)               # equation*
    QTest.keyClick(ed, Qt.Key.Key_Up)
    QTest.keyClick(ed, Qt.Key.Key_Tab)
    text = ed.toPlainText()
    assert text.startswith("\\begin{equation}\n  ") and text.rstrip().endswith("\\end{equation}")
    assert text.count("}") == text.count("{")         # the auto-closed brace was swallowed
    assert not ed.popup_visible()
    # first tab stop: the equation body; Tab goes on to the label field
    QTest.keyClicks(ed, "E=mc^2")
    QTest.keyClick(ed, Qt.Key.Key_Tab)
    QTest.keyClicks(ed, "einstein")
    assert "\\label{eq:einstein}" in ed.toPlainText() and "E=mc^2" in ed.toPlainText()
    # Esc closes the popup; Enter without arrow navigation is a plain newline
    ed.setPlainText("")
    _type(ed, r"\fra")
    assert ed.popup_visible()
    QTest.keyClick(ed, Qt.Key.Key_Escape)
    assert not ed.popup_visible()
    _type(ed, "c")
    QTest.keyClick(ed, Qt.Key.Key_Return)
    assert ed.toPlainText() == "\\frac\n"


def test_enter_after_begin_adds_the_end(win):
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    ids, dm, tab = _doc_win(win, 1)
    ed = tab.editor
    ed.autocomplete = True
    ed.setPlainText("")
    _type(ed, r"\begin{center}")
    ed.hide_popup()
    QTest.keyClick(ed, Qt.Key.Key_Return)
    assert ed.toPlainText() == "\\begin{center}\n  \n\\end{center}"
    assert ed.textCursor().blockNumber() == 1
    ed.setPlainText("\\begin{center}\nx\n\\end{center}\n\\begin{center}")
    c = ed.textCursor()
    c.movePosition(c.MoveOperation.End)
    ed.setTextCursor(c)
    QTest.keyClick(ed, Qt.Key.Key_Return)
    assert ed.toPlainText().count("\\end{center}") == 2
    ed.setPlainText("\\begin{center}\nx\n\\end{center}")      # already closed: no duplicate end
    c = ed.textCursor()
    c.setPosition(len("\\begin{center}"))
    ed.setTextCursor(c)
    QTest.keyClick(ed, Qt.Key.Key_Return)
    assert ed.toPlainText().count("\\end{center}") == 1


def test_pasting_a_picture_into_text_and_presentation(win):
    from PyQt6.QtGui import QImage
    ids, dm, tab = _doc_win(win, 1)
    img = QImage.fromData(_png_bytes(80, 40), "PNG")
    assert not img.isNull()
    tab.paste_picture(img)                              # an article: a figure environment
    assert "\\noorfile" in tab.source() and "\\begin{figure}" in tab.source()
    assert len(win.assets) == 1
    # a presentation: a free object in the frame that holds the cursor
    from NoorSuite import slideobjects as so
    tab.editor.setPlainText("\n".join([
        r"\documentclass[aspectratio=1610]{beamer}", r"\usepackage{noorsuite}", r"\begin{document}",
        r"\begin{frame}{A}", "text", r"\end{frame}", r"\end{document}", ""]))
    c = tab.editor.textCursor()
    c.setPosition(tab.source().index("text"))
    tab.editor.setTextCursor(c)
    tab.paste_picture(img)
    objs = so.objects_in(tab.source(), 0)
    assert len(objs) == 1 and objs[0]["kind"] == "file"
    assert abs(objs[0]["w"] / objs[0]["h"] - 2.0) < 0.05          # aspect ratio kept


def test_slide_editor_pastes_the_clipboard_picture(win):
    from PyQt6.QtGui import QGuiApplication, QImage
    from NoorSuite import slideobjects as so
    ids, dm, tab = _doc_win(win, 1)
    tab.editor.setPlainText("\n".join([
        r"\documentclass[aspectratio=1610]{beamer}", r"\usepackage{noorsuite}", r"\begin{document}",
        r"\begin{frame}{A}", "text", r"\end{frame}", r"\end{document}", ""]))
    ed = tab.slide_editor
    ed.set_frame(0)
    QGuiApplication.clipboard().setImage(QImage.fromData(_png_bytes(60, 60), "PNG"))
    ed.canvas.pasteRequested.emit()
    objs = so.objects_in(tab.source(), 0)
    assert [o["kind"] for o in objs] == ["file"] and abs(objs[0]["w"] - objs[0]["h"]) < 0.05
