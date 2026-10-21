r"""Pictures / graphs placed on slides as \noorobj{file}{id} / \noorobj{sheet}{id} must be exported and shown."""
import pytest

from NoorSuite import latex
from tests.test_gui_features import _doc_win, _wait, app, win  # noqa: F401  (window fixtures)


def test_object_references_count_as_figures():
    s = "\n".join([r"\noorsheet{a}", r"\noorobj[x=1,y=2]{file}{abc123}", r"\noorobj{sheet}{s9} \noorfile[width=3cm]{img}",
                 r"% \noorobj{file}{commented}", r"\noormasterobj[x=1]{file}{logo}", r"\noorobj{rect}{notme}"])
    assert latex.figure_refs(s) == ["a", "s9"]
    assert latex.asset_refs(s) == ["abc123", "img", "logo"]


@pytest.mark.skipif(latex.find_engine() is None, reason="no LaTeX engine installed")
def test_pasted_picture_and_graph_on_a_slide_are_drawn_not_printed_as_ids(win):
    from PyQt6.QtCore import QBuffer, QByteArray, QIODevice
    from PyQt6.QtGui import QColor, QImage
    from PyQt6.QtPdf import QPdfDocument
    ids, dm, tab = _doc_win(win, 1)
    img = QImage(80, 50, QImage.Format.Format_RGB32)
    img.fill(QColor("#cc2222"))
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    asset = win.import_asset(data=bytes(ba), ext="png", name="Pasted picture test")
    src = "\n".join([
        r"\documentclass[aspectratio=1610,11pt,t]{beamer}", r"\usepackage{noorsuite}", r"\begin{document}",
        r"\begin{frame}{Pic}",
        r"\noorobj[x=2,y=3,w=4,h=2.5]{file}{%s}" % asset.asset_id,
        r"\noorobj[x=8,y=3,w=5,h=3.5]{sheet}{%s}" % ids[0],
        r"\end{frame}", r"\end{document}", ""])
    tab.editor.setPlainText(src)
    tab.compile(force=True)
    assert _wait(lambda: not tab._building and tab._pdf, 300), tab.log.toPlainText()[-800:]
    d = QPdfDocument(win)
    d.load(tab._pdf)
    text = d.getAllText(0).text()
    assert asset.asset_id not in text and ids[0] not in text, text          # no id printed as text
    px = d.render(0, d.pagePointSize(0).toSize() * 2)
    # the red picture is on the page (left object), at roughly x 2..6 cm / y 3..5.5 cm of 16 x 10 cm
    red = px.pixelColor(int(4.0 / 16 * px.width()), int(4.2 / 10 * px.height()))
    assert red.red() > 150 and red.green() < 80, red.name()
    d.close()
