"""Word / LibreOffice export: source preparation (pure) and a pandoc round trip."""
import re
import zipfile

import pytest

from NoorSuite import docexport, latex


def test_figures_become_includegraphics_and_missing_ones_are_flagged():
    src = r"A \noorsheet[width=5cm]{iv} B \noorfile{logo} C \noorsheet{gone}"
    out, missing = docexport.swap_figures(src, {("noorsheet", "iv"): "figs/iv.png",
                                                ("noorfile", "logo"): "figs/logo.png"})
    assert r"\includegraphics[width=5cm]{figs/iv.png}" in out
    assert r"\includegraphics{figs/logo.png}" in out
    assert missing == ["gone"] and "missing figure gone" in out


def test_reference_list_becomes_numbered_paragraphs():
    src = (r"text \begin{thebibliography}{9}" "\n" r"\bibitem{a} First entry." "\n"
           r"\bibitem{b} Second" "\n entry.\n" r"\end{thebibliography}" "\nend")
    out = docexport.bibliography_to_paragraphs(src)
    assert r"\section*{References}" in out
    assert r"\noindent (1)\ First entry." in out and r"\noindent (2)\ Second entry." in out
    assert "thebibliography" not in out


def test_captions_are_numbered_and_refs_resolved():
    src = (r"\section{Intro}\label{s:i} See \ref{f:a}, \autoref{t:b}, \ref{s:i}, \ref{nope}." "\n"
           r"\begin{figure}\includegraphics{a.png}\caption{First}\label{f:a}\end{figure}" "\n"
           r"\subsection{Deep}\label{s:d}" "\n"
           r"\begin{table}\caption[short]{Second}\label{t:b}\end{table}" "\n"
           r"\begin{figure}\caption{Third}\label{f:c}\end{figure} \ref{s:d} \eqref{f:c}")
    out, labels = docexport.number_references(src)
    assert r"\caption{Figure 1. First}" in out
    assert r"\caption[short]{Table 1. Second}" in out
    assert r"\caption{Figure 2. Third}" in out
    assert "See 1, Table 1, 1, [nope]." in out
    assert "1.1 (2)" in out
    assert labels["s:d"] == "Section 1.1" and labels["f:c"] == "Figure 2"


def test_comments_do_not_confuse_numbering():
    src = ("% \\begin{figure}\n\\begin{figure}\\caption{Only}\\label{f}\\end{figure} \\ref{f}\n")
    out, _ = docexport.number_references(src)
    assert r"\caption{Figure 1. Only}" in out and out.rstrip().endswith("1")


@pytest.mark.skipif(docexport.find_pandoc() is None, reason="pandoc not installed")
def test_pandoc_roundtrip_docx_and_odt(tmp_path):
    pandoc = docexport.find_pandoc()
    rec = {"key": "k1", "type": "article-journal", "title": "A study", "authors": [["Doe", "J."]],
           "editors": [], "year": "2024", "container": "J. Test", "container_short": "J. Test",
           "volume": "3", "issue": "", "pages": "1-2", "doi": "10.1/x", "url": "",
           "publisher": "", "place": "", "isbn": "", "month": "", "zotero_key": ""}
    src = (r"\documentclass{article}" "\n" r"\begin{document}" "\n"
           r"\section{Intro}\label{s} As shown~\cite{k1}, see \ref{s}." "\n"
           r"\begin{equation} a^2+b^2=c^2 \end{equation}" "\n"
           r"\bibliography{references}" "\n" r"\end{document}" "\n")
    for fmt in ("docx", "odt"):
        out = tmp_path / f"o.{fmt}"
        ok, log = docexport.export_office(src, [rec], "superscript", [], [], out, fmt,
                                          tmp_path / f"w{fmt}", lambda *a: None,
                                          lambda *a: None, pandoc)
        assert ok, log
        assert zipfile.is_zipfile(out)
    xml = zipfile.ZipFile(tmp_path / "o.docx").read("word/document.xml").decode("utf8")
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", xml))
    assert "Doe, J." in text and "References" in text and "1 Intro" in text
    assert "<m:oMath" in xml                               # a native Word equation


def test_missing_pandoc_gives_the_install_hint(tmp_path, monkeypatch):
    monkeypatch.setattr(docexport, "find_pandoc", lambda: None)
    ok, log = docexport.run_pandoc(tmp_path, "main.tex", tmp_path / "o.docx", "docx")
    assert not ok and "conda install" in log
