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
