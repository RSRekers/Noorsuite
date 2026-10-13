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
    src = (r"A \begin{equation}\label{e1} x = 1 % note" "\n" r"\end{equation} B" "\n"
           "% \\begin{equation} hidden \\end{equation}\n"
           r"\begin{equation*} u=v \end{equation*}" "\n"
           r"\begin{equation} y = \frac{1}{2} \nonumber \end{equation}" "\n"
           r"\begin{equation} z \end{equation}")
    out, n, labels = docexport.equations_to_tables(src)
    assert n == 2 and labels == {"e1": 1}
    assert out.count(r"\begin{longtable}{p{0.08\linewidth}p{0.80\linewidth}p{0.08\linewidth}}") == 3
    m = docexport._MARK
    assert m + r" & $\displaystyle x = 1$ & (1) \\" in out
    assert m + r" & $\displaystyle y = \frac{1}{2}$ &  \\" in out       # \nonumber
    assert m + r" & $\displaystyle z$ & (2) \\" in out
    assert r"\label" not in out and "hidden" in out and r"\begin{equation*}" in out


def test_align_rows_are_numbered_and_split_at_the_ampersand():
    src = (r"\begin{align}" "\n" r"a &= b + c \label{r1} \\" "\n"
           r"  &= d \nonumber \\" "\n"
           r"e &= \begin{aligned} f \\ g \end{aligned} \label{r3}" "\n" r"\end{align}")
    out, n, labels = docexport.equations_to_tables(src)
    m = docexport._MARK
    assert n == 2 and labels == {"r1": 1, "r3": 2}
    assert r"p{0.08\linewidth}p{0.38\linewidth}p{0.42\linewidth}p{0.08\linewidth}" in out
    assert m + r" & $\displaystyle a$ & $\displaystyle = b + c$ & (1) \\" in out
    assert m + r" &  & $\displaystyle = d$ &  \\" in out
    assert (r"$\displaystyle e$ & $\displaystyle = \begin{aligned} f \\ g \end{aligned}$ & (2)"
            in out)
    same, k, _ = docexport.equations_to_tables(r"\begin{align*} a &= b \end{align*}")
    assert k == 0 and "longtable" not in same


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


@pytest.mark.skipif(docexport.find_pandoc() is None, reason="pandoc not installed")
def test_docx_align_block_is_a_four_column_table_with_per_row_numbers(tmp_path):
    src = (r"\documentclass{article}\begin{document}" "\n"
           r"Rows~\eqref{b} and~\eqref{c}." "\n"
           r"\begin{align}" "\n" r"a &= b + c \label{b} \\" "\n"
           r"  &= d \nonumber \\" "\n" r"e &= f \label{c}" "\n" r"\end{align}" "\n"
           r"\end{document}")
    out = tmp_path / "o.docx"
    ok, log = docexport.export_office(src, [], "superscript", [], [], out, "docx",
                                      tmp_path / "w", lambda *a: None, lambda *a: None,
                                      docexport.find_pandoc())
    assert ok, log
    xml = zipfile.ZipFile(out).read("word/document.xml").decode("utf8")
    assert xml.count("<w:tbl>") == 1 and xml.count("<w:gridCol") == 4
    assert xml.count("<w:tr>") == 3
    tbl = re.search(r"<w:tbl>.*</w:tbl>", xml, re.S).group(0)
    assert 'w:jc w:val="right"' in tbl and 'w:jc w:val="left"' in tbl and 'w:val="nil"' in tbl
    flat = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", xml))
    assert "(1)" in flat and "(2)" in flat and "(3)" not in flat
    assert "Rows (1) and (2)" in flat


# ------------------------------------------------------------------ user layouts
@pytest.fixture
def tpl_dir(tmp_path, monkeypatch):
    d = tmp_path / "templates"
    monkeypatch.setenv("NOORSUITE_TEMPLATES", str(d))
    return d


def _custom_source():
    return (r"\documentclass[10pt,a4paper,twocolumn]{article}" "\n"
            r"\usepackage{siunitx}" "\n" r"\usepackage{booktabs}" "\n"
            r"\usepackage{noorsuite}" "\n"
            r"\newcommand{\Li}{\mathrm{Li}}" "\n"
            r"\title{Old title}\author{Old Author}" "\n"
            r"\begin{document}" "\n" r"\maketitle" "\n"
            r"\begin{abstract}Old abstract.\end{abstract}" "\n"
            r"\section{Old body}Text." "\n" r"\end{document}" "\n")


def test_user_layout_is_saved_listed_applied_and_deleted(tpl_dir):
    from NoorSuite import templates as T
    tid = T.make_user_template(_custom_source(), "Group thesis", "8.5, 17.5")
    assert tid == "user:group-thesis" and (tpl_dir / "group-thesis.json").is_file()
    assert (tid, "Group thesis  (my layout)") in T.list_templates()
    t = T.get(tid)
    assert t["two_column"] and T.base_of(tid) == "article"
    assert [w[1] for w in T.figure_widths(tid)] == [8.5, 17.5]
    assert T.figure_widths(tid)[1][2] is True                   # last = double column
    # a new document in this layout keeps the class, packages and macros, takes new front matter
    doc = T.build_document(tid, "New title", ["A One", "B Two"], "New abstract.", "Body here.")
    assert r"\documentclass[10pt,a4paper,twocolumn]{article}" in doc
    assert r"\usepackage{siunitx}" in doc and r"\newcommand{\Li}" in doc
    assert "New title" in doc and "New abstract." in doc and "Old title" not in doc
    assert "Body here." in doc and "Old body" not in doc
    # switching an ACS document to it, and back to a built-in, keeps text
    acs = T.build_document("acs", "T", ["X"], "A.", "Keep this text.")
    mine = T.apply_template(acs, tid)
    assert "{article}" in mine and "Keep this text." in mine and r"\usepackage{siunitx}" in mine
    assert "Keep this text." in T.apply_template(mine, "elsevier")
    # push_document(template="user:...")
    assert "twocolumn" in T.compose(r"\section{A}" "\nBody.", tid, "Title", ["Au"], "Abs.")
    assert T.delete_user_template(tid) and not (tpl_dir / "group-thesis.json").exists()
    assert tid not in dict(T.list_templates())


def test_gui_save_apply_and_forget_layout(win, tpl_dir, monkeypatch):
    from PyQt6.QtWidgets import QInputDialog
    ids, dm, tab = _doc_win(win, 1)
    tab.set_source(_custom_source())
    answers = iter([("My two-col", True), ("8.4, 17.2", True)])
    monkeypatch.setattr(QInputDialog, "getText",
                        staticmethod(lambda *a, **k: next(answers)))
    tab.save_layout()
    tid = "user:my-two-col"
    assert tab.model.layout == tid and tab.layout_combo.findData(tid) >= 0
    assert tab.current_layout() == tid
    labels = [tab.figures.width_combo.itemText(i) for i in range(tab.figures.width_combo.count())]
    assert any("17.2 cm" in t for t in labels)
    # choose a built-in, then the user layout again
    tab.set_layout("elsevier")
    assert tab.current_layout() == "elsevier" and tab.model.layout == ""
    tab.set_layout(tid)
    assert tab.model.layout == tid and "twocolumn" in tab.source()
    # persisted with the document
    from NoorSuite.model import DocumentModel
    assert DocumentModel.from_dict(tab.model.to_dict()).layout == tid


def test_user_layout_shows_in_new_manuscript_and_skill_push(win, tpl_dir):
    from NoorSuite import templates as T
    tid = T.make_user_template(_custom_source(), "Lab style")
    dm = win.create_new_document("Draft", template=tid)
    assert "twocolumn" in dm.source and "siunitx" in dm.source
    win.handle_incoming_ipc({"action": "document", "name": "Skill doc", "mode": "replace",
                             "source": r"\section{A}" "\nText.", "template": tid,
                             "title": "Skill", "authors": ["A"], "abstract": "Abs."})
    d2 = next(d for d in win.documents.values() if d.name == "Skill doc")
    assert "twocolumn" in d2.source and "Skill" in d2.source


# ------------------------------------------------------------------ author lists
_AUTHORS = [{"name": "Anna Author", "affils": ["x"], "email": "a@b.org", "corresponding": True},
            {"name": "Ben Writer", "affils": ["x", "y"], "email": "", "corresponding": False}]
_AFFS = {"x": "University A, City", "y": "Institute B, Town"}


def test_author_lists_roundtrip_through_every_layout():
    from NoorSuite import templates as T
    src = T.build_document("article", "T", _AUTHORS, "Abs.", "Body.", None, _AFFS)
    assert r"\usepackage{authblk}" in src and r"\author[1,2]{Ben Writer}" in src
    assert r"\affil[2]{Institute B, Town}" in src
    for tid, marker in (("acs", r"\alsoaffiliation{Institute B, Town}"),
                        ("elsevier", r"\address[2]{Institute B, Town}"),
                        ("rsc", r"$^{2}$ Institute B, Town"), ("article", r"\affil[1]")):
        out = T.apply_template(src, tid)
        assert marker in out, (tid, out)
        assert r"\usepackage{authblk}" in out if tid == "article" else \
            r"\usepackage{authblk}" not in out
    # structured information survives ACS -> Elsevier -> article (keys renumbered)
    back = T.parse_authors(T.apply_template(T.apply_template(src, "acs"), "elsevier"))
    assert [a["name"] for a in back["authors"]] == ["Anna Author", "Ben Writer"]
    assert back["authors"][0]["email"] == "a@b.org" and back["authors"][0]["corresponding"]
    assert set(back["affiliations"].values()) == set(_AFFS.values())
    assert len(back["authors"][1]["affils"]) == 2


def test_parse_authors_reads_handwritten_conventions():
    from NoorSuite import templates as T
    authblk = (r"\documentclass{article}\usepackage{authblk}" "\n"
               r"\author[1,2]{Anna A.}\author[2*]{Ben B.}" "\n"
               r"\affil[1]{Uni A}\affil[2]{Uni B}\begin{document}\end{document}")
    p = T.parse_authors(authblk)
    assert [a["affils"] for a in p["authors"]] == [["1", "2"], ["2"]]
    assert p["authors"][1]["corresponding"] and p["affiliations"] == {"1": "Uni A", "2": "Uni B"}
    acs = (r"\documentclass{achemso}" "\n" r"\author{Anna A.}\affiliation{Uni A}\email{a@b.c}" "\n"
           r"\author{Ben B.}\affiliation[]{Uni B}\alsoaffiliation{Uni C}" "\n"
           r"\begin{document}\end{document}")
    p = T.parse_authors(acs)
    assert p["authors"][0]["email"] == "a@b.c" and len(p["authors"][1]["affils"]) == 2
    plain = r"\author{A One \and B Two}\begin{document}"
    assert [a["name"] for a in T.parse_authors(plain)["authors"]] == ["A One", "B Two"]


def test_build_copy_repairs_authblk_in_classes_with_own_author_commands():
    from NoorSuite import latex
    hand = (r"\usepackage{authblk}" "\n" r"\author[1]{Anna A.}" "\n" r"\affil[1]{Uni A}" "\n")
    for cls, own in (("achemso", r"\affiliation{Uni A}"), ("elsarticle", r"\address[1]{Uni A}")):
        src = (r"\documentclass{" + cls + "}\n" + hand + r"\title{T}" "\n"
               r"\begin{document}" "\n" r"\begin{abstract}x\end{abstract}" "\nBody.\n"
               r"\end{document}")
        out, notes = latex.prepare_build_source(src, [], "acs-guide")[:2]
        assert r"\usepackage{authblk}" not in out and own in out
        assert any("authblk" in n for n in notes)
    art = (r"\documentclass{article}" "\n" r"\usepackage{authblk}" "\n"
           r"\author[1]{Anna}\affiliation[1]{Uni A}" "\n" r"\begin{document}\end{document}")
    out, notes = latex.prepare_build_source(art, [], "acs-guide")[:2]
    assert r"\affil[1]{Uni A}" in out and r"\affiliation" not in out
    noblk = r"\documentclass{article}\author[1]{A}\affil[1]{B}" "\n" r"\begin{document}\end{document}"
    out, _ = latex.prepare_build_source(noblk, [], "acs-guide")[:2]
    assert r"\usepackage{authblk}" in out


def test_authors_dialog_edits_the_front_matter(win, monkeypatch):
    from NoorSuite import templates as T
    from NoorSuite.authorsdialog import AuthorsDialog
    ids, dm, tab = _doc_win(win, 1)
    tab.set_layout("elsevier")
    dlg_holder = {}

    def fake_exec(self):
        dlg_holder["pre"] = ([a["name"] for a in self.result_authors], self.a_table.rowCount())
        self.a_table.setRowCount(0)
        self._add_author({"name": "Zoe Zed", "affils": ["k"], "email": "z@z.z",
                          "corresponding": True})
        self._add_author({"name": "Yan Yu", "affils": ["k", "m"]})
        self.f_table.setRowCount(0)
        self._add_affil("k", "Lab K, Town")
        self._add_affil("m", "Lab M, City")
        self._accept()
        return self.DialogCode.Accepted

    monkeypatch.setattr(AuthorsDialog, "exec", fake_exec)
    tab.edit_authors()
    src = tab.source()
    assert r"\author[1]{Zoe Zed\corref{cor1}}" in src and r"\ead{z@z.z}" in src
    assert r"\author[1,2]{Yan Yu}" in src and r"\address[2]{Lab M, City}" in src
    assert r"\cortext[cor1]{Corresponding author}" in src
    # switching layout keeps the list
    tab.set_layout("acs")
    p = T.parse_authors(tab.source())
    assert [a["name"] for a in p["authors"]] == ["Zoe Zed", "Yan Yu"]
    assert len(p["authors"][1]["affils"]) == 2


# ------------------------------------------------------------------ SI documents
def _main_paper():
    from NoorSuite import templates as T
    body = (r"\section{Intro}" "\nSee Fig.~\\ref{f}.\n"
            r"\begin{figure}\noorsheet{a}\caption{Main}\label{f}\end{figure}" "\n"
            r"\bibliography{references}" "\n")
    src = T.build_document("acs", "Charge transport in LLZO", _AUTHORS, "Main abstract.", body,
                           [r"\newcommand{\Li}{\mathrm{Li}}"], _AFFS)
    return src


def test_si_document_keeps_layout_authors_and_macros_with_s_counters():
    from NoorSuite import templates as T
    si = T.si_document(_main_paper())
    assert r"\documentclass[journal=jacsat,manuscript=article]{achemso}" in si
    assert r"\newcommand{\Li}" in si and r"\author{Anna Author}" in si
    assert r"\title{Supporting Information for: Charge transport in LLZO}" in si
    assert "Main abstract." not in si and "Supporting information for the main text." in si
    assert r"\section{Intro}" not in si and r"\section*{Supporting Information}" in si
    for c in ("figure", "table", "equation", "page"):
        assert r"\renewcommand{\the" + c + r"}{S\arabic{" + c + "}}" in si
    assert si.rstrip().endswith(r"\end{document}") and si.count(r"\begin{document}") == 1
    assert T.si_document(si).count(r"\renewcommand{\thefigure}") == 1      # idempotent counters
    assert r"\title{SI}" in T.si_document(_main_paper(), "SI")


def test_si_numbering_prefix_reaches_the_word_export():
    from NoorSuite import docexport
    src = (r"\renewcommand{\thefigure}{S\arabic{figure}}" "\n"
           r"\renewcommand{\theequation}{S\arabic{equation}}" "\n"
           r"See Fig.~\ref{f1}, \autoref{f1} and Eq.~\eqref{e1}." "\n"
           r"\begin{figure}\caption{One}\label{f1}\end{figure}" "\n"
           r"\begin{equation}\label{e1} a=b \end{equation}")
    assert docexport.counter_prefixes(src) == {"figure": "S", "equation": "S"}
    t, n, labels = docexport.equations_to_tables(src, "S")
    assert "(S1)" in t and n == 1
    out, _ = docexport.number_references(t, labels)
    assert r"\caption{Figure S1. One}" in out
    assert "See Fig.~S1, Figure S1 and Eq.~(S1)." in out


def test_si_via_window_button_and_offline_api(win, tmp_path):
    from NoorSuite.client import OfflineClient
    from NoorSuite.model import ProjectModel
    ids, dm, tab = _doc_win(win, 1)
    tab.set_layout("acs")
    n0 = len(win.documents)
    tab.new_si_document()
    assert len(win.documents) == n0 + 1
    si = next(d for d in win.documents.values() if d.name.endswith(" - SI"))
    assert "{achemso}" in si.source and "thefigure" in si.source
    s = OfflineClient(tmp_path / "p.sciproj")
    s.push_document("Paper", _main_paper())
    s.push_si_document("Paper")
    docs = {d.name: d for d in ProjectModel.load(tmp_path / "p.sciproj").documents}
    assert "Paper - SI" in docs and "Supporting Information for" in docs["Paper - SI"].source


def test_push_document_with_structured_authors_offline(tmp_path):
    from NoorSuite.client import OfflineClient
    from NoorSuite.model import ProjectModel
    s = OfflineClient(tmp_path / "p.sciproj")
    s.push_document("Paper", "Body text.", template="elsevier", title="T",
                    authors=[{"name": "A One", "affils": ["1"], "email": "a@b.org",
                              "corresponding": True}, {"name": "B Two", "affils": ["1", "2"]}],
                    affiliations={"1": "Univ A", "2": "Inst B"})
    src = ProjectModel.load(tmp_path / "p.sciproj").documents[0].source
    assert r"\author[1]{A One\corref{cor1}}" in src and r"\ead{a@b.org}" in src
    assert r"\author[1,2]{B Two}" in src and r"\address[2]{Inst B}" in src
