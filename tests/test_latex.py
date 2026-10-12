import os

import pandas as pd
import pytest

from NoorSuite import latex
from NoorSuite.client import OfflineClient
from NoorSuite.model import DocumentModel, ProjectModel, SheetModel


def test_refs_snippet_and_resolution():
    src = "a \\noorsheet{abc12345} % \\noorsheet{ignored}\n\\noorsheet[width=5cm]{IV-curve}\n"
    assert latex.figure_refs(src) == ["abc12345", "IV-curve"]
    sheets = [SheetModel("IV curve", sheet_id="abc12345"), SheetModel("Other", sheet_id="ff00")]
    assert latex.resolve_ref("abc12345", sheets) is sheets[0]
    assert latex.resolve_ref("iv-curve", sheets) is sheets[0]          # slug of the name
    assert latex.resolve_ref("IV curve", sheets) is sheets[0]
    assert latex.resolve_ref("nope", sheets) is None
    snip = latex.figure_snippet("abc12345", "IV curve", "Caption here.")
    assert r"\noorsheet{abc12345}" in snip and r"\caption{Caption here.}" in snip
    assert latex.figure_refs(snip) == ["abc12345"]


def test_engine_commands_and_template():
    assert latex.engine_commands("/x/tectonic.exe", "main.tex", "out")[0][:3] == \
        ["/x/tectonic.exe", "-X", "compile"]
    assert len(latex.engine_commands("pdflatex", "main.tex", "out")) == 2     # refs need 2 runs
    t = latex.article_template("My title")
    assert r"\title{My title}" in t and "@TITLE@" not in t
    assert r"\usepackage{noorsuite}" in t


def test_documents_roundtrip_and_tree(tmp_path):
    suite = OfflineClient(tmp_path / "p.sciproj")
    suite.push_dataframe(pd.DataFrame({"x": [0.0, 1.0], "y": [1.0, 2.0]}), name="d")
    suite.plot("d", "x", "y", new_sheet=True, title="Fig A", folder="Figures")
    suite.push_document("Paper", r"\section{One}", folder="Manuscripts")
    suite.push_document("Paper", r"\section{Two}", mode="append")
    assert suite.get_document("Paper") == "\\section{One}\n\n\\section{Two}"
    assert suite.list_documents().iloc[0]["name"] == "Paper"
    assert r"\noorsheet{" in suite.figure_snippet("Fig A", "Caption")
    pm = ProjectModel.load(tmp_path / "p.sciproj")
    assert isinstance(pm.documents[0], DocumentModel)
    docs = [n for n in pm.tree if n["type"] == "folder" and n["name"] == "Manuscripts"]
    assert docs and docs[0]["children"] == [{"type": "document", "doc_id": pm.documents[0].doc_id}]
    # moving a sheet around must leave document nodes alone
    suite.organize_sheet("Fig A", folder="Elsewhere")
    pm = ProjectModel.load(tmp_path / "p.sciproj")
    assert any(n.get("type") == "folder" and n.get("name") == "Manuscripts" for n in pm.tree)
    with pytest.raises(KeyError):
        suite.get_document("missing")


needs_engine = pytest.mark.skipif(latex.find_engine() is None, reason="no LaTeX engine installed")


@needs_engine
def test_headless_build_and_export(tmp_path):
    pytest.importorskip("PyQt6")
    import subprocess
    import sys
    suite = OfflineClient(tmp_path / "p.sciproj")
    suite.push_dataframe(pd.DataFrame({"t": [0.0, 1.0, 2.0], "y": [0.0, 1.0, 4.0]}), name="d")
    suite.plot("d", "t", "y", new_sheet=True, title="Curve")
    src = latex.article_template("T").replace(
        r"\noorsheet{sheet-id}", suite.figure_snippet("Curve", "A curve."))
    suite.push_document("Doc", src)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = {**os.environ, "PYTHONPATH": root, "QT_QPA_PLATFORM": "offscreen"}
    out = tmp_path / "doc.pdf"
    r = subprocess.run([sys.executable, "-m", "NoorSuite", "latex", "build", "--project",
                        str(tmp_path / "p.sciproj"), "--doc", "Doc", "--out", str(out)],
                       env=env, capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr[-1500:]
    assert out.stat().st_size > 5000
    bundle = tmp_path / "bundle"
    r = subprocess.run([sys.executable, "-m", "NoorSuite", "latex", "export", "--project",
                        str(tmp_path / "p.sciproj"), "--doc", "Doc", "--folder", str(bundle)],
                       env=env, capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr[-1500:]
    assert (bundle / "main.tex").is_file() and (bundle / "noorsuite.sty").is_file()
    assert list((bundle / "noorsuite-figures").glob("*.pdf"))
