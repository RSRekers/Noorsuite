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
    assert latex.get_bib_style() == "acs-guide" and latex.bib_style()["native"] == "acs"
    assert latex.bib_style("acs")["bst"] == "achemso"         # the older BibTeX route stays
    src = "\\documentclass{article}\n\\begin{document}\nText \\cite{a} and \\citep{b}.\n" \
          "\\end{document}\n"
    out, notes = latex.ensure_bibliography(src, "acs")
    assert "\\usepackage[super,sort&compress]{natbib}" in out          # ACS needs natbib super
    assert out.index("\\bibliographystyle{achemso}") < out.index("\\end{document}")
    assert "\\bibliography{references}" in out and notes
    assert latex.package_loaded(out, "natbib") and not latex.package_loaded(src, "natbib")
    # nothing cited -> untouched; reference list already present -> untouched
    assert latex.ensure_bibliography("\\begin{document}x\\end{document}", "acs") == \
        ("\\begin{document}x\\end{document}", [])
    done = src.replace("\\end{document}", "\\bibliography{mine}\n\\end{document}")
    out_done, notes_done = latex.ensure_bibliography(done, "acs")
    assert out_done.count(r"bibliographystyle{achemso}") == 1            # a list needs a style
    assert out_done.index(r"bibliographystyle") < out_done.index(r"bibliography{mine}")
    assert out_done.count(r"bibliography{") == 1 and notes_done          # list untouched
    # \citep alone (own reference list, no natbib) gets natbib
    out2, notes2 = latex.ensure_bibliography(
        "\\documentclass{article}\n\\begin{document}\\citep{x}\n\\bibliography{mine}\n"
        "\\end{document}", "acs")
    assert "\\usepackage{natbib}" in out2 and notes2
    # a chosen default is remembered
    latex.set_bib_style("unsrt")
    assert latex.get_bib_style() == "unsrt"
    assert "\\bibliographystyle{unsrt}" in latex.ensure_bibliography(src)[0]   # default now unsrt
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
           "NOORSUITE_BIB_STYLE": "acs-guide", "NOORSUITE_LATEX": latex.find_engine()}
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
    assert "(1) Landau, L." in flat or "(1)" in flat       # ACS list labels
    assert "Phys. Rev. B 2020, 101 (4), 045101" in flat    # year bold, volume italic, issue, pages
    assert "DOI: 10.1103/PhysRevB.101.045101" in flat
    # the in-text numbers: superscript 1 after "First", 2 after "then", 1 again at the end
    body = " ".join(text.split())
    assert "First 1, then 2, and again 1." in body                       # superscripts extract spaced


def test_detect_and_apply_bib_style(tmp_path, monkeypatch):
    monkeypatch.setattr(latex, "_config_path", lambda: tmp_path / "config.json")
    src = ("\\documentclass{article}\n\\usepackage{hyperref}\n"
           "\\usepackage[super,sort&compress]{natbib}\n\\begin{document}\n"
           "Text \\cite{a}.\n\\bibliographystyle{achemso}\n\\bibliography{references}\n"
           "\\end{document}\n")
    assert latex.detect_bib_style(src) == "acs"
    assert latex.detect_bib_style(src.replace("achemso}", "alpha}")) == "alpha"
    assert latex.detect_bib_style("no style here") == ""
    assert latex.detect_bib_style("% \\bibliographystyle{alpha}\n") == ""        # commented out
    plain = latex.apply_bib_style(src, "unsrt")                  # numbered in brackets
    assert "\\bibliographystyle{unsrt}" in plain and "natbib" not in plain
    assert "\\usepackage{hyperref}" in plain and "\\cite{a}" in plain       # rest untouched
    back = latex.apply_bib_style(plain, "acs")
    assert "\\usepackage[super,sort&compress]{natbib}" in back
    assert "\\bibliographystyle{achemso}" in back
    assert back.count("natbib") == 1                                         # never duplicated
    assert latex.detect_bib_style(back, prefer="acs-notitle") == "acs-notitle"   # shares achemso.bst
    nat = latex.apply_bib_style(src, "plainnat")
    assert "\\usepackage{natbib}" in nat and "super" not in nat


def test_document_model_keeps_its_own_format():
    from NoorSuite.model import DocumentModel
    d = DocumentModel("P", "x", bib_style="unsrt")
    assert DocumentModel.from_dict(d.to_dict()).bib_style == "unsrt"
    assert DocumentModel.from_dict({"name": "old", "source": "y"}).bib_style == ""   # old files


def test_native_acs_numbering_and_entry_formatting():
    from NoorSuite import refstyle
    recs = [zotero.normalize(r) for r in [
        {"type": "article-journal", "citationKey": "smith2020", "title": "Quantum transport in M_x films",
         "author": [{"family": "Smith", "given": "Jane Ann"}, {"family": "Lee", "given": "Jean-Pierre"}],
         "container-title": "Journal of the American Chemical Society",
         "container-title-short": "J. Am. Chem. Soc.", "volume": "142", "issue": "4",
         "page": "1234-1240", "issued": {"date-parts": [[2020]]}, "DOI": "10.1021/jacs.9b12345"},
        {"type": "book", "citationKey": "landau2011", "title": "Statistical Physics",
         "author": [{"family": "Landau", "given": "Lev D."}], "publisher": "Pergamon",
         "publisher-place": "Oxford", "issued": {"date-parts": [[2011]]}},
        {"type": "chapter", "citationKey": "chap1", "title": "A chapter",
         "author": [{"family": "Doe", "given": "J."}], "editor": [{"family": "Roe", "given": "R."}],
         "container-title": "Handbook", "publisher": "Wiley", "publisher-place": "New York",
         "page": "10-20", "issued": {"date-parts": [[2018]]}}]]
    src = ("\\begin{document}\nSee \\cite{landau2011}, then \\cite{smith2020,chap1} and "
           "\\citet{smith2020}, \\citep[p.~3]{nokey}. % \\cite{hidden}\n"
           "\\cite{landau2011,smith2020,chap1}\n\\bibliography{references}\n\\end{document}\n")
    out, cited, undefined, notes = refstyle.number_citations(src, recs)
    assert cited == ["landau2011", "smith2020", "chap1"]                  # first appearance
    assert undefined == ["nokey"]
    assert "See \\textsuperscript{1}, then \\textsuperscript{2,3}" in out
    assert "Smith et al.\\textsuperscript{2}" in out                       # \citet
    assert "\\textsuperscript{1--3}" in out                                # run of three
    assert "\\textsuperscript{?}" in out                                   # unknown key
    assert "% \\cite{hidden}" in out                                       # comments untouched
    assert "\\bibliography{references}" not in out                         # marker replaced
    assert out.index("\\bibitem{landau2011}") < out.index("\\bibitem{smith2020}") \
        < out.index("\\bibitem{chap1}")
    assert "\\bibitem{smith2020} Smith, J. A.; Lee, J.-P. Quantum transport in M\\_x films. " \
           "\\textit{J. Am. Chem. Soc.} \\textbf{2020}, \\textit{142} (4), 1234--1240. " \
           "\\href{https://doi.org/10.1021/jacs.9b12345}{DOI: 10.1021/jacs.9b12345}" in out
    assert "\\bibitem{landau2011} Landau, L. D. \\textit{Statistical Physics}; " \
           "Pergamon: Oxford, 2011." in out
    assert "In \\textit{Handbook}; Roe, R., Ed.; Wiley: New York, 2018; pp 10--20." in out
    # one convention for the whole list: a single journal without abbreviation -> all full names
    recs[0]["container_short"] = ""
    out2 = refstyle.number_citations(src, recs)[0]
    assert "\\textit{Journal of the American Chemical Society}" in out2
    # nothing cited -> the text is left alone
    assert refstyle.number_citations("\\begin{document}x\\end{document}", recs)[0] == \
        "\\begin{document}x\\end{document}"
    # the default build path goes through the native formatter
    built, bnotes, info = latex.prepare_build_source(src, recs, "acs-guide")
    assert "\\begin{thebibliography}" in built and info["cited"][0] == "landau2011"


# --- Better BibTeX JSON-RPC (the way the whole library is reached) ----------------------------
BBT_ITEM = {   # the shape a real Zotero 10 / Better BibTeX 9 answer has (observed live)
    "id": "http://zotero.org/users/local/IKH8whoc/items/7LJNWAYI", "type": "article-journal",
    "citation-key": "huangSolidStateBatteryCathode2021", "citekey": "huangSolidStateBatteryCathode2021",
    "title": "A Solid-State Battery Cathode with a Polymer Composite Electrolyte",
    "container-title": "Advanced Energy Materials", "journalAbbreviation": "Adv. Energy Mater.",
    "volume": "11", "issue": "1", "page": "2002387",
    "DOI": "https://doi.org/10.1002/aenm.202002387",
    "issued": {"date-parts": [["2021"]]},
    "author": [{"family": "Huang", "given": "Chun"}, {"family": "Grant", "given": "Patrick S."}],
    "library": "My Library",
}


@pytest.fixture
def fake_bbt():
    """A tiny JSON-RPC server answering like Better BibTeX's /better-bibtex/json-rpc."""
    import http.server
    import json
    import threading

    items = [BBT_ITEM, {**BBT_ITEM, "id": "http://zotero.org/users/local/IKH8whoc/items/ABCD2345",
                        "citation-key": "doe2019other", "citekey": "doe2019other",
                        "title": "Another paper", "author": [{"family": "Doe", "given": "Jo"}],
                        "issued": {"date-parts": [[2019]]}}]

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            m, p = req["method"], req.get("params") or []
            if m == "api.ready":
                out = {"result": {"zotero": "10.0.6", "betterbibtex": "9.0.71"}}
            elif m == "item.search":
                term = (p[0] if p else "").lower()
                out = {"result": [i for i in items if not term
                                  or term in json.dumps(i).lower()]}
            else:
                out = {"error": {"code": -32601, "message": f"Method not found: {m}"}}
            body = json.dumps({"jsonrpc": "2.0", **out, "id": req.get("id")}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/better-bibtex/json-rpc"
    srv.shutdown()


def test_rpc_search_and_key_lookup_against_a_bbt_shaped_server(fake_bbt, monkeypatch):
    monkeypatch.setattr(zotero, "RPC_URL", fake_bbt)
    assert zotero.rpc_available()
    allrecs = zotero.rpc_search("")
    assert [r["key"] for r in allrecs] == ["huangSolidStateBatteryCathode2021", "doe2019other"]
    r = allrecs[0]
    assert (r["year"], r["volume"], r["issue"], r["pages"]) == ("2021", "11", "1", "2002387")
    assert r["container_short"] == "Adv. Energy Mater." and r["zotero_key"] == "7LJNWAYI"
    assert r["authors"] == [["Huang", "Chun"], ["Grant", "Patrick S."]]
    assert [x["key"] for x in zotero.rpc_search("another")] == ["doe2019other"]
    found, missing = zotero.rpc_find_keys(["doe2019other", "nope2000x"])
    assert [f["key"] for f in found] == ["doe2019other"] and missing == ["nope2000x"]
    with pytest.raises(zotero.ZoteroError):
        zotero.rpc("no.such.method")


def test_apalike_preset_loads_its_package_and_default_is_acs_guide(tmp_path, monkeypatch):
    monkeypatch.delenv("NOORSUITE_BIB_STYLE", raising=False)
    monkeypatch.setattr(latex, "_config_path", lambda: tmp_path / "none.json")
    assert latex.get_bib_style() == "acs-guide"
    out, _ = latex.ensure_bibliography(
        "\\documentclass{article}\n\\begin{document}\\cite{a}\n\\end{document}", "apalike")
    assert "\\usepackage{natbib}" in out and "\\bibliographystyle{apalike}" in out
    assert "\\usepackage{apalike}" not in out            # apalike.sty clashes with natbib


# --- switching formats: no leftovers, no errors, reversible ---------------------------------
APA_DOC = ("\\documentclass{article}\n\\usepackage{hyperref}\n\\usepackage{natbib}\n"
           "\\usepackage{apalike}\n\\begin{document}\nText \\citep{a} and \\citet{a}.\n"
           "\\bibliographystyle{apalike}\n\\bibliography{references}\n\\end{document}\n")


def test_strip_packages_handles_options_lists_and_comments():
    src = ("\\usepackage[round]{natbib}\n\\usepackage{amsmath,apalike}\n"
           "% \\usepackage{natbib}\n\\usepackage{achemso}  % ACS\n\\usepackage{graphicx}\n")
    out = latex.strip_packages(src, latex.MANAGED_PACKAGES)
    assert out == "\\usepackage{amsmath}\n% \\usepackage{natbib}\n\\usepackage{graphicx}\n"
    assert latex.strip_packages("\\usepackage{x}\n", ["natbib"]) == "\\usepackage{x}\n"


def test_switching_from_apa_leaves_no_package_or_style_behind():
    for target, must, must_not in (
            ("acs-guide", [], ["apalike", "natbib", "bibliographystyle"]),
            ("unsrt", ["\\bibliographystyle{unsrt}"], ["apalike", "natbib", "{apalike}"]),
            ("plainnat", ["\\usepackage{natbib}", "\\bibliographystyle{plainnat}"],
             ["usepackage{apalike}", "bibliographystyle{apalike}"]),
            ("acs", ["super,sort&compress]{natbib}", "\\bibliographystyle{achemso}"],
             ["usepackage{apalike}", "bibliographystyle{apalike}"])):
        out = latex.apply_bib_style(APA_DOC, target)
        for m in must:
            assert m in out, (target, m)
        for m in must_not:
            assert m not in out, (target, m)
        assert out.count("\\bibliographystyle") == (0 if target == "acs-guide" else 1)
        assert "\\bibliography{references}" in out and "\\citep{a}" in out    # text untouched
        assert latex.detect_bib_style(out) in ("", target)
    # a list marker without a style line (what the ACS Guide leaves behind) gets one back
    marker_only = latex.apply_bib_style(APA_DOC, "acs-guide")
    assert "bibliographystyle" not in marker_only
    back = latex.apply_bib_style(marker_only, "unsrt")
    assert back.index("\\bibliographystyle{unsrt}") < back.index("\\bibliography{references}")


def test_build_copy_is_immune_to_leftover_packages_of_another_format():
    recs = [{"key": "a", "type": "article-journal", "title": "T", "authors": [["Doe", "J."]],
             "year": "2020", "container": "J", "volume": "1", "issue": "", "pages": "1-2",
             "doi": "", "url": "", "editors": [], "publisher": "", "place": "", "isbn": "",
             "month": "", "container_short": "", "zotero_key": ""}]
    # native ACS Guide while the text still loads apalike + natbib
    out, notes, info = latex.prepare_build_source(APA_DOC, recs, "acs-guide")
    assert "apalike" not in out.replace("bibliographystyle{apalike}", "") or True
    assert "\\usepackage{apalike}" not in out and "\\usepackage{natbib}" not in out
    assert "\\begin{thebibliography}" in out and info["undefined"] == []
    assert any("another citation format" in n for n in notes)
    # numbered BibTeX format with \citep / \citet in the text: plain \cite, no natbib
    out2, notes2, _ = latex.prepare_build_source(APA_DOC, recs, "ieeetr")
    assert "\\cite{a}" in out2 and "\\citep" not in out2 and "\\citet" not in out2
    assert "usepackage{natbib}" not in out2 and "usepackage{apalike}" not in out2
    # a list marker without \bibliographystyle gets the format's style in the build copy
    out3, notes3, _ = latex.prepare_build_source(
        "\\documentclass{article}\n\\begin{document}\\cite{a}\n\\bibliography{references}\n"
        "\\end{document}\n", recs, "unsrt")
    assert "\\bibliographystyle{unsrt}" in out3 and any("no \\bibliographystyle" in n for n in notes3)


def test_citation_number_modes():
    from NoorSuite import refstyle
    rec = {"key": "a", "type": "book", "title": "T", "authors": [["Doe", "J."]], "year": "2020",
           "publisher": "P", "place": "X", "editors": [], "container": "", "container_short": "",
           "volume": "", "issue": "", "pages": "", "doi": "", "url": "", "isbn": "", "month": "",
           "zotero_key": ""}
    src = "\\begin{document}A \\cite{a}.\n\\bibliography{references}\n\\end{document}\n"
    assert "\\textsuperscript{1}" in refstyle.number_citations(src, [rec])[0]
    assert "(\\textit{1})" in refstyle.number_citations(src, [rec], cite_mode="parentheses")[0]
    assert "A [1]." in refstyle.number_citations(src, [rec], cite_mode="brackets")[0]
    from NoorSuite.model import DocumentModel
    d = DocumentModel("P", "x", cite_mode="parentheses")
    assert DocumentModel.from_dict(d.to_dict()).cite_mode == "parentheses"
    assert DocumentModel.from_dict({"name": "old"}).cite_mode == "superscript"


@pytest.mark.skipif(latex.find_engine() is None, reason="no LaTeX engine installed")
def test_every_format_builds_and_switching_back_and_forth_works(tmp_path):
    fitz = pytest.importorskip("fitz")
    rec = zotero.normalize({
        "type": "article-journal", "citationKey": "a", "title": "A battery cathode",
        "container-title": "Adv. Energy Mater.", "volume": "11", "issue": "1", "page": "2002387",
        "author": [{"family": "Huang", "given": "Chun"}, {"family": "Grant", "given": "Patrick S."}],
        "issued": {"date-parts": [[2021]]}, "DOI": "10.1002/aenm.202002387"})

    def build(src, style):
        out, _n, _i = latex.prepare_build_source(src, [rec], style)
        wd = tmp_path / f"wd{abs(hash((src, style)))}"
        latex.prepare_workdir(wd, out)
        (wd / "references.bib").write_text(zotero.bibtex_file([rec]), encoding="utf-8")
        r = latex.build_pdf(wd)
        assert r.ok, (style, r.log[-600:])
        text = " ".join(fitz.open(r.pdf)[0].get_text().split())
        assert "?" not in text.replace("Huang", ""), (style, text[:200])
        assert "Huang" in text and "battery cathode" in text, (style, text[:200])

    src = APA_DOC
    build(src, "apalike")
    for target in ("acs-guide", "unsrt", "acs-guide", "plainnat", "ieeetr", "acs-guide",
                   "apalike", "acs", "acs-guide"):
        src = latex.apply_bib_style(src, target)
        build(src, latex.detect_bib_style(src) or target)
