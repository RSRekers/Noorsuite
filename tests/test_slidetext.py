r"""Font size / style of ordinary slide text (\fontsize wrapper, \noorfont per frame)."""
from NoorSuite import slidetext as st

SRC = "\n".join([
    r"\documentclass{beamer}", r"\begin{document}",
    r"\begin{frame}{My title}", r"Some plain text", r"\begin{itemize}",
    r"\item<2-> First point", r"\item[--] Second", r"\end{itemize}", r"\end{frame}",
    r"\begin{frame}", r"\frametitle{Other}", r"\end{frame}", r"\end{document}"])


def _line(src, n):
    return src.split("\n")[n]


def test_item_text_title_and_idempotent_wrapping():
    s = st.restyle_line(SRC, 5, size=24)
    assert _line(s, 5) == r"\item<2-> {\fontsize{24}{28.8}\selectfont First point}"
    s = st.restyle_line(s, 5, size=30, bold=True)            # re-styling replaces, never nests
    assert _line(s, 5) == r"\item<2-> {\fontsize{30}{36}\selectfont\bfseries First point}"
    assert st.line_style(s, 5)["size"] == 30 and st.line_style(s, 5)["bold"]
    assert st.line_style(s, 5)["text"] == "First point"
    s = st.restyle_line(s, 5, size=None, bold=False)         # all defaults -> plain again
    assert _line(s, 5) == _line(SRC, 5)
    t = st.restyle_line(SRC, 2, size=32)
    assert _line(t, 2) == r"\begin{frame}{{\fontsize{32}{38.4}\selectfont My title}}"
    assert st.line_style(t, 2)["kind"] == "title" and st.line_style(t, 2)["text"] == "My title"
    u = st.restyle_line(SRC, 10, italic=True, color="#c8553d")
    assert _line(u, 10) == r"\frametitle{{\itshape\color[HTML]{C8553D} Other}}"
    p = st.restyle_line(SRC, 3, size=18)
    assert _line(p, 3) == r"{\fontsize{18}{21.6}\selectfont Some plain text}"
    assert st.restyle_line(SRC, 4, size=18) == SRC           # \begin{itemize} is not text
    assert st.restyle_line(SRC, 99, size=18) == SRC
    assert st.line_style(SRC, 6)["kind"] == "item" and st.line_style(SRC, 6)["text"] == "Second"
    assert st.frame_title_line(SRC, 0) == 2 and st.frame_title_line(SRC, 1) == 10


def test_frame_fonts_line():
    s = st.set_frame_fonts(SRC, 0, body=22)
    assert _line(s, 3) == r"\noorfont{body=22}"
    assert st.read_frame_fonts(s, 0) == {"body": 22.0}
    assert st.read_frame_fonts(s, 1) == {}
    s = st.set_frame_fonts(s, 0, body=0)
    assert s == SRC                                             # line removed again
    assert st.line_style(st.set_frame_fonts(SRC, 0, body=20), 3) is None   # \noorfont is not text


def _heights(pdf, words):
    from PyQt6.QtGui import QGuiApplication
    from PyQt6.QtPdf import QPdfDocument
    app = QGuiApplication.instance() or QGuiApplication([])
    d = QPdfDocument(app)
    d.load(str(pdf))
    out = []
    for page in range(d.pageCount()):
        text = d.getAllText(page).text()
        row = {}
        for w in words:
            i = text.find(w)
            row[w] = (d.getSelectionAtIndex(page, i, len(w)).boundingRectangle().height()
                      if i >= 0 else None)
        out.append(row)
    d.close()
    return out


def _build(tmp_path, body):
    from NoorSuite import latex
    head = "\n".join([r"\documentclass[aspectratio=1610,11pt]{beamer}", r"\usepackage{noorsuite}",
                      r"\begin{document}"])
    latex.prepare_workdir(tmp_path, head + "\n" + body + "\n" + r"\end{document}" + "\n")
    res = latex.build_pdf(tmp_path)
    assert res.ok, res.log[-800:]
    return res.pdf


def test_sizes_reach_the_compiled_slides(tmp_path):
    import pytest
    from NoorSuite import latex
    if latex.find_engine() is None:
        pytest.skip("no LaTeX engine installed")
    plain = [r"\begin{frame}{Heading}", r"\begin{itemize}", r"\item Alpha", r"\end{itemize}", r"\end{frame}"]
    src = "\n".join(plain)
    s = st.set_frame_fonts(src, 0, body=30)                                        # \noorfont{body=30}
    s2 = st.restyle_line(src, st.frame_title_line(src, 0), size=40)                # title text wrapper
    s3 = st.restyle_line(src, 2, size=36)                                          # one item
    pdf = _build(tmp_path, "\n".join([src, s, s2, s3]))
    base, fonts, titled, itemed = _heights(pdf, ["Heading", "Alpha"])
    assert fonts["Alpha"] > base["Alpha"] * 1.4, (base, fonts)
    assert titled["Heading"] > base["Heading"] * 1.4, (base, titled)
    assert itemed["Alpha"] > base["Alpha"] * 1.4, (base, itemed)
    # the global default \noorslide{body-size=..}
    glob = tmp_path / "g"
    glob.mkdir()
    pdf2 = _build(glob, "\n".join([r"\noorslide{body-size=34}", src]))
    (only,) = _heights(pdf2, ["Alpha"])
    assert only["Alpha"] > base["Alpha"] * 1.5, (base, only)


def test_pdf_text_is_matched_to_the_source_line_of_its_frame():
    src = "\n".join([r"\begin{frame}{These results}", r"\begin{itemize}",
                    r"\item<2-> The \textbf{first} very long point that wraps over two lines of the slide",
                    r"\item Second point $x^2$", r"\end{itemize}", r"\end{frame}"])
    # a wrapped fragment (with a bullet and a ligature as the PDF has them), a short item, the title
    assert st.match_pdf_text(src, 0, 5, "• The ﬁrst very long point that") == 2
    assert st.match_pdf_text(src, 0, 5, "very long point that wraps over") == 2
    assert st.match_pdf_text(src, 0, 5, "• Second point x2") == 3
    assert st.match_pdf_text(src, 0, 5, "These results") == 0
    assert st.match_pdf_text(src, 0, 5, "nothing like it") is None
    assert st.match_pdf_text(src, 3, 3, "very long point") is None          # outside the frame lines
