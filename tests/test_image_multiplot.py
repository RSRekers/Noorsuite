"""Image multiplots: render speed, custom colormaps for images, style broadcast, default aspect."""
import time

import numpy as np
import pytest
from PyQt6.QtWidgets import QApplication

from NoorSuite import colormap
from NoorSuite.model import ColorMap, ImageObject, ImageRef, copy_image_style, make_image_ref
from tests.test_gui_features import app, win  # noqa: F401  (window fixtures)


def _sheet_with_images(win, n=4, size=384):
    rng = np.random.default_rng(0)
    sm = win._active_sheet_model()
    sm.rows, sm.cols = 2, 2
    win.rows_spin.setValue(2)
    win.cols_spin.setValue(2)
    ids = []
    for i in range(n):
        obj = ImageObject(f"im{i}", rng.random((3, size, size)), ["z", "y", "x"])
        win.images[obj.id] = obj
        ids.append(obj.id)
    for i, oid in enumerate(ids):
        win._add_image_to_subplot(oid, sm.sheet_id, i, [1, 2])
    return sm, ids


def test_plain_images_default_to_equal_aspect_and_automatic_ticks(win):
    obj = ImageObject("im", np.zeros((2, 5, 5)), ["z", "y", "x"])
    assert make_image_ref(obj, [1, 2]).aspect == "equal"
    sm, _ = _sheet_with_images(win, 1)
    ps = win.current_sheet()
    ps.render(win.repository, win.images)
    ax = ps._axes[0]
    assert ax.get_aspect() == 1.0 or ax.get_aspect() == "equal"
    assert len(ax.get_xticks()) < 30 and len(ax.get_yticks()) < 30      # not one tick per pixel


def test_four_images_render_fast(win):
    sm, _ = _sheet_with_images(win, 4, 512)
    ps = win.current_sheet()
    t0 = time.time()
    ps.render(win.repository, win.images)
    assert time.time() - t0 < 6.0, "image multiplots must not be slow again"


def test_custom_colormap_colours_an_image(win):
    sm, ids = _sheet_with_images(win, 1, 16)
    sm.colormaps.append(ColorMap("mine", ["#000000", "#ff0000"]))
    sub = sm.subplots[0]
    sub.image.cmap = "mine"
    sub.image.vmin, sub.image.vmax = 0.0, 1.0
    win.sync_active_subplot_inspector()
    names = [win.axes_widget.image_widget.cmap_combo.itemText(i)
             for i in range(win.axes_widget.image_widget.cmap_combo.count())]
    assert names[0] == "mine" and "viridis" in names
    ps = win.current_sheet()
    ps.render(win.repository, win.images)
    cmap = ps._axes[0].images[0].get_cmap()
    assert tuple(np.round(cmap(1.0)[:3], 2)) == (1.0, 0.0, 0.0)
    assert tuple(np.round(cmap(0.0)[:3], 2)) == (0.0, 0.0, 0.0)
    fp1 = win.sheet_fingerprint(sm.sheet_id)
    sm.colormaps[0].colors = ["#000000", "#00ff00"]
    assert win.sheet_fingerprint(sm.sheet_id) != fp1                    # editing the map redraws
    colormap.set_library([])


def test_image_style_can_be_applied_to_all_images(win):
    sm, _ = _sheet_with_images(win, 3, 16)
    sm.active_index = 0
    win.sync_active_subplot_inspector()
    assert win.image_broadcast_btn.isEnabled()
    src = sm.subplots[0].image
    src.cmap, src.colorbar, src.interpolation, src.vmax = "magma", True, "bilinear", 0.5
    copy_image_style(src, sm.subplots[1].image)
    assert (sm.subplots[1].image.cmap, sm.subplots[1].image.colorbar) == ("magma", True)
    assert sm.subplots[1].image.vmax is None                            # data range stays
    sm.subplots[2].image.cmap = "gray"
    win._apply_image_style_to_all_images()
    assert [s.image.cmap for s in sm.subplots[:3]] == ["magma"] * 3
    sm.subplots[1].image = None
    sm.subplots[2].image = None
    win.sync_active_subplot_inspector()
    assert not win.image_broadcast_btn.isEnabled()                      # nothing to copy to
