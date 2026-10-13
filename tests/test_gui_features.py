"""Headless GUI checks (skipped without PyQt6): zoom, export, duplicate, plot options."""
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")
pytest.importorskip("PyQt6")

from io import BytesIO

from PyQt6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(app, monkeypatch, tmp_path):
    from NoorSuite import app as appmod
    # never touch the real ~/.scisuite_session.json (the user's live session)
    monkeypatch.setattr(appmod.SciSuiteWindow, "load_session", lambda self: None)
    monkeypatch.setattr(appmod.SciSuiteWindow, "save_session", lambda self: None)
    from NoorSuite import latex as _latex                      # never write the user's config
    monkeypatch.delenv("NOORSUITE_BIB_STYLE", raising=False)
    monkeypatch.setattr(_latex, "_config_path", lambda: tmp_path / "config.json")   # per test
    w = appmod.SciSuiteWindow(port=55590 + os.getpid() % 300)
    w._autosave_timer.stop()
    yield w
    w.ipc.stop()
    # release the window, its canvases and document tabs: dozens of live windows in one
    # process exhausted native resources (silent crashes late in a full run)
    try:
        for tab in list(w.doc_tabs.values()):
            tab._source_timer.stop()
        w.close()
        w.deleteLater()
        from PyQt6.QtWidgets import QApplication as _QA
        _QA.processEvents()
    except Exception:
        pass


def _plot(win, **extra):
    win.handle_incoming_ipc({"action": "append_dataframe", "name": "d",
                             "columns": {"x": [3., 1., 2.], "y": [9., 1., 4.]},
                             "column_order": ["x", "y"]})
    win.handle_incoming_ipc({"action": "add_to_sheet", "data_name": "d", "x_col": "x",
                             "y_cols": ["y"], "sheet": "__new__", "subplot_index": 0, **extra})
    return next(reversed(win.sheets.values()))


def test_plot_options_reach_the_trace(win):
    sm = _plot(win, trace_style={"plot_type": "Step", "sort_x": True, "color": "#123456"})
    t = sm.subplots[0].traces[0]
    assert (t.plot_type, t.sort_x, t.color) == ("Step", True, "#123456")


def test_duplicate_sheet_and_folder(win):
    sm = _plot(win, organize={"name": "Orig", "folder": "F"})
    folder = next(win.tree.topLevelItem(i) for i in range(win.tree.topLevelItemCount())
                  if win.tree.topLevelItem(i).text(0) == "F")
    sheet_item = folder.child(0)
    n = len(win.sheets)
    new = win._duplicate_tree_item(sheet_item)
    assert len(win.sheets) == n + 1 and new.text(0) == "Orig (copy)"
    assert folder.indexOfChild(new) == 1
    assert win.sheets[new.data(0, 0x100 + 2)].subplots[0].traces[0].data_id == \
        sm.subplots[0].traces[0].data_id
    fcopy = win._duplicate_tree_item(folder)
    assert fcopy.text(0) == "F (copy)" and fcopy.childCount() == 2
    assert len(win.sheets) == n + 1 + 2
    ids = {fcopy.child(i).data(0, 0x100 + 2) for i in range(2)}
    assert ids.isdisjoint({sheet_item.data(0, 0x100 + 2), new.data(0, 0x100 + 2)})


def test_zoom_keeps_inches_and_scales_dpi(win):
    sm = _plot(win)
    ps = win._open_sheet_tab(sm.sheet_id)
    win.show()
    QApplication.processEvents()
    inches = tuple(ps.fig.get_size_inches())
    base_dpi = ps.fig.dpi
    ps.canvas_host.set_zoom(2.0)
    QApplication.processEvents()
    assert ps.fig.dpi == pytest.approx(base_dpi * 2)
    assert tuple(ps.fig.get_size_inches()) == pytest.approx(inches, rel=0.02)
    ps.canvas_host.set_zoom(1.0)
    QApplication.processEvents()
    assert ps.fig.dpi == pytest.approx(base_dpi)


def test_clipboard_export_matches_display_proportions(win):
    from PIL import Image
    sm = _plot(win)
    ps = win._open_sheet_tab(sm.sheet_id)
    win.show()
    QApplication.processEvents()
    ps.render(win.repository, win.images)
    QApplication.processEvents()
    w_in, h_in = ps.fig.get_size_inches()
    buf = BytesIO()
    win._savefig_at_export_size(ps, buf, "png", wysiwyg=True)
    w, h = Image.open(BytesIO(buf.getvalue())).size
    assert (w, h) == (round(w_in * 300), round(h_in * 300))      # no tight re-crop
    assert tuple(ps.fig.get_size_inches()) == (w_in, h_in)       # figure restored


def test_ctrl_wheel_zooms_and_ctrl_0_resets(win):
    from PyQt6.QtCore import QPoint, QPointF, Qt
    from PyQt6.QtGui import QWheelEvent
    sm = _plot(win)
    ps = win._open_sheet_tab(sm.sheet_id)
    win.show()
    QApplication.processEvents()

    def wheel(mod):
        ev = QWheelEvent(QPointF(10, 10), QPointF(10, 10), QPoint(0, 0), QPoint(0, 120),
                         Qt.MouseButton.NoButton, mod, Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(ps.canvas, ev)

    wheel(Qt.KeyboardModifier.NoModifier)
    assert ps.canvas_host.zoom == 1.0                       # plain wheel does not zoom
    wheel(Qt.KeyboardModifier.ControlModifier)
    assert ps.canvas_host.zoom == pytest.approx(1.15)
    ps.canvas_host.set_zoom(1.0)
    assert ps.canvas_host.zoom == 1.0


def _heatmap_sheet(win, **style):
    import numpy as np
    z = np.add.outer(np.linspace(0, 10, 6), np.linspace(0, 40, 5))      # 6 rows (y) x 5 cols (x)
    win.handle_incoming_ipc({
        "action": "append_image", "name": "hm", "bytes": z.tobytes(), "shape": list(z.shape),
        "dtype": str(z.dtype), "axis_names": ["T", "f"],
        "axis_coords": {0: [100, 200, 300, 400, 500, 600], 1: [1, 2, 4, 8, 16]}})
    win.handle_incoming_ipc({"action": "add_image_to_sheet", "name": "hm", "sheet": "__new__",
                             "subplot_index": 0, "display_axes": [0, 1], "image_style": style})
    sm = next(reversed(win.sheets.values()))
    ps = win._open_sheet_tab(sm.sheet_id)
    win.show()
    QApplication.processEvents()
    return sm, ps


def test_heatmap_renders_at_coordinates_with_bands_isolines_and_ticks(win):
    from matplotlib.collections import QuadMesh
    from matplotlib.colors import BoundaryNorm
    sm, ps = _heatmap_sheet(win, cmap_bins=5, iso_show=True, iso_above=20, colorbar=True,
                            x_tick_mode="data", x_tick_every=2,
                            y_tick_mode="custom", y_tick_values="100, 300")
    ax = ps.fig.axes[0]
    mesh = next(c for c in ax.collections if isinstance(c, QuadMesh))
    assert isinstance(mesh.norm, BoundaryNorm) and len(mesh.norm.boundaries) == 6
    x0, x1 = ax.get_xlim()
    assert x0 < 1 and x1 > 16                                   # real x coordinates, not 0..5
    assert [t.get_text() for t in ax.get_xticklabels()] == ["1", "4", "16"]
    assert list(ax.get_yticks()) == [100.0, 300.0]
    assert len(ps.fig.axes) == 2                                # + colourbar axes
    iso_levels = [lv for c in ax.collections if hasattr(c, "levels") for lv in c.levels]
    assert iso_levels and min(iso_levels) >= 20                 # nothing below the threshold
    assert sm.subplots[0].image.aspect == "auto"                # heatmap default


def test_plain_image_still_renders_in_index_space(win):
    import numpy as np
    z = np.arange(12.0).reshape(3, 4)
    win.handle_incoming_ipc({"action": "append_image", "name": "im", "bytes": z.tobytes(),
                             "shape": [3, 4], "dtype": "float64"})
    win.handle_incoming_ipc({"action": "add_image_to_sheet", "name": "im", "sheet": "__new__",
                             "subplot_index": 0, "display_axes": [0, 1]})
    sm = next(reversed(win.sheets.values()))
    ps = win._open_sheet_tab(sm.sheet_id)
    win.show()
    QApplication.processEvents()
    ax = ps.fig.axes[0]
    assert ax.get_xlim() == (0.0, 4.0) and len(ax.images) == 1


def test_image_style_widget_roundtrips_heatmap_controls(win):
    sm, ps = _heatmap_sheet(win, iso_show=True, cmap_bins=4, x_tick_mode="data")
    sub = sm.subplots[0]
    w = win.axes_widget.image_widget
    win.sync_active_subplot_inspector()
    assert w.bins_spin.value() == 4 and w.iso_check.isChecked()
    w.iso_above_edit.setText("12.5")
    w.bounds_edit.setText("0, 20, 60")
    w.reverse_check.setChecked(True)
    w._tick_widgets["y"][0].setCurrentIndex(2)
    w._tick_widgets["y"][1].setText("100, 500")
    w._push()
    r = sub.image
    assert (r.iso_above, r.cmap_boundaries, r.cmap_reverse) == (12.5, "0, 20, 60", True)
    assert (r.y_tick_mode, r.y_tick_values, r.x_tick_mode) == ("custom", "100, 500", "data")
    from NoorSuite.model import ImageRef
    assert ImageRef.from_dict(r.to_dict()).iso_above == 12.5


def test_custom_tick_labels_on_plot_and_categorical_heatmap(win):
    import numpy as np
    sm = _plot(win, organize={"subplots": {0: {"x_tick_labels": "1=one; 3=three",
                                                "y_tick_labels": "ignored-no-equals"}}})
    ps = win._open_sheet_tab(sm.sheet_id)
    win.show()
    QApplication.processEvents()
    ax = ps.fig.axes[0]
    assert list(ax.get_xticks()) == [1.0, 3.0]
    assert [t.get_text() for t in ax.get_xticklabels()] == ["one", "three"]

    z = np.eye(3)
    win.handle_incoming_ipc({"action": "append_image", "name": "cat", "bytes": z.tobytes(),
                             "shape": [3, 3], "dtype": "float64", "axis_names": ["r", "c"],
                             "axis_coords": {0: ["u", "v", "w"], 1: ["u", "v", "w"]}})
    win.handle_incoming_ipc({"action": "add_image_to_sheet", "name": "cat", "sheet": "__new__",
                             "subplot_index": 0, "display_axes": [0, 1],
                             "organize": {"subplots": {0: {"x_tick_labels": "v=Vee"}}}})
    sm2 = next(reversed(win.sheets.values()))
    ps2 = win._open_sheet_tab(sm2.sheet_id)
    ps2.render(win.repository, win.images)
    ax2 = ps2.fig.axes[0]
    assert [t.get_text() for t in ax2.get_xticklabels()] == ["u", "Vee", "w"]   # renamed
    assert [t.get_text() for t in ax2.get_yticklabels()] == ["u", "v", "w"]     # auto -> every label


def _wait(cond, timeout=240):
    t0 = time.time()
    while not cond() and time.time() - t0 < timeout:
        QApplication.processEvents()
        time.sleep(0.05)
    return cond()


def test_document_tab_builds_and_double_click_opens_sheet(win):
    from NoorSuite import latex
    if latex.find_engine() is None:
        pytest.skip("no LaTeX engine installed")
    from NoorSuite.model import DocumentModel
    sm = _plot(win, organize={"name": "IV curve"})
    win.sheets[sm.sheet_id].fig_width_cm, win.sheets[sm.sheet_id].fig_height_cm = 10.0, 7.0
    win.show()
    dm = win.create_new_document("Paper", latex.article_template("T").replace(
        r"\noorsheet{sheet-id}", latex.figure_snippet(sm.sheet_id, "IV curve", "Cap.")), None)
    tab = win._open_document_tab(dm.doc_id)
    tab.auto_check.setChecked(False)
    opened = []
    tab.sheetRequested.connect(opened.append)
    tab.compile(force=True)
    assert _wait(lambda: not tab._building and tab._pdf), tab.log.toPlainText()[-800:]
    assert tab.status_label.text().strip().startswith("built"), tab.status_label.text()
    QApplication.processEvents()
    links = tab.preview._links
    assert links and links[0] and links[0][0][1] == sm.sheet_id
    rect = links[0][0][0]
    scale = tab.preview._px_per_pt()
    from PyQt6.QtCore import QPointF
    key = tab.preview.link_at(0, QPointF(rect.center().x() * scale, rect.center().y() * scale))
    assert key == sm.sheet_id
    tab.editor.sheetRequested.emit(sm.sheet_id)
    assert opened == [sm.sheet_id]
    # source editing reaches the model, the tree and the project file format
    tab.editor.setPlainText(tab.editor.toPlainText() + "\n% edit")
    assert win.documents[dm.doc_id].source.endswith("% edit")
    pm = win._project_model()
    assert [d.name for d in pm.documents] == ["Paper"]
    assert {"type": "document", "doc_id": dm.doc_id} in pm.tree
    tab._source_timer.stop()


def _doc_win(win, n_sheets=2, **doc_kw):
    """A window with ``n_sheets`` plotted sheets and an open document that references them."""
    from NoorSuite import latex
    ids = []
    win.handle_incoming_ipc({"action": "append_dataframe", "name": "d",
                             "columns": {"x": [1., 2., 3.], "y": [1., 4., 9.]},
                             "column_order": ["x", "y"]})
    for i in range(n_sheets):
        win.handle_incoming_ipc({
            "action": "add_to_sheet", "data_name": "d", "x_col": "x", "y_cols": ["y"],
            "sheet": "__new__", "subplot_index": 0,
            "organize": {"name": f"Fig {i}", "tags": ["iv" if i == 0 else "xrd"]}})
        ids.append(next(reversed(win.sheets.values())).sheet_id)
    src = latex.article_template("T").replace(
        r"\noorsheet{sheet-id}",
        "\n".join(latex.figure_snippet(i, f"Fig {n}", "Cap.") for n, i in enumerate(ids)))
    dm = win.create_new_document("Paper", src, None)
    tab = win._open_document_tab(dm.doc_id)
    tab.auto_check.setChecked(False)
    win.show()
    QApplication.processEvents()
    return ids, dm, tab


def test_document_theme_is_explicit_and_white_by_default(win):
    ids, dm, tab = _doc_win(win, 1)
    ss = tab.styleSheet()
    assert "#ffffff" in ss and "#000000" in ss                       # white ground, black text
    tab.dark_act.setChecked(True)
    assert "#1b1b1b" in tab.styleSheet() and "#f2f2f2" in tab.styleSheet()
    tab.dark_act.setChecked(False)
    assert "background: #ffffff; color: #000000" in tab.styleSheet()


def test_figure_panel_search_snippets_and_tree_drag_payload(win):
    from PyQt6.QtCore import Qt as _Qt
    ids, dm, tab = _doc_win(win, 3)
    fp = tab.figures
    assert fp.list.count() == 4                                      # 3 figures + the default sheet
    fp.search.setText("#xrd")                                         # tag scope
    visible = [fp.list.item(i).text() for i in range(fp.list.count()) if not fp.list.item(i).isHidden()]
    assert visible == ["Fig 1", "Fig 2"]
    fp.search.setText("fig 0 -xrd")
    assert [fp.list.item(i).text() for i in range(fp.list.count()) if not fp.list.item(i).isHidden()] == ["Fig 0"]
    fp.search.setText("")
    item = fp.list.item(0)
    snip = fp.snippets_for([item])
    assert f"\\noorsheet{{{item.data(_Qt.ItemDataRole.UserRole)}}}" in snip
    got = []
    fp.insertRequested.connect(got.append)
    fp.list.itemDoubleClicked.emit(item)
    assert got and "\\begin{figure}" in got[0]
    before = tab.source()
    tab.insert_text(snip)
    assert len(tab.source()) > len(before) and snip.strip() in tab.source()

    # dragging tree items (a sheet, or a folder of sheets) carries the figures as text
    tree = win.tree
    sheet_items = []

    def walk(it):
        if it.data(0, 0x100 + 1) == "sheet":
            sheet_items.append(it)
        for i in range(it.childCount()):
            walk(it.child(i))
    for i in range(tree.topLevelItemCount()):
        walk(tree.topLevelItem(i))
    md = tree.mimeData([sheet_items[0]])
    assert md.hasText() and "\\noorsheet{" in md.text() and md.text().count("\\begin{figure}") == 1
    md = tree.mimeData(sheet_items)
    assert md.text().count("\\begin{figure}") == len(sheet_items)


def test_zotero_pick_stores_references_and_inserts_cite(win, monkeypatch):
    from NoorSuite import zotero
    ids, dm, tab = _doc_win(win, 1)
    raw = [{"type": "article-journal", "citationKey": "doe2021test", "title": "A test",
            "author": [{"family": "Doe", "given": "J."}], "issued": {"date-parts": [[2021]]}}]
    monkeypatch.setattr(zotero, "cayw_pick", lambda *a, **k: raw)
    tab.editor.moveCursor(tab.editor.textCursor().MoveOperation.End)
    tab.pick_from_zotero(insert=True)
    assert _wait(lambda: "doe2021test" in win.references, timeout=10)
    QApplication.processEvents()
    assert "\\cite{doe2021test}" in tab.source()
    assert tab.refs.list.count() == 1
    assert win._project_model().references[0]["key"] == "doe2021test"
    tab.refs.list.selectAll()
    got = []
    tab.refs.insertRequested.connect(got.append)
    tab.refs._cite("citep")
    assert got == ["\\citep{doe2021test}"]
    from NoorSuite import latex as _l
    assert _l.package_loaded(tab.source(), "natbib")                   # template has it (ACS)
    assert tab.source().count("natbib") == 1                           # ... and not added twice
    tab.refs.style_combo.setCurrentIndex(tab.refs.style_combo.findData("plainnat"))
    tab.refs._insert_bibliography()
    assert got[-1] == "\\bibliographystyle{plainnat}\n\\bibliography{references}"
    tab.refs.style_combo.setCurrentIndex(0)                            # ACS Guide: native list
    tab.refs._insert_bibliography()
    assert got[-1] == "\\bibliography{references}"                  # just the marker
    tab.refs.style_combo.setCurrentIndex(tab.refs.style_combo.findData("acs"))
    tab.refs._insert_bibliography()
    assert got[-1] == "\\bibliographystyle{achemso}\n\\bibliography{references}"


def test_second_compile_reuses_unchanged_figures(win):
    from NoorSuite import latex
    if latex.find_engine() is None:
        pytest.skip("no LaTeX engine installed")
    ids, dm, tab = _doc_win(win, 2)

    def build():
        tab.compile(force=True)
        assert _wait(lambda: not tab._building and tab._pdf, 300), tab.log.toPlainText()[-600:]
        QApplication.processEvents()
        return tab.status_label.text()

    first = build()
    assert "2 new / 0 cached" in first, first
    tab._pdf = ""
    second = build()
    assert "0 new / 2 cached" in second, second
    # change one sheet -> only that figure is re-exported
    win.sheets[ids[0]].subplots[0].title = "changed"
    tab._pdf = ""
    third = build()
    assert "1 new / 1 cached" in third, third


def test_format_combo_ignores_the_wheel_and_the_format_is_per_document(win):
    from PyQt6.QtCore import QPoint, QPointF, Qt as _Qt
    from PyQt6.QtGui import QWheelEvent
    from NoorSuite import latex as _l
    ids, dm, tab = _doc_win(win, 1)
    combo = tab.refs.style_combo
    start = combo.currentIndex()
    assert combo.currentData() == "acs-guide"                         # ACS Guide by default
    ev = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, -120),
                     _Qt.MouseButton.NoButton, _Qt.KeyboardModifier.NoModifier,
                     _Qt.ScrollPhase.NoScrollPhase, False)
    for _ in range(8):
        QApplication.sendEvent(combo, ev)
    assert combo.currentIndex() == start                              # wheel never changes it
    assert tab.model.bib_style == ""

    # an explicit choice rewrites THIS document and is remembered with it
    tab.editor.setPlainText(tab.source().replace(
        r"\end{document}", "Text \\cite{k}.\n\\bibliographystyle{achemso}\n"
        "\\bibliography{references}\n\\end{document}"))
    tab.set_bib_style("unsrt")
    assert tab.model.bib_style == "unsrt"
    assert "\\bibliographystyle{unsrt}" in tab.source()
    assert "natbib" not in tab.source() and tab.bib_style_id() == "unsrt"
    assert combo.currentData() == "unsrt"                             # combo follows the text

    # changing the user's default (or another document) does not reformat this one
    _l.set_bib_style("alpha")
    assert tab.bib_style_id() == "unsrt"
    build_src, notes = _l.ensure_bibliography("\\begin{document}\\cite{k}\\end{document}",
                                              tab.bib_style_id())
    assert "\\bibliographystyle{unsrt}" in build_src
    # editing the text to another style is picked up (the text is the truth)
    tab.editor.setPlainText(tab.source().replace("{unsrt}", "{alpha}"))
    assert tab.bib_style_id() == "alpha" and combo.currentData() == "alpha"


def test_zotero_library_search_add_cite_and_unknown_keys_are_resolved(win, monkeypatch):
    from NoorSuite import zotero
    rec = {"key": "huang2021", "type": "article-journal", "title": "A battery cathode",
           "authors": [["Huang", "Chun"]], "editors": [], "year": "2021", "month": "",
           "container": "Adv. Energy Mater.", "container_short": "", "volume": "11",
           "issue": "1", "pages": "2002387", "doi": "10.1002/aenm.202002387", "url": "",
           "publisher": "", "place": "", "isbn": "", "zotero_key": "7LJNWAYI"}
    typed = {**rec, "key": "typedByHand2020", "title": "Typed by hand", "zotero_key": "ZZZZ9999"}
    searches = []
    monkeypatch.setattr(zotero, "rpc_search", lambda terms="", limit=None:
                        (searches.append(terms) or [rec]))
    monkeypatch.setattr(zotero, "rpc_find_keys", lambda keys: (
        [typed] if "typedByHand2020" in keys else [],
        [k for k in keys if k != "typedByHand2020"]))
    ids, dm, tab = _doc_win(win, 1)

    # 1. search the library in the panel -> results -> "Add & cite"
    tab.refs.zsearch.setText("huang")
    assert _wait(lambda: tab.refs.zlist.count() == 1, 10)
    assert searches[-1] == "huang"
    tab.refs.zlist.selectAll()
    tab.editor.moveCursor(tab.editor.textCursor().MoveOperation.End)
    tab.refs._zotero_add(True)
    QApplication.processEvents()
    assert "huang2021" in win.references
    assert "\\cite{huang2021}" in tab.source()
    assert tab.refs.list.count() == 1                                  # shows in the project tab

    # 2. a key typed by hand (or copied from Obsidian) is looked up in Zotero when building
    tab.editor.setPlainText(tab.source().replace(
        "\\end{document}", "Also \\cite{typedByHand2020}.\n\\end{document}"))
    from NoorSuite import latex
    recs = win.document_references()
    out, notes, info = latex.prepare_build_source(tab.source(), recs, "acs-guide")
    assert info["undefined"] == ["typedByHand2020"]                    # shown as ? ...
    tab._resolve_unknown_keys(info["undefined"])                       # ... then fetched
    assert _wait(lambda: "typedByHand2020" in win.references, 10)
    out, notes, info = latex.prepare_build_source(tab.source(), win.document_references(),
                                                  "acs-guide")
    assert info["undefined"] == [] and "\\textsuperscript{1,2}" not in out
    assert "\\textsuperscript{2}" in out                               # numbered by occurrence
    assert out.index("Huang, C.") < out.index("Typed by hand")


def test_thin_picker_records_are_completed_from_zotero_and_cites_stay_before_end(win, monkeypatch):
    from NoorSuite import zotero
    full = {"key": "naik2023", "type": "article-journal", "title": "Heterogeneities affect",
            "authors": [["Naik", "Kaustubh G."]], "editors": [], "year": "2023", "month": "",
            "container": "Energy Storage Materials", "container_short": "Energy Storage Materials",
            "volume": "55", "issue": "", "pages": "312-321", "doi": "10.1016/j.ensm.2022.11.055",
            "url": "", "publisher": "", "place": "", "isbn": "", "zotero_key": "376IJDEL"}
    thin = {"key": "naik2023", "type": "article-journal", "title": "Heterogeneities affect",
            "authors": [], "editors": [], "year": "", "zotero_key": "376IJDEL"}
    calls = []
    monkeypatch.setattr(zotero, "rpc_find_keys",
                        lambda keys: (calls.append(list(keys)) or [full], []))
    ids, dm, tab = _doc_win(win, 1)
    win.add_references([thin])
    assert not zotero.is_complete(win.references["naik2023"])
    tab.editor.setPlainText(tab.source().replace(
        "\\end{document}", "Cited \\cite{naik2023}.\n\\end{document}"))
    tab._complete_records(None)                                       # what the tab does on open
    assert _wait(lambda: zotero.is_complete(win.references.get("naik2023", {})), 10)
    assert win.references["naik2023"]["authors"] == [["Naik", "Kaustubh G."]]
    assert win.references["naik2023"]["key"] == "naik2023"            # the cited key is kept
    assert calls and calls[0] == ["naik2023"]

    # a citation inserted with the cursor behind \end{document} lands in front of it
    tab.editor.moveCursor(tab.editor.textCursor().MoveOperation.End)
    tab.insert_text("\\cite{naik2023}")
    src = tab.source()
    assert src.index("\\cite{naik2023}", src.index("Cited")) < src.rindex("\\end{document}")
    assert src.rstrip().endswith("\\end{document}")


def test_objects_share_the_layer_list_with_traces(win):
    from PyQt6.QtCore import Qt
    from NoorSuite.model import ShapeRef
    win.handle_incoming_ipc({"action": "append_dataframe", "name": "d",
                             "columns": {"x": [1., 2., 3.], "y": [1., 4., 9.]},
                             "column_order": ["x", "y"]})
    win.handle_incoming_ipc({"action": "add_to_sheet", "data_name": "d", "x_col": "x",
                             "y_cols": ["y"], "sheet": "__new__", "subplot_index": 0})
    sm = next(reversed(win.sheets.values()))
    win._open_sheet_tab(sm.sheet_id)
    sub = sm.subplots[0]

    win._add_shape("rect")
    win._add_shape("arrow")
    assert win.subplot_traces.count() == 3                     # trace + 2 objects
    assert [type(i).__name__ for i in sub.stack()] == ["TraceRef", "ShapeRef", "ShapeRef"]
    # the freshly added object is the selection and drives the Object inspector tab
    assert win.inspector_tabs.currentIndex() == win._SHAPE_TAB
    assert win.shape_widget._ref is sub.shapes[1]

    # select the rectangle and send it behind the trace
    win.subplot_traces.clearSelection()
    win.subplot_traces.item(1).setSelected(True)
    win._move_layers("back")
    assert isinstance(sub.stack()[0], ShapeRef) and sub.shapes[0].kind == "rect"
    assert sub.shapes[0].layer_pos == 0
    # ... and one step up again: it sits between nothing and the trace -> above the trace
    win._move_layers("up")
    assert [type(i).__name__ for i in sub.stack()] == ["TraceRef", "ShapeRef", "ShapeRef"]
    win._move_layers("front")
    assert sub.stack()[-1].kind == "rect"

    # editing through the widget re-renders and keeps the geometry in the model
    sh = sub.shapes[-1]
    win.shape_widget.set_shape(sh)
    win.shape_widget.fill_mode_combo.setCurrentIndex(1)       # linear gradient
    win.shape_widget._add_stop()
    assert sh.fill_mode == "linear" and len(sh.grad_stops) == 3
    ax = win.open_tabs[sm.sheet_id]._axes[0]
    assert len(ax.get_images()) == 1

    # toggling the tick in the list hides the object
    for i, it in enumerate(sub.stack()):
        if it is sh:
            win.subplot_traces.item(i).setCheckState(Qt.CheckState.Unchecked)
    assert sh.enabled is False
    assert len(win.open_tabs[sm.sheet_id]._axes[0].get_images()) == 0

    # remove the selected objects (shapes only), the trace stays
    win.subplot_traces.clearSelection()
    for i, it in enumerate(sub.stack()):
        if isinstance(it, ShapeRef):
            win.subplot_traces.item(i).setSelected(True)
    win._remove_selected_subplot_traces()
    assert sub.shapes == [] and len(sub.traces) == 1
