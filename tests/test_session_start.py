"""Crash / speed fixes: CLI-sized payloads, document start-up cache, lazy PDF pages, narrow panes."""
import socket
import threading
import time

import numpy as np
import pytest
from PyQt6.QtWidgets import QApplication

from NoorSuite import latex
from NoorSuite.model import thin_band, thin_indices
from NoorSuite.protocol import frame, read_frame
from tests.test_gui_features import _doc_win, _wait, app, win  # noqa: F401  (window fixtures)


def test_huge_frames_roundtrip_quickly():
    a, b = socket.socketpair()
    payload = {"action": "x", "v": np.random.rand(3_000_000)}          # 24 MB
    out = {}
    th = threading.Thread(target=lambda: out.setdefault("p", read_frame(b)))
    th.start()
    t0 = time.time()
    a.sendall(frame(payload))
    th.join(30)
    assert np.array_equal(out["p"]["v"], payload["v"]) and time.time() - t0 < 10
    a.close()
    b.close()


def test_a_failing_request_does_not_take_the_window_down(win):
    def boom(_payload):
        raise RuntimeError("kaboom")
    win._dispatch_ipc = boom
    win.handle_incoming_ipc({"action": "clear"})                          # must not raise
    assert "kaboom" in win.statusBar().currentMessage()


def test_million_point_errorbars_and_bands_are_reduced(win):
    x = np.linspace(0, 9, 400_000)
    win.handle_incoming_ipc({"action": "append_dataframe", "name": "big",
                             "columns": {"x": x, "a": np.sin(x), "e": 0.1 + 0 * x},
                             "column_order": ["x", "a", "e"]})
    t0 = time.time()
    win.handle_incoming_ipc({"action": "add_to_sheet", "data_name": "big", "x_col": "x",
                             "y_cols": ["a"], "new_sheet": True,
                             "trace_styles": {"a": {"show_errorbar": True, "yerr_col": "e",
                                                    "show_band": True}}})
    QApplication.processEvents()
    assert time.time() - t0 < 20
    ps = win.current_sheet()
    ax = ps._axes[0]
    (cont,) = ax.containers
    assert len(cont.lines[2][0].get_segments()) <= 400
    n_poly = [len(c.get_paths()[0].vertices) for c in ax.collections if hasattr(c, "get_paths")
              and c.get_paths()]
    assert max(n_poly) < 40_000


def test_thin_helpers():
    x = np.linspace(0, 1, 100_000)
    xs, lo, hi = thin_band(x, x - 1, x + 1, 1000)
    assert len(xs) == 1000 and lo.max() <= hi.min() + 2 and (lo < hi).all()
    small = np.arange(10.0)
    assert thin_band(small, small, small, 1000)[0] is small or len(thin_band(small, small, small, 1000)[0]) == 10
    idx = thin_indices(1_000_000, 400)
    assert 390 <= len(idx) <= 400 and idx[0] == 0 and idx[-1] == 999_999
    assert len(thin_indices(50, 400)) == 50


def _need_engine():
    if latex.find_engine() is None:
        pytest.skip("no LaTeX engine installed")


def test_unchanged_document_is_not_rebuilt_in_the_next_session(win):
    _need_engine()
    ids, dm, tab = _doc_win(win, 2)
    tab.compile(force=True)
    assert _wait(lambda: not tab._building and tab._pdf), tab.log.toPlainText()[-600:]
    assert tab._built_fp and (tab.workdir / "state.json").is_file()
    from NoorSuite.docview import DocumentTab
    again = DocumentTab(dm, win)                  # "next session": same document, fresh tab
    assert again._last_fp == again._fingerprint() and again.built_source == again.source()
    assert set(again._fig_fp) == set(tab._fig_fp)
    again.compile()                               # nothing changed -> nothing happens
    assert not again._building
    # a changed text invalidates the cache
    again.editor.setPlainText(again.source() + "\nmore")
    assert again._fingerprint() != again._last_fp


def test_pdf_preview_paints_only_the_pages_in_view(win):
    _need_engine()
    from NoorSuite.model import DocumentModel
    body = "\n".join(r"Page %d\newpage" % i for i in range(30))
    dm = win.create_new_document("Long", latex.article_template("T").replace(
        r"\noorsheet{sheet-id}", body), None)
    tab = win._open_document_tab(dm.doc_id)
    tab.auto_check.setChecked(False)
    win.resize(1100, 700)
    win.show()
    tab.compile(force=True)
    assert _wait(lambda: not tab._building and tab._pdf), tab.log.toPlainText()[-600:]
    for _ in range(20):
        QApplication.processEvents()
        time.sleep(0.02)
    pv = tab.preview
    painted = [l for l in pv._labels if not l.pixmap().isNull()]
    assert len(pv._labels) >= 25 and 0 < len(painted) < len(pv._labels) / 2
    pv.verticalScrollBar().setValue(pv.verticalScrollBar().maximum())
    _wait(lambda: not pv._labels[-1].pixmap().isNull(), 10)
    assert not pv._labels[-1].pixmap().isNull()


def test_panes_of_a_document_can_be_dragged_narrow(win):
    ids, dm, tab = _doc_win(win, 1)
    win.resize(900, 600)
    QApplication.processEvents()
    sp = tab._split
    total = sum(sp.sizes())
    for pane in (tab.side, tab.right_tabs, sp.widget(1)):
        assert pane.minimumWidth() <= 160
    sp.setSizes([130, total - 130 - 180, 180])
    QApplication.processEvents()
    s = sp.sizes()
    assert s[2] <= 200 and s[0] <= 150, s           # the handles actually move (was stuck at ~640 px)


def test_warmup_prepares_figures_and_thumbnails_before_a_document_is_opened(win):
    _need_engine()
    ids, dm, tab = _doc_win(win, 2)
    import shutil
    work = latex.default_workdir(dm.doc_id)
    win.close_tab(win.tab_widget.indexOf(tab))
    shutil.rmtree(work, ignore_errors=True)               # a fresh machine / session
    win.start_warmup(0)
    assert _wait(lambda: (work / "state.json").is_file()
                 and len(list((work / latex.FIGURES_DIR).glob("*.pdf"))) == 2, 120)
    _wait(lambda: win.statusBar().currentMessage().startswith("Documents are ready"), 30)
    again = win._open_document_tab(dm.doc_id)
    assert set(again._fig_fp) == set(ids) or len(again._fig_fp) == 2
    again.compile(force=True)
    assert _wait(lambda: not again._building and again._pdf, 240)
    assert "2 cached" in again.status_label.text() or "0 new" in again.status_label.text(), \
        again.status_label.text()
