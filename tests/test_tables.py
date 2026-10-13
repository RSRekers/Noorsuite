"""Data tables for documents: rendering, expansion, API, editor and Word export."""
import re
import zipfile

import numpy as np
import pytest

from NoorSuite import docexport, tables
from NoorSuite.model import DataObject, ProjectModel, TableModel
from tests.test_gui_features import app, win  # noqa: F401  (window fixtures)


def _data():
    return DataObject("IV", {"V": np.array([0.0, 0.5, 1.0, 1.5]),
                             "I": np.array([1e-6, 2.5e-4, -3.0e-3, np.nan]),
                             "n": np.array([4.0, 2.0, 3.0, 1.0])},
                      column_order=["V", "I", "n"], units={"V": "V", "I": "A"})


def test_number_formats():
    f = tables.format_number
    assert f(12.3456, "fixed", 2) == "12.35"
    assert f(-1.5, "fixed", 1) == "$-$1.5"
    assert f(1234.5678, "sig", 3) == r"1.23\,$\times$\,10$^{3}$"
    assert f(0.000123, "sci", 2) == r"1.23\,$\times$\,10$^{-4}$"
    assert f(2.6, "int") == "3" and f(0.25, "auto") == "0.25"
    assert f(float("nan")) == "--" and f(float("inf")) == "--"
    assert f(1e-8, "auto") == r"1\,$\times$\,10$^{-8}$"


def test_render_headers_units_rows_sort_and_rules():
    d = _data()
    cols = tables.normalize_columns(d, [("V", "Voltage"), {"col": "I", "fmt": "sci", "digits": 1},
                                        "n"])
    tm = TableModel("iv", d.id, cols)
    out = tables.render_table(tm, d)
    assert out.startswith(r"\begin{tabular}{rrr}") and r"\toprule" in out and r"\bottomrule" in out
    assert "Voltage & I (A) & n \\\\" in out.replace(r" \\", " \\\\")      # header + unit
    assert out.count("\\\\") == 5                                         # header + 4 rows
    assert r"$-$3.0\,$\times$\,10$^{-3}$" in out and "& -- &" in out
    tm.sort_by, tm.descending, tm.row_start, tm.max_rows = "n", True, 1, 2
    out2 = tables.render_table(tm, d)
    body = [ln for ln in out2.splitlines() if ln.endswith("\\\\")][1:]
    assert len(body) == 2 and body[0].startswith("0.5") is False        # n = 4 first -> V = 0
    assert body[0].split("&")[0].strip() == "0"
    tm.booktabs = False
    assert r"\hline" in tables.render_table(tm, d) and "toprule" not in tables.render_table(tm, d)
    with pytest.raises(ValueError):
        tables.normalize_columns(d, ["nope"])
    with pytest.raises(ValueError):
        tables.normalize_columns(d, [{"col": "V", "fmt": "weird"}])


def test_expand_replaces_references_adds_booktabs_and_reports_missing():
    d = _data()
    tm = TableModel("IV summary", d.id, tables.normalize_columns(d, ["V", "n"]))
    src = (r"\documentclass{article}" "\n" r"\begin{document}" "\n"
           r"\begin{table}\caption{x}\noortable{iv-summary}\end{table}" "\n"
           r"% \noortable{commented}" "\n" r"\noortable{nope} \noortable{" + tm.table_id + "}\n"
           r"\end{document}")
    out, missing = tables.expand_tables(src, [tm], {d.id: d})
    assert missing == ["nope"] and out.count(r"\begin{tabular}") == 2
    assert r"\usepackage{booktabs}" in out and "% \\noortable{commented}" in out
    assert tables.table_refs(src) == ["iv-summary", "nope", tm.table_id]


def test_table_roundtrips_in_the_project_and_fingerprint_follows_data(tmp_path):
    d = _data()
    tm = TableModel("T", d.id, tables.normalize_columns(d))
    tm.row_end, tm.sort_by = 3, "V"
    pm = ProjectModel()
    pm.data_objects, pm.tables = [d], [tm]
    back = ProjectModel.from_json(pm.to_json()).tables[0]
    assert (back.name, back.row_end, back.sort_by) == ("T", 3, "V")
    assert [c["col"] for c in back.columns] == ["V", "I", "n"]
    f1 = tables.fingerprint(tm, d)
    d.columns["V"][0] = 9.0
    assert tables.fingerprint(tm, d) != f1


def test_offline_push_table_and_headless_export(tmp_path):
    import pandas as pd
    from NoorSuite.client import OfflineClient
    s = OfflineClient(tmp_path / "p.sciproj")
    s.push_dataframe(pd.DataFrame({"V": [0.0, 1.0], "I": [0.1, 0.2]}), name="IV A3")
    s.push_table("IV summary", "IV A3", ["V", ("I", "Current (mA)")], rows=(1, 2), digits=2,
                 fmt="fixed")
    pm = ProjectModel.load(tmp_path / "p.sciproj")
    t = pm.tables[0]
    assert t.name == "IV summary" and t.row_end == 2 and t.data_id == pm.data_objects[0].id
    assert [c["header"] for c in t.columns] == ["", "Current (mA)"]
    assert t.columns[1]["fmt"] == "fixed" and t.columns[1]["digits"] == 2
    assert list(s.list_tables()["name"]) == ["IV summary"]
    assert "noortable{iv-summary}" in s.table_snippet("IV summary", caption="Currents")
    assert s.push_table("x", "no such data")["status"] == "error"
    assert s.push_table("y", "IV A3", ["missing-col"])["status"] == "error"


def test_gui_editor_inserts_a_live_table_and_it_updates_with_the_data(win, monkeypatch):
    from NoorSuite.tabledialog import TableEditorDialog
    win.handle_incoming_ipc({"action": "append_dataframe", "name": "IV",
                             "columns": {"V": [0., 1., 2.], "I": [1., 4., 9.]},
                             "column_order": ["V", "I"], "units": {"V": "V", "I": "mA"}})
    obj = next(iter(win.repository.values()))
    dlg = TableEditorDialog(win, None)
    assert dlg.data_combo.currentData() == obj.id and dlg.col_table.rowCount() == 2
    dlg.name_edit.setText("IV table")
    dlg.col_table.item(0, 1).setText("Voltage (V)")
    dlg.col_table.cellWidget(1, 2).setCurrentIndex(1)             # fixed decimals
    dlg.col_table.cellWidget(1, 3).setValue(1)
    assert "Voltage (V) & I (mA)" in dlg.preview.toPlainText()
    assert "4.0" in dlg.preview.toPlainText()
    dlg._accept()
    tm = dlg.table
    assert tm is not None and [c["col"] for c in tm.columns] == ["V", "I"]
    win.upsert_table(tm)
    text, missing = win.expand_document_tables(r"\begin{document}\noortable{iv-table}\end{document}")
    assert not missing and "Voltage (V)" in text
    fp = win.table_fingerprint(tm.table_id)
    obj.columns["I"][1] = 5.5                                    # data changes -> table follows
    assert win.table_fingerprint(tm.table_id) != fp
    assert "5.5" in win.expand_document_tables(r"\noortable{iv-table}")[0]
    # saved with the project
    pmodel = win._project_model()
    assert [t.name for t in pmodel.tables] == ["IV table"]
    # a skill push reaches the window too
    win.handle_incoming_ipc({"action": "table", "name": "Via skill", "data": "IV",
                             "columns": "__all__", "options": {"max_rows": 2}})
    assert any(t.name == "Via skill" and t.max_rows == 2 for t in win.tables.values())


@pytest.mark.skipif(docexport.find_pandoc() is None, reason="pandoc not installed")
def test_word_export_contains_a_real_table(tmp_path):
    d = _data()
    tm = TableModel("T", d.id, tables.normalize_columns(d, ["V", "n"]))
    src = (r"\documentclass{article}\begin{document}" "\n"
           r"\begin{table}\caption{Currents}\noortable{T}\end{table}" "\n" r"\end{document}")
    text, _ = tables.expand_tables(src, [tm], {d.id: d})
    out = tmp_path / "o.docx"
    ok, log = docexport.export_office(text, [], "superscript", [], [], out, "docx",
                                      tmp_path / "w", lambda *a: None, lambda *a: None,
                                      docexport.find_pandoc())
    assert ok, log
    xml = zipfile.ZipFile(out).read("word/document.xml").decode("utf8")
    assert xml.count("<w:tbl>") == 1 and xml.count("<w:tr>") == 5       # header + 4 rows
    flat = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", xml))
    assert "Table 1. Currents" in flat and "V" in flat


def test_figure_commands_in_comments_are_left_alone():
    src = r"\usepackage{noorsuite}   % \noorsheet{...}: sheets" "\n" r"\noorsheet{a} % \noorsheet{b}"
    out, missing = docexport.swap_figures(src, {("noorsheet", "a"): "f/a.png"})
    assert missing == [] and r"% \noorsheet{...}: sheets" in out
    assert r"\includegraphics{f/a.png} % \noorsheet{b}" in out
