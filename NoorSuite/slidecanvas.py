"""WYSIWYG-ish editor for the free objects of one Beamer slide (``\\noorobj`` lines).

:class:`SlideCanvas` paints the slide (the compiled PDF page as background) and edits objects with
the mouse: move, scale (edge / corner handles, Shift keeps the aspect), rotate (round handle above
the object, Shift snaps to 15 degrees), line end points, arrow-key nudging.  The LaTeX text stays the
source of truth: every finished gesture is reported with :attr:`SlideCanvas.committed`.

Objects that differ from the last compiled state are painted by Qt (and the old place is covered), so
a drag looks live although the PDF is only rebuilt afterwards.
"""

from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import (QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen,
                         QPixmap, QPolygonF, QRadialGradient)
from PyQt6.QtWidgets import QWidget

from . import slideobjects as so

MM_PER_PT = 25.4 / 72.27
HANDLE_PX = 9.0
ROTATE_OFFSET_PX = 26.0
MIN_MM = 2.0


def _color(hexstr, default="#000000") -> QColor:
    c = QColor("#" + hexstr.lstrip("#")) if hexstr and hexstr != "none" else QColor(default)
    return c if c.isValid() else QColor(default)


def to_mm(obj: dict) -> dict:
    """Object (cm) -> geometry in mm: cx, cy, w, h, a (degrees, visual counter-clockwise)."""
    if obj["kind"] in ("line", "arrow"):
        x1, y1, x2, y2 = (float(obj[k]) * 10 for k in ("x", "y", "x2", "y2"))
        return {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "cx": (x1 + x2) / 2, "cy": (y1 + y2) / 2,
                "w": abs(x2 - x1), "h": abs(y2 - y1), "a": 0.0}
    x, y, w, h = (float(obj[k]) * 10 for k in ("x", "y", "w", "h"))
    return {"cx": x + w / 2, "cy": y + h / 2, "w": w, "h": h, "a": float(obj.get("angle", 0))}


class SlideCanvas(QWidget):
    committed = pyqtSignal(int, dict)          # object index, edited object (cm)
    live = pyqtSignal(int, dict)               # while dragging (property panel follows)
    selected = pyqtSignal(int)                 # -1 = nothing
    openRequested = pyqtSignal(int)            # double-click
    deleteRequested = pyqtSignal(int)
    duplicateRequested = pyqtSignal(int)
    reorderRequested = pyqtSignal(int, int)    # index, +1 / -1
    pasteRequested = pyqtSignal()              # Ctrl+V
    pageClicked = pyqtSignal(float, float, bool)   # x, y in cm on the slide where no object is; double?

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 200)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.page_mm = (160.0, 100.0)
        self.objs: list = []
        self.sel = -1
        self.bg: QPixmap | None = None
        self.dirty: set = set()                # objects painted by Qt
        self.cover: list = []                  # [(cx, cy, w, h, a, bg colour)] old places to hide
        self.pixmap_for = lambda kind, key: None
        self.snap = True
        self.grid_mm = 5.0
        self._drag = None
        self._scale = 1.0
        self._off = QPointF(0, 0)
        self._pix_cache: dict = {}
        self.marker = None                     # (x, y, w, h) cm: the clicked text element
        self.hover = -1

    def set_marker(self, rect):
        self.marker = rect
        self.update()

    # ------------------------------------------------------------------ model
    def set_state(self, objs, page_mm, bg=None, dirty=None, cover=None, keep_selection=True):
        sel = self.sel if keep_selection else -1
        self.objs = [dict(o) for o in objs]
        self.page_mm = page_mm
        self.bg = bg
        self.dirty = set(range(len(self.objs))) if dirty is None else set(dirty)
        self.cover = cover or []
        self.sel = sel if 0 <= sel < len(self.objs) else -1
        self._pix_cache.clear()
        self.update()

    def select(self, index: int):
        if index != self.sel:
            self.sel = index if 0 <= index < len(self.objs) else -1
            self.update()
            self.selected.emit(self.sel)

    # ------------------------------------------------------------------ geometry
    def _layout(self):
        pw, ph = self.page_mm
        m = 12.0
        s = min((self.width() - 2 * m) / pw, (self.height() - 2 * m) / ph)
        self._scale = max(0.5, s)
        self._off = QPointF((self.width() - pw * self._scale) / 2,
                            (self.height() - ph * self._scale) / 2)

    def to_px(self, x, y) -> QPointF:
        return QPointF(self._off.x() + x * self._scale, self._off.y() + y * self._scale)

    def to_mm_pt(self, p) -> tuple:
        return ((p.x() - self._off.x()) / self._scale, (p.y() - self._off.y()) / self._scale)

    @staticmethod
    def _local(g, x, y):
        a = math.radians(g["a"])
        dx, dy = x - g["cx"], y - g["cy"]
        return dx * math.cos(a) - dy * math.sin(a), dx * math.sin(a) + dy * math.cos(a)

    @staticmethod
    def _global(g, lx, ly):
        a = math.radians(g["a"])
        return (g["cx"] + lx * math.cos(a) + ly * math.sin(a),
                g["cy"] - lx * math.sin(a) + ly * math.cos(a))

    # ------------------------------------------------------------------ painting
    def paintEvent(self, _e):
        self._layout()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("#2b2e36"))
        pw, ph = self.page_mm
        page = QRectF(self._off.x(), self._off.y(), pw * self._scale, ph * self._scale)
        p.fillRect(page, QColor("white"))
        if self.bg is not None and not self.bg.isNull():
            p.drawPixmap(page.toRect(), self.bg)
        for cx, cy, w, h, a, col in self.cover:
            p.save()
            c = self.to_px(cx, cy)
            p.translate(c)
            p.rotate(-a)
            p.fillRect(QRectF(-w * self._scale / 2 - 2, -h * self._scale / 2 - 2,
                              w * self._scale + 4, h * self._scale + 4), col)
            p.restore()
        for i, obj in enumerate(self.objs):
            if i in self.dirty:
                self._paint_object(p, obj)
        if 0 <= self.hover < len(self.objs) and self.hover != self.sel and self._drag is None:
            self._paint_hover(p, self.objs[self.hover])
        if self.marker is not None:                      # the clicked text element of the slide
            x, y, w, h = self.marker
            a = self.to_px(x * 10, y * 10)
            p.setPen(QPen(QColor("#2d8cff"), 1.6, Qt.PenStyle.DashLine))
            p.setBrush(QColor(45, 140, 255, 40))
            p.drawRect(QRectF(a.x() - 3, a.y() - 2, w * 10 * self._scale + 6, h * 10 * self._scale + 4))
        if 0 <= self.sel < len(self.objs):
            self._paint_handles(p, self.objs[self.sel])
        p.end()

    def _paint_hover(self, p: QPainter, obj: dict):
        """Thin outline of the object under the mouse: shows what a click would select."""
        g = to_mm(obj)
        p.save()
        p.setPen(QPen(QColor("#2d8cff"), 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        if obj["kind"] in ("line", "arrow"):
            p.drawLine(self.to_px(g["x1"], g["y1"]), self.to_px(g["x2"], g["y2"]))
        else:
            c = self.to_px(g["cx"], g["cy"])
            p.translate(c)
            p.rotate(-g["a"])
            p.drawRect(QRectF(-g["w"] * self._scale / 2, -g["h"] * self._scale / 2,
                              g["w"] * self._scale, g["h"] * self._scale))
        p.restore()

    def _paint_object(self, p: QPainter, obj: dict):
        g, k = to_mm(obj), obj["kind"]
        s = self._scale
        if k.startswith("ph-"):                      # master placeholder: dashed labelled box
            c = self.to_px(g["cx"], g["cy"])
            r = QRectF(c.x() - g["w"] * s / 2, c.y() - g["h"] * s / 2, g["w"] * s, g["h"] * s)
            p.save()
            p.setPen(QPen(QColor("#e67e22"), 1.6, Qt.PenStyle.DashLine))
            p.setBrush(QColor(230, 126, 34, 34))
            p.drawRect(r)
            f = QFont()
            f.setPixelSize(11)
            f.setBold(True)
            p.setFont(f)
            p.setPen(QColor("#c0630f"))
            p.drawText(r.adjusted(4, 2, -4, -2), int(Qt.AlignmentFlag.AlignLeft
                                                     | Qt.AlignmentFlag.AlignTop), obj.get("arg", ""))
            p.restore()
            return
        pen = QPen(_color(obj.get("line", "000000")), max(0.5, float(obj.get("lw", 1)) * MM_PER_PT * s))
        none_line = obj.get("line") == "none"
        dash = obj.get("dash", "solid")
        if dash == "dashed":
            pen.setStyle(Qt.PenStyle.DashLine)
        elif dash == "dotted":
            pen.setStyle(Qt.PenStyle.DotLine)
        p.save()
        p.setOpacity(max(0.0, min(1.0, float(obj.get("opacity", 1)))))
        if k in ("line", "arrow"):
            a, b = self.to_px(g["x1"], g["y1"]), self.to_px(g["x2"], g["y2"])
            p.setPen(pen)
            p.drawLine(a, b)
            if k == "arrow":
                ang = math.atan2(b.y() - a.y(), b.x() - a.x())
                n = max(8.0, 3.0 * s)
                tip = QPolygonF([b, QPointF(b.x() - n * math.cos(ang - 0.4), b.y() - n * math.sin(ang - 0.4)),
                                 QPointF(b.x() - n * math.cos(ang + 0.4), b.y() - n * math.sin(ang + 0.4))])
                p.setBrush(QBrush(pen.color()))
                p.drawPolygon(tip)
            p.restore()
            return
        c = self.to_px(g["cx"], g["cy"])
        p.translate(c)
        p.rotate(-g["a"])
        w, h = g["w"] * s, g["h"] * s
        r = QRectF(-w / 2, -h / 2, w, h)
        if k in ("sheet", "file"):
            pm = self._pixmap(k, obj.get("arg", ""))
            if pm is not None and not pm.isNull():
                p.drawPixmap(r.toRect(), pm)
            else:
                p.setPen(QPen(QColor("#888"), 1, Qt.PenStyle.DashLine))
                p.setBrush(QColor(200, 200, 200, 80))
                p.drawRect(r)
                p.drawText(r, Qt.AlignmentFlag.AlignCenter, f"{k}: {obj.get('arg', '')}")
            p.restore()
            return
        path = QPainterPath()
        if k == "ellipse":
            path.addEllipse(r)
        else:
            path.addRect(r)
        fill = obj.get("fill", "none")
        if fill != "none":
            c1 = _color(fill)
            fill2 = obj.get("fill2", "")
            if fill2:
                c2 = _color(fill2)
                grad = obj.get("grad", "vertical")
                if grad == "radial":
                    gr = QRadialGradient(QPointF(0, 0), max(w, h) / 2)
                    gr.setColorAt(0, c1)
                    gr.setColorAt(1, c2)
                elif grad == "horizontal":
                    gr = QLinearGradient(r.left(), 0, r.right(), 0)
                    gr.setColorAt(0, c1)
                    gr.setColorAt(1, c2)
                else:
                    gr = QLinearGradient(0, r.top(), 0, r.bottom())
                    gr.setColorAt(0, c1)
                    gr.setColorAt(1, c2)
                p.setBrush(QBrush(gr))
            else:
                p.setBrush(QBrush(c1))
        else:
            p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(Qt.PenStyle.NoPen if none_line else pen)
        p.drawPath(path)
        text = obj.get("arg", "")
        if text.strip():
            from .pptxexport import plain
            font = QFont()
            font.setPixelSize(max(6, int(float(obj.get("size", 18)) * MM_PER_PT * s)))
            p.setFont(font)
            p.setPen(_color(obj.get("color", "000000")))
            al = {"center": Qt.AlignmentFlag.AlignHCenter, "right": Qt.AlignmentFlag.AlignRight
                  }.get(obj.get("align", "center"), Qt.AlignmentFlag.AlignLeft)
            v = Qt.AlignmentFlag.AlignVCenter if k != "text" else Qt.AlignmentFlag.AlignVCenter
            p.drawText(r.adjusted(3, 3, -3, -3), int(al | v | Qt.TextFlag.TextWordWrap), plain(text))
        p.restore()

    def _pixmap(self, kind, key):
        ck = (kind, key)
        if ck not in self._pix_cache:
            self._pix_cache[ck] = self.pixmap_for(kind, key)
        return self._pix_cache[ck]

    # ------------------------------------------------------------------ handles
    def _handles(self, obj):
        """[(name, QPointF px)] for the selected object."""
        g = to_mm(obj)
        if obj["kind"] in ("line", "arrow"):
            return [("p1", self.to_px(g["x1"], g["y1"])), ("p2", self.to_px(g["x2"], g["y2"]))]
        out = []
        for hx in (-1, 0, 1):
            for hy in (-1, 0, 1):
                if hx == 0 and hy == 0:
                    continue
                x, y = self._global(g, hx * g["w"] / 2, hy * g["h"] / 2)
                out.append((f"s{hx}{hy}", self.to_px(x, y)))
        if obj["kind"].startswith("ph-"):
            return out                                # placeholders: move / resize only
        # rotation handle: above the top edge, in the rotated frame
        off = ROTATE_OFFSET_PX / self._scale
        x, y = self._global(g, 0, -g["h"] / 2 - off)
        out.append(("rot", self.to_px(x, y)))
        return out

    def _paint_handles(self, p, obj):
        g = to_mm(obj)
        p.save()
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor("#3b82f6"), 1.2, Qt.PenStyle.DashLine))
        if obj["kind"] not in ("line", "arrow"):
            c = self.to_px(g["cx"], g["cy"])
            p.translate(c)
            p.rotate(-g["a"])
            w, h = g["w"] * self._scale, g["h"] * self._scale
            p.drawRect(QRectF(-w / 2, -h / 2, w, h))
            p.restore()
            p.save()
        else:
            p.drawLine(self.to_px(g["x1"], g["y1"]), self.to_px(g["x2"], g["y2"]))
        for name, pt in self._handles(obj):
            if name == "rot":
                p.setPen(QPen(QColor("#3b82f6"), 1))
                p.setBrush(QColor("#ffffff"))
                p.drawEllipse(pt, HANDLE_PX / 2 + 1, HANDLE_PX / 2 + 1)
            else:
                p.setPen(QPen(QColor("#3b82f6"), 1))
                p.setBrush(QColor("#ffffff"))
                p.drawRect(QRectF(pt.x() - HANDLE_PX / 2, pt.y() - HANDLE_PX / 2, HANDLE_PX, HANDLE_PX))
        p.restore()

    # ------------------------------------------------------------------ hit testing
    def _hit_handle(self, pos):
        if not 0 <= self.sel < len(self.objs):
            return None
        for name, pt in self._handles(self.objs[self.sel]):
            if abs(pt.x() - pos.x()) <= HANDLE_PX and abs(pt.y() - pos.y()) <= HANDLE_PX:
                return name
        return None

    def _hit_object(self, pos) -> int:
        x, y = self.to_mm_pt(pos)
        for i in range(len(self.objs) - 1, -1, -1):
            obj, g = self.objs[i], to_mm(self.objs[i])
            if obj["kind"] in ("line", "arrow"):
                if _seg_dist(x, y, g["x1"], g["y1"], g["x2"], g["y2"]) <= 3.0 / self._scale * 3:
                    return i
                continue
            lx, ly = self._local(g, x, y)
            if abs(lx) <= g["w"] / 2 + 1 and abs(ly) <= g["h"] / 2 + 1:
                return i
        return -1

    # ------------------------------------------------------------------ mouse
    def mousePressEvent(self, e):
        self.setFocus()
        if e.button() != Qt.MouseButton.LeftButton:
            return
        h = self._hit_handle(e.position())
        if h is not None:
            self._begin(h, e)
            return
        i = self._hit_object(e.position())
        self.select(i)
        if i >= 0:
            self.marker = None
            self._begin("move", e)
        else:
            x, y = self.to_mm_pt(e.position())
            if 0 <= x <= self.page_mm[0] and 0 <= y <= self.page_mm[1]:
                self.pageClicked.emit(x / 10.0, y / 10.0, False)

    def _begin(self, mode, e):
        obj = dict(self.objs[self.sel])
        self._drag = {"mode": mode, "start": dict(obj), "g": to_mm(obj),
                      "p0": self.to_mm_pt(e.position()), "moved": False}

    def mouseMoveEvent(self, e):
        if self._drag is None:
            h = self._hit_handle(e.position())
            over = self._hit_object(e.position())
            if over != self.hover:
                self.hover = over
                self.update()
            self.setCursor(Qt.CursorShape.CrossCursor if h else (
                Qt.CursorShape.SizeAllCursor if over >= 0 else Qt.CursorShape.IBeamCursor))
            return
        d = self._drag
        d["moved"] = True
        x, y = self.to_mm_pt(e.position())
        shift = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        obj = dict(d["start"])
        g = d["g"]
        mode = d["mode"]
        if mode == "move":
            dx, dy = x - d["p0"][0], y - d["p0"][1]
            if self.snap:
                nx, ny = self._snap(g["cx"] + dx, g["cy"] + dy, g)
                dx, dy = nx - g["cx"], ny - g["cy"]
            if obj["kind"] in ("line", "arrow"):
                obj["x"], obj["y"] = (g["x1"] + dx) / 10, (g["y1"] + dy) / 10
                obj["x2"], obj["y2"] = (g["x2"] + dx) / 10, (g["y2"] + dy) / 10
            else:
                obj["x"] = (g["cx"] + dx - g["w"] / 2) / 10
                obj["y"] = (g["cy"] + dy - g["h"] / 2) / 10
        elif mode in ("p1", "p2"):
            if self.snap:
                x, y = round(x / self.grid_mm) * self.grid_mm, round(y / self.grid_mm) * self.grid_mm
            k1, k2 = ("x", "y") if mode == "p1" else ("x2", "y2")
            obj[k1], obj[k2] = x / 10, y / 10
            if shift:                                     # 15 degree steps
                ox = float(obj["x2" if mode == "p1" else "x"])
                oy = float(obj["y2" if mode == "p1" else "y"])
                ang = math.atan2(y / 10 - oy, x / 10 - ox)
                ang = round(ang / (math.pi / 12)) * (math.pi / 12)
                r = math.hypot(x / 10 - ox, y / 10 - oy)
                obj[k1], obj[k2] = ox + r * math.cos(ang), oy + r * math.sin(ang)
        elif mode == "rot":
            dx, dy = x - g["cx"], y - g["cy"]
            ang = math.degrees(math.atan2(-dx, -dy))
            if shift:
                ang = round(ang / 15.0) * 15.0
            obj["angle"] = round(((ang + 180) % 360) - 180, 1)
        else:                                             # scale handle s<hx><hy>
            hx, hy = _hxy(mode)
            lx, ly = self._local(g, x, y)
            half_w, half_h = g["w"] / 2, g["h"] / 2
            left, right, top, bottom = -half_w, half_w, -half_h, half_h
            if hx > 0:
                right = max(lx, left + MIN_MM)
            elif hx < 0:
                left = min(lx, right - MIN_MM)
            if hy > 0:
                bottom = max(ly, top + MIN_MM)
            elif hy < 0:
                top = min(ly, bottom - MIN_MM)
            keep = shift or obj["kind"] in ("sheet", "file")
            if keep and hx != 0 and hy != 0 and g["w"] > 0 and g["h"] > 0:
                fw, fh = (right - left) / g["w"], (bottom - top) / g["h"]
                f = max(fw, fh)
                if hx > 0:
                    right = left + g["w"] * f
                else:
                    left = right - g["w"] * f
                if hy > 0:
                    bottom = top + g["h"] * f
                else:
                    top = bottom - g["h"] * f
            ncx, ncy = self._global(g, (left + right) / 2, (top + bottom) / 2)
            nw, nh = right - left, bottom - top
            obj["x"], obj["y"] = (ncx - nw / 2) / 10, (ncy - nh / 2) / 10
            obj["w"], obj["h"] = nw / 10, nh / 10
        self.objs[self.sel] = obj
        self.dirty.add(self.sel)
        self.update()
        self.live.emit(self.sel, obj)

    def _snap(self, cx, cy, g):
        """Snap the centre to the slide centre lines / the object's edges to the grid."""
        pw, ph = self.page_mm
        tol = 2.0
        for target in (pw / 2,):
            if abs(cx - target) < tol:
                cx = target
        for target in (ph / 2,):
            if abs(cy - target) < tol:
                cy = target
        left = cx - g["w"] / 2
        top = cy - g["h"] / 2
        cx += round(left / self.grid_mm) * self.grid_mm - left if abs(
            left - round(left / self.grid_mm) * self.grid_mm) < tol and cx != pw / 2 else 0
        cy += round(top / self.grid_mm) * self.grid_mm - top if abs(
            top - round(top / self.grid_mm) * self.grid_mm) < tol and cy != ph / 2 else 0
        return cx, cy

    def mouseReleaseEvent(self, e):
        d, self._drag = self._drag, None
        if d is not None and d["moved"] and 0 <= self.sel < len(self.objs):
            self.committed.emit(self.sel, self._rounded(self.objs[self.sel]))

    @staticmethod
    def _rounded(obj):
        out = dict(obj)
        for k in ("x", "y", "w", "h", "x2", "y2"):
            if k in out:
                out[k] = round(float(out[k]), 2)
        if "angle" in out:
            out["angle"] = round(float(out["angle"]), 1)
        return out

    def mouseDoubleClickEvent(self, e):
        i = self._hit_object(e.position())
        if i >= 0:
            self.select(i)
            self.openRequested.emit(i)
        else:
            x, y = self.to_mm_pt(e.position())
            if 0 <= x <= self.page_mm[0] and 0 <= y <= self.page_mm[1]:
                self.pageClicked.emit(x / 10.0, y / 10.0, True)

    def keyPressEvent(self, e):
        k = e.key()
        step = 0.5 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 0.1      # cm
        if k == Qt.Key.Key_V and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.pasteRequested.emit()
            return
        if self.sel < 0:
            return super().keyPressEvent(e)
        if k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.deleteRequested.emit(self.sel)
        elif k in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down):
            dx = {Qt.Key.Key_Left: -step, Qt.Key.Key_Right: step}.get(k, 0.0)
            dy = {Qt.Key.Key_Up: -step, Qt.Key.Key_Down: step}.get(k, 0.0)
            obj = dict(self.objs[self.sel])
            obj["x"], obj["y"] = float(obj["x"]) + dx, float(obj["y"]) + dy
            if obj["kind"] in ("line", "arrow"):
                obj["x2"], obj["y2"] = float(obj["x2"]) + dx, float(obj["y2"]) + dy
            self.objs[self.sel] = obj
            self.dirty.add(self.sel)
            self.update()
            self.committed.emit(self.sel, self._rounded(obj))
        elif k == Qt.Key.Key_D and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.duplicateRequested.emit(self.sel)
        elif k == Qt.Key.Key_BracketRight and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.reorderRequested.emit(self.sel, 1)
        elif k == Qt.Key.Key_BracketLeft and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.reorderRequested.emit(self.sel, -1)
        elif k == Qt.Key.Key_Escape:
            self.select(-1)
        else:
            super().keyPressEvent(e)


def _hxy(mode: str) -> tuple:
    """``s-1-1`` / ``s01`` ... -> (hx, hy)."""
    body = mode[1:]
    vals, i = [], 0
    while i < len(body):
        if body[i] == "-":
            vals.append(-int(body[i + 1]))
            i += 2
        else:
            vals.append(int(body[i]))
            i += 1
    return vals[0], vals[1]


def _seg_dist(px, py, x1, y1, x2, y2) -> float:
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(px - x1, py - y1)
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))
