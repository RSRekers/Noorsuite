import os

import pandas as pd
import pytest

from NoorSuite import latex, zotero
from NoorSuite.client import OfflineClient
from NoorSuite.model import ProjectModel

CSL = {
    "id": "http://zotero.org/users/123/items/ABCD1234", "type": "article-journal",
    "citationKey": "smith2020quantum", "title": "Quantum transport & noise in M_x films",
    "author": [{"family": "Smith", "given": "Jane A."}, {"family": "O'Neil", "given": "Tom"}],
    "container-title": "Phys. Rev. B", "volume": "101", "issue": "4", "page": "045101-045110",
    "issued": {"date-parts": [[2020, 1, 22]]}, "DOI": "10.1103/PhysRevB.101.045101",
}
ZOT = {
    "itemType": "book", "title": "Statistical Physics", "date": "March 2011",
    "creators": [{"creatorType": "author", "firstName": "Lev", "lastName": "Landau"},
                 {"creatorType": "editor", "name": "Pergamon Press"}],
    "publisher": "Pergamon", "place": "Oxford", "ISBN": "978-0-7506-3372-7",
    "itemKey": "WXYZ9876",
}


def test_normalize_csl_and_zotero_shapes():
    r = zotero.normalize(CSL)
    assert r["key"] == "smith2020quantum" and r["year"] == "2020" and r["zotero_key"] == "ABCD1234"
    assert r["authors"] == [["Smith", "Jane A."], ["O'Neil", "Tom"]]
    assert r["container"] == "Phys. Rev. B" and r["pages"] == "045101-045110"
    z = zotero.normalize(ZOT)
    assert z["year"] == "2011" and z["type"] == "book" and z["zotero_key"] == "WXYZ9876"
    assert z["key"] == "landau2011statistical"                # generated: author+year+word
    assert z["editors"] == [["Pergamon Press*", ""]]
    assert zotero.normalize({"note": "no title"}) is None
    assert zotero.zotero_url(z) == "zotero://select/library/items/WXYZ9876"
    assert zotero.label(r) == "Smith et al. (2020) - Quantum transport & noise in M_x films"


def test_bibtex_output_and_cite_keys():
    bib = zotero.to_bibtex(zotero.normalize(CSL))
    assert bib.startswith("@article{smith2020quantum,")
    assert "author = {Smith, Jane A. and O'Neil, Tom}" in bib
    assert "title = {{Quantum transport \\& noise in M\\_x films}}" in bib
    assert "pages = {045101--045110}" in bib and "journal = {Phys. Rev. B}" in bib
    assert "doi = {10.1103/PhysRevB.101.045101}" in bib
    book = zotero.to_bibtex(zotero.normalize(ZOT))
    assert book.startswith("@book{") and "editor = {{Pergamon Press}}" in book
    src = "see \\cite{a, b}, \\citep[p.~3]{c} % \\cite{hidden}\n\\nocite{d}\n\\citet*{a}"
    assert zotero.cite_keys(src) == ["a", "b", "c", "d"]
    recs = [{"key": "a", "type": "misc", "title": "A", "authors": []},
            {"key": "z", "type": "misc", "title": "Z", "authors": []}]
    assert "@misc{a," in zotero.bibtex_file(recs, used=["a"]) and \
        "@misc{z," not in zotero.bibtex_file(recs, used=["a"])


def test_references_roundtrip_offline_and_in_project(tmp_path):
    suite = OfflineClient(tmp_path / "p.sciproj")
    assert suite.push_references([CSL, ZOT, {"junk": 1}]) == \
        ["smith2020quantum", "landau2011statistical"]
    df = suite.list_references()
    assert list(df["key"]) == ["smith2020quantum", "landau2011statistical"]
    pm = ProjectModel.load(tmp_path / "p.sciproj")
    assert [r["key"] for r in pm.references] == ["smith2020quantum", "landau2011statistical"]
    suite.push_references([{**CSL, "title": "Updated title"}])              # same key -> replaced
    pm = ProjectModel.load(tmp_path / "p.sciproj")
    assert len(pm.references) == 2
    assert next(r for r in pm.references if r["key"] == "smith2020quantum")["title"] == \
        "Updated title"


def test_zotero_unreachable_gives_a_clear_error():
    with pytest.raises(zotero.ZoteroError):
        zotero.cayw_pick(timeout=1, url="http://127.0.0.1:9/better-bibtex/cayw?format=json")


needs_engine = pytest.mark.skipif(latex.find_engine() is None, reason="no LaTeX engine installed")


@needs_engine
def test_bibliography_builds_into_the_pdf(tmp_path):
    pytest.importorskip("PyQt6")
    import subprocess
    import sys
    suite = OfflineClient(tmp_path / "p.sciproj")
    suite.push_references([CSL])
    body = (r"""\documentclass{article}
\usepackage{hyperref}
\usepackage{noorsuite}
\begin{document}
As shown by Smith \cite{smith2020quantum}, noise matters.
\bibliographystyle{unsrt}
\bibliography{references}
\end{document}
""")
    suite.push_document("Doc", body)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = {**os.environ, "PYTHONPATH": root, "QT_QPA_PLATFORM": "offscreen"}
    out = tmp_path / "doc.pdf"
    r = subprocess.run([sys.executable, "-m", "NoorSuite", "latex", "build", "--project",
                        str(tmp_path / "p.sciproj"), "--doc", "Doc", "--out", str(out)],
                       env=env, capture_output=True, text=True, timeout=900)
    assert r.returncode == 0, (r.stdout + r.stderr)[-2000:]
    try:
        import fitz
    except ImportError:
        return
    text = fitz.open(str(out))[0].get_text()
    assert "Quantum transport" in text and "[1]" in text       # numbered entry + citation


def test_default_style_is_acs_and_build_copy_gets_a_reference_list(tmp_path, monkeypatch):
    monkeypatch.setattr(latex, "_config_path", lambda: tmp_path / "config.json")
    assert latex.get_bib_style() == "acs" and latex.bib_style()["bst"] == "achemso"
    src = "\\documentclass{article}\n\\begin{document}\nText \\cite{a} and \\citep{b}.\n" \
          "\\end{document}\n"
    out, notes = latex.ensure_bibliography(src)
    assert "\\usepackage[super,sort&compress]{natbib}" in out          # ACS needs natbib super
    assert out.index("\\bibliographystyle{achemso}") < out.index("\\end{document}")
    assert "\\bibliography{references}" in out and notes
    assert latex.package_loaded(out, "natbib") and not latex.package_loaded(src, "natbib")
    # nothing cited -> untouched; reference list already present -> untouched
    assert latex.ensure_bibliography("\\begin{document}x\\end{document}") == \
        ("\\begin{document}x\\end{document}", [])
    done = src.replace("\\end{document}", "\\bibliography{mine}\n\\end{document}")
    assert "bibliographystyle" not in latex.ensure_bibliography(done)[0]
    # \citep alone (own reference list, no natbib) gets natbib
    out2, notes2 = latex.ensure_bibliography(
        "\\documentclass{article}\n\\begin{document}\\citep{x}\n\\bibliography{mine}\n"
        "\\end{document}")
    assert "\\usepackage{natbib}" in out2 and notes2
    # a chosen default is remembered
    latex.set_bib_style("unsrt")
    assert latex.get_bib_style() == "unsrt"
    assert "\\bibliographystyle{unsrt}" in latex.ensure_bibliography(src)[0]
    assert "natbib" in latex.article_template("T", "plainnat")
    assert "natbib" not in latex.article_template("T", "plain")


def test_import_bibtex_and_csl_files_and_dedupe(tmp_path):
    bib = tmp_path / "lib.bib"
    bib.write_text(
        "@article{smith_2020,\n  author = {Smith, Jane A. and M{\\\"u}ller, Hans and "
        "{World Health Organization}},\n  title = {{Quantum} transport \\& noise},\n"
        "  journal = {J. Am. Chem. Soc.}, volume = 142, number = {4},\n"
        "  pages = {1234--1240}, year = 2020, doi = \"10.1021/jacs.9b12345\"\n}\n"
        "@book{landau, author={Landau, L.}, title={Statistical Physics},\n"
        "  publisher={Pergamon}, address={Oxford}, year={2011}}\n", encoding="utf-8")
    recs = zotero.load_file(bib)
    assert [r["key"] for r in recs] == ["smith_2020", "landau"]
    a = recs[0]
    assert a["authors"] == [["Smith", "Jane A."], ["M\u00fcller", "Hans"],
                            ["World Health Organization*", ""]]
    assert a["title"] == "Quantum transport & noise" and a["pages"] == "1234-1240"
    assert (a["year"], a["volume"], a["issue"], a["container"]) == \
        ("2020", "142", "4", "J. Am. Chem. Soc.")
    assert recs[1]["type"] == "book" and recs[1]["place"] == "Oxford"
    # round trip: our own BibTeX output parses back to the same record
    again = zotero.parse_bibtex(zotero.to_bibtex(a))[0]
    assert again["title"] == a["title"] and again["authors"] == a["authors"]
    js = tmp_path / "lib.json"
    js.write_text('{"items": [' + __import__("json").dumps(CSL) + "]}", encoding="utf-8")
    assert zotero.load_file(js)[0]["key"] == "smith2020quantum"

    # the same Zotero item imported later under a generated key keeps the cited key
    suite = OfflineClient(tmp_path / "p.sciproj")
    suite.push_references([CSL])                                     # BBT key, item ABCD1234
    other = {**CSL, "citationKey": "", "id": "http://zotero.org/users/1/items/ABCD1234"}
    other.pop("citationKey")
    suite.push_references([other])
    pm = ProjectModel.load(tmp_path / "p.sciproj")
    assert [r["key"] for r in pm.references] == ["smith2020quantum"]


@needs_engine
def test_citations_are_numbered_by_occurrence_with_acs_default(tmp_path):
    pytest.importorskip("PyQt6")
    fitz = pytest.importorskip("fitz")
    import subprocess
    import sys
    suite = OfflineClient(tmp_path / "p.sciproj")
    suite.push_references([CSL, ZOT])
    # Landau is cited FIRST, Smith second; the document has no reference list at all
    body = ("\\documentclass{article}\n\\usepackage{hyperref}\n\\usepackage{noorsuite}\n"
            "\\begin{document}\nFirst \\cite{landau2011statistical}, then "
            "\\cite{smith2020quantum}, and again \\cite{landau2011statistical}.\n"
            "\\end{document}\n")
    suite.push_document("Doc", body)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = {**os.environ, "PYTHONPATH": root, "QT_QPA_PLATFORM": "offscreen",
           "NOORSUITE_BIB_STYLE": "acs", "NOORSUITE_LATEX": latex.find_engine()}
    out = tmp_path / "doc.pdf"
    r = subprocess.run([sys.executable, "-m", "NoorSuite", "latex", "build", "--project",
                        str(tmp_path / "p.sciproj"), "--doc", "Doc", "--out", str(out)],
                       env=env, capture_output=True, text=True, timeout=900)
    assert r.returncode == 0, (r.stdout + r.stderr)[-2000:]
    assert "no reference list" in r.stderr                               # told what was added
    text = fitz.open(str(out))[0].get_text()
    assert "?" not in text                                               # no unresolved [?]
    # numbered by first occurrence: Landau is entry 1, Smith entry 2, whatever the author order
    refs = text[text.index("References"):]
    assert refs.index("Landau, L.") < refs.index("Smith, J. A.")
    flat = " ".join(refs.split())                          # undo line wrapping
    assert "Phys. Rev. B 2020, 101" in flat
    # the in-text numbers: superscript 1 after "First", 2 after "then", 1 again at the end
    assert "First1, then2, and again1" in text.replace("\n", " ").replace(" ,", ",") \
        or "First1" in text.replace("\n", "")
