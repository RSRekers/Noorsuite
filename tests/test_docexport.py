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


# ------------------------------------------------------------------ journal templates
def test_templates_switch_keeps_text_and_front_matter():
    from NoorSuite import templates as T
    body = (r"\section{Intro}" "\n" r"Text $E=mc^2$." "\n" r"\noorsheet{fig-a}" "\n"
            r"\bibliography{references}" "\n")
    src = T.build_document("article", "My title", ["A. One", "B. Two"], "Abstract here.", body)
    src = src.replace(r"\usepackage{noorsuite}",
                      r"\usepackage{siunitx}" "\n" r"\usepackage{noorsuite}")
    cur = src
    for tid, marker in (("acs", "{achemso}"), ("rsc", "{rsc}"), ("elsevier", "{elsarticle}"),
                        ("article", "{article}"), ("acs", "{achemso}")):
        cur = T.apply_template(cur, tid)
        p = T.extract_parts(cur)
        assert marker in cur and T.detect(cur) == tid
        assert p["title"] == "My title" and p["abstract"] == "Abstract here."
        assert r"\noorsheet{fig-a}" in p["body"] and r"\bibliography{references}" in p["body"]
        assert any("siunitx" in e for e in p["extra"]), tid
    # two authors become two \author entries in ACS
    assert cur.count(r"\author{") == 2 and "A. One" in cur and "B. Two" in cur


def test_acs_environments_round_trip_to_sections():
    from NoorSuite import templates as T
    acs = T.build_document("acs", "T", ["X"], "A.",
                           "Text.\n" r"\begin{acknowledgement}" "\nThanks.\n"
                           r"\end{acknowledgement}" "\n")
    art = T.apply_template(acs, "article")
    assert r"\section*{Acknowledgements}" in art and "acknowledgement}" not in art
    back = T.apply_template(art, "acs")
    assert r"\begin{acknowledgement}" in back


def test_compose_wraps_body_text_and_switches_full_documents():
    from NoorSuite import templates as T
    wrapped = T.compose(r"\section{A}" "\nBody.", "elsevier", "Title X", ["Au Thor"], "Abs.")
    assert r"\documentclass[preprint,12pt]{elsarticle}" in wrapped and "Title X" in wrapped
    assert r"\section{A}" "\nBody." in wrapped
    assert wrapped.rstrip().endswith(r"\end{document}")


def test_figure_widths_and_two_column_snippet():
    from NoorSuite import templates as T
    w = dict((lab, cm) for lab, cm, _ in T.figure_widths("acs"))
    assert w["ACS single column (8.25 cm)"] == 8.25
    assert T.get("rsc")["two_column"] and not T.get("acs")["two_column"]
    snip = latex.figure_snippet("abc", "Fig", width="width=17.1cm", star=True)
    assert snip.startswith(r"\begin{figure*}") and snip.rstrip().endswith(r"\end{figure*}")


def test_class_with_own_citations_is_not_given_natbib():
    from NoorSuite import templates as T
    for tid in ("acs", "rsc", "elsevier"):
        doc = T.build_document(tid, "T", ["A"], "Abs.",
                               r"Cite \citep{x}." "\n" r"\bibliography{references}" "\n")
        out, _ = latex.normalize_for_style(doc, "acs")
        assert out == doc, tid                       # nothing injected
        assert latex.own_citations(doc)
    assert not latex.own_citations(T.build_document("article"))


@pytest.mark.skipif(docexport.find_pandoc() is None, reason="pandoc not installed")
def test_word_reference_doc_takes_journal_font_and_margins(tmp_path):
    from NoorSuite import templates as T
    ref = docexport.make_reference_docx("acs", tmp_path, docexport.find_pandoc())
    assert ref and zipfile.is_zipfile(ref)
    z = zipfile.ZipFile(ref)
    assert "Times New Roman" in z.read("word/styles.xml").decode("utf8")
    assert 'w:left="1440"' in z.read("word/document.xml").decode("utf8")      # 2.54 cm
    ok, log = docexport.export_office(
        T.build_document("acs", "T", ["A"], "Abs.", "Body text."), [], "superscript", [], [],
        tmp_path / "o.docx", "docx", tmp_path / "w", lambda *a: None, lambda *a: None,
        docexport.find_pandoc())
    assert ok, log


# ------------------------------------------------------------------ GUI integration
from PyQt6.QtCore import Qt  # noqa: E402
from tests.test_gui_features import app, win, _doc_win, _wait  # noqa: E402,F401


def test_document_tab_layout_switch_widths_and_sheet_resize(win):
    ids, dm, tab = _doc_win(win, 1)
    assert tab.layout_combo.currentData() == "article"
    tab.set_layout("acs")
    assert r"\documentclass[journal=jacsat,manuscript=article]{achemso}" in tab.source()
    assert tab.layout_combo.currentData() == "acs"
    labels = [tab.figures.width_combo.itemText(i) for i in range(tab.figures.width_combo.count())]
    assert any("single column (8.25 cm)" in t for t in labels)
    # one undoable edit
    tab.editor.undo()
    assert "{achemso}" not in tab.source()
    tab.set_layout("rsc")
    # a figure at the double-column width: figure*, and the sheet takes that width
    fp = tab.figures
    i = next(i for i in range(fp.width_combo.count()) if fp.width_combo.itemText(i).startswith("RSC full"))
    fp.width_combo.setCurrentIndex(i)
    for k in range(fp.list.count()):
        if fp.list.item(k).data(Qt.ItemDataRole.UserRole) == ids[0]:
            fp.list.item(k).setSelected(True)
    snip = fp.snippets_for(fp.list.selectedItems())
    assert r"\begin{figure*}" in snip and "width=17.1cm" in snip
    sm = win.sheets[ids[0]]
    assert abs(sm.fig_width_cm - 17.1) < 1e-6
    assert abs(sm.fig_height_cm - 17.1 * 2 / 3) < 0.01


def test_new_journal_manuscript_and_push_document_template(win):
    dm = win.create_new_document("Paper ACS", template="acs")
    assert r"{achemso}" in dm.source and "Paper ACS" in dm.source
    win.handle_incoming_ipc({"action": "document", "name": "Via skill", "mode": "replace",
                             "source": r"\section{A}" "\nText.", "template": "elsevier",
                             "title": "Skill title", "authors": ["A One"], "abstract": "Abs."})
    d2 = next(d for d in win.documents.values() if d.name == "Via skill")
    assert r"{elsarticle}" in d2.source and "Skill title" in d2.source and "A One" in d2.source


def test_offline_push_document_with_template(tmp_path):
    from NoorSuite.client import OfflineClient
    from NoorSuite.model import ProjectModel
    s = OfflineClient(tmp_path / "p.sciproj")
    s.push_document("Paper", r"\section{Intro}" "\nBody.", template="acs", title="T",
                    authors=["X Y", "Z W"], abstract="Abs.")
    src = ProjectModel.load(tmp_path / "p.sciproj").documents[0].source
    assert "{achemso}" in src and src.count(r"\author{") == 2 and "Body." in src


# ------------------------------------------------------------------ equation numbering
def test_equations_become_three_column_tables_with_numbers():
    src = (r"A \begin{equation}\label{e1} x = 1 % note" "\n" r"\nonumber\end{equation} B" "\n"
           "% \\begin{equation} hidden \\end{equation}\n"
           r"\begin{equation*} u=v \end{equation*}" "\n"
           r"\begin{equation} y = \frac{1}{2} \end{equation}")
    out, n = docexport.equations_to_tables(src)
    assert n == 2
    assert out.count(r"\begin{longtable}{p{0.08\linewidth}p{0.80\linewidth}p{0.08\linewidth}}") == 2
    assert r" & $\displaystyle x = 1$ & (1) \\" in out
    assert r" & $\displaystyle y = \frac{1}{2}$ & (2) \\" in out
    assert r"\label" not in out and "hidden" in out and r"\begin{equation*}" in out


def test_equation_labels_resolve_in_references():
    src = (r"\section{S}\label{s} See \eqref{e2}, \ref{e1}, \autoref{e2}." "\n"
           r"\begin{equation}\label{e1} a \end{equation}" "\n"
           r"\begin{equation}\label{e2} b \end{equation}")
    out, labels = docexport.number_references(src)
    assert "See (2), 1, Equation 2." in out
    assert labels["e1"] == "Equation 1" and labels["e2"] == "Equation 2"


@pytest.mark.skipif(docexport.find_pandoc() is None, reason="pandoc not installed")
def test_docx_equation_tables_are_aligned_and_borderless(tmp_path):
    src = (r"\documentclass{article}\begin{document}" "\n"
           r"Energy~\eqref{e}." "\n"
           r"\begin{equation}\label{e} E = mc^2 \end{equation}" "\n"
           r"\end{document}")
    out = tmp_path / "o.docx"
    ok, log = docexport.export_office(src, [], "superscript", [], [], out, "docx",
                                      tmp_path / "w", lambda *a: None, lambda *a: None,
                                      docexport.find_pandoc())
    assert ok, log
    xml = zipfile.ZipFile(out).read("word/document.xml").decode("utf8")
    assert xml.count("<w:tbl>") == 1 and "<m:oMath" in xml
    tbl = re.search(r"<w:tbl>.*</w:tbl>", xml, re.S).group(0)
    assert 'w:jc w:val="center"' in tbl and 'w:jc w:val="right"' in tbl
    assert 'w:val="nil"' in tbl
    text = re.sub(r"<[^>]+>", " ", xml)
    assert "(1)" in text and "Energy (1)" in re.sub(r"\s+", " ", text)
