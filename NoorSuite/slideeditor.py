"""Slide editor panel of the document tab: frame navigator, object toolbar, canvas, properties.

The objects live in the LaTeX text (``\\noorobj`` lines, see :mod:`NoorSuite.slideobjects`); this panel
only reads and rewrites those lines, so the text editor, undo and the PDF build keep working as usual.
"""

from __future__ import annotations

import re

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPixmap
from PyQt6.QtPdf import QPdfDocument
from PyQt6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox,
                             QDoubleSpinBox, QFormLayout, QHBoxLayout, QInputDialog, QLabel,
                             QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
                             QToolButton, QVBoxLayout, QWidget)

from . import slideobjects as so
from .slidecanvas import SlideCanvas

_TITLE_RE = re.compile(r"\\begin\{frame\}\s*(?:<[^>]*>)?\s*(?:\[[^\]]*\])?\s*\{([^{}]*)\}")


def frame_titles(source: str) -> list:
    out = []
    for i, (s, e) in enumerate(so.frames(source)):
        body = source[s:e]
        m = _TITLE_RE.match(body)
        if m and m.group(1).strip():
            out.append(m.group(1).strip())
        elif "\\titlepage" in body:
            out.append("Title slide")
        else:
            ft = re.search(r"\\frametitle\{([^{}]*)\}", body)
            out.append(ft.group(1) if ft else f"Slide {i + 1}")
    return out


class ColorButton(QPushButton):
    changed = pyqtSignal(str)

    def __init__(self, parent=None, allow_none=True):
        super().__init__(parent)
        self._hex = "000000"
        self._none = False
        self.setFixedWidth(64)
        self.clicked.connect(self._pick)
        self._allow_none = allow_none

    def set_value(self, hexstr: str):
        self._none = hexstr in ("none", "")
        self._hex = "000000" if self._none else hexstr.lstrip("#")
        self._paint()

    def value(self) -> str:
        return "none" if self._none else self._hex

    def _paint(self):
        self.setText("none" if self._none else "")
        bg = "transparent" if self._none else f"#{self._hex}"
        self.setStyleSheet(f"background:{bg}; border:1px solid #888;")

    def _pick(self):
        col = QColorDialog.getColor(QColor("#" + self._hex), self, "Colour")
        if col.isValid():
            self._none = False
            self._hex = col.name()[1:].upper()
            self._paint()
            self.changed.emit(self._hex)

    def contextMenuEvent(self, e):
        if self._allow_none:
            self._none = True
            self._paint()
            self.changed.emit("none")


class SlideSetupDialog(QDialog):
    """Per-document slide defaults (``\\noorslide{...}``): title, body and number placement."""

    FIELDS = [("title-dx", "Title: shift right (cm)"), ("title-dy", "Title: shift down (cm)"),
              ("title-size", "Title: font size (pt)"), ("body-top", "Body: extra space above (cm)"),
              ("margin-left", "Text margin left (cm)"), ("margin-right", "Text margin right (cm)"),
              ("number-x", "Title slide number: x from left (cm)"),
              ("number-y", "Title slide number: y from top (cm)"),
              ("number-size", "Title slide number: size (pt)")]

    def __init__(self, values: dict, page_cm=(16.0, 10.0), parent=None):
        super().__init__(parent)
        self.setWindowTitle("Slide setup")
        form = QFormLayout(self)
        self.number_check = QCheckBox("Show the slide number on the title slide (it counts as 1)")
        self.number_check.setChecked(str(values.get("number-on-title", "")).lower() == "true")
        form.addRow(self.number_check)
        self.edits = {}
        for key, label in self.FIELDS:
            e = QLineEdit(str(values.get(key, "")))
            e.setPlaceholderText("theme default")
            self.edits[key] = e
            form.addRow(label, e)
        note = QLabel(f"Slide size {page_cm[0]:g} x {page_cm[1]:g} cm. Empty fields keep the "
                      "theme's own value. The numbers are written as one \\noorslide{...} line "
                      "in the preamble.")
        note.setWordWrap(True)
        form.addRow(note)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                               | QDialogButtonBox.StandardButton.Cancel)
        reset = box.addButton("Reset all", QDialogButtonBox.ButtonRole.ResetRole)
        reset.clicked.connect(self._reset)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        form.addRow(box)

    def _reset(self):
        for e in self.edits.values():
            e.clear()
        self.number_check.setChecked(False)

    def values(self) -> dict:
        out = {k: e.text().strip() for k, e in self.edits.items()}
        out["number-on-title"] = "true" if self.number_check.isChecked() else ""
        return out


class SlideEditor(QWidget):
    """``tab`` = the owning DocumentTab (``source()``, ``apply_source``, ``host``, ``built_source``,
    ``_pdf``, ``_open_ref``)."""

    def __init__(self, tab, parent=None):
        super().__init__(parent)
        self.tab = tab
        self.frame = 0
        self._loading = False
        self._doc = QPdfDocument(self)
        self._doc_path = ""
        self.canvas = SlideCanvas()
        self.canvas.pixmap_for = self._pixmap_for
        self.canvas.committed.connect(self._commit)
        self.canvas.live.connect(self._show_props)
        self.canvas.selected.connect(self._on_selected)
        self.canvas.openRequested.connect(self._open)
        self.canvas.deleteRequested.connect(self.delete_object)
        self.canvas.duplicateRequested.connect(self.duplicate_object)
        self.canvas.reorderRequested.connect(self.reorder_object)

        self.frame_combo = QComboBox()
        self.frame_combo.currentIndexChanged.connect(self._frame_chosen)
        self._detected = None                  # layout measured in the compiled PDF
        prev_b, next_b = QToolButton(), QToolButton()
        prev_b.setText("\u25C0")
        next_b.setText("\u25B6")
        prev_b.clicked.connect(lambda: self.set_frame(self.frame - 1))
        next_b.clicked.connect(lambda: self.set_frame(self.frame + 1))
        top = QHBoxLayout()
        top.addWidget(prev_b)
        top.addWidget(self.frame_combo, 1)
        top.addWidget(next_b)
        tools = QHBoxLayout()
        for label, kind in (("Rectangle", "rect"), ("Ellipse", "ellipse"), ("Line", "line"),
                            ("Arrow", "arrow"), ("Text", "text")):
            b = QPushButton(label)
            b.clicked.connect(lambda _c=False, k=kind: self.add_object(k))
            tools.addWidget(b)
        gb = QPushButton("Graph...")
        gb.setToolTip("Place a NoorSuite graph on the slide (double-click it to edit the graph)")
        gb.clicked.connect(self.add_graph)
        fb = QPushButton("SVG / picture...")
        fb.setToolTip("Place an SVG / PDF / PNG / JPG (double-click: edit in Inkscape)")
        fb.clicked.connect(self.add_file)
        tools.addWidget(gb)
        tools.addWidget(fb)
        tools.addStretch(1)
        self.snap_check = QCheckBox("Snap")
        self.snap_check.setChecked(True)
        self.snap_check.toggled.connect(lambda on: setattr(self.canvas, "snap", on))
        tools.addWidget(self.snap_check)
        setup_b = QPushButton("Slide setup...")
        setup_b.clicked.connect(self.slide_setup)
        tools.addWidget(setup_b)

        self._build_props()
        body = QHBoxLayout()
        body.addWidget(self.canvas, 1)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setWidget(self.props)
        sc.setFixedWidth(250)
        body.addWidget(sc)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addLayout(top)
        lay.addLayout(tools)
        lay.addLayout(body, 1)
        self.hint = QLabel("Drag to move, handles to scale, round handle to rotate (Shift: aspect / "
                           "15\u00B0 steps), arrow keys nudge, Del removes, Ctrl+D duplicates, "
                           "Ctrl+[ / ] changes the stacking order. Double-click opens graphs and "
                           "SVGs for editing.")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color:#777; font-size:11px;")
        lay.addWidget(self.hint)
        self._text_timer = QTimer(self)
        self._text_timer.setSingleShot(True)
        self._text_timer.timeout.connect(self._text_changed)

    # ---------------------------------------------------------------- properties
    def _spin(self, lo=-100.0, hi=200.0, step=0.1):
        s = QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setDecimals(2)
        s.setSingleStep(step)
        s.setKeyboardTracking(False)        # commit on Enter / focus-out, not on every digit
        s.valueChanged.connect(self._props_edited)
        return s

    def _build_props(self):
        self.props = QWidget()
        f = QFormLayout(self.props)
        self.kind_label = QLabel("")
        f.addRow("Object", self.kind_label)
        self.sp = {k: self._spin() for k in ("x", "y", "w", "h", "x2", "y2")}
        self.sp["angle"] = self._spin(-360, 360, 1.0)
        self.sp["lw"] = self._spin(0, 40, 0.5)
        self.sp["opacity"] = self._spin(0, 1, 0.1)
        self.sp["size"] = self._spin(4, 200, 1.0)
        labels = {"x": "x (cm)", "y": "y (cm)", "w": "width (cm)", "h": "height (cm)",
                  "x2": "x end (cm)", "y2": "y end (cm)", "angle": "rotation (\u00B0)",
                  "lw": "line width (pt)", "opacity": "opacity", "size": "font size (pt)"}
        self.rows = {}
        for k in ("x", "y", "w", "h", "x2", "y2", "angle"):
            f.addRow(labels[k], self.sp[k])
            self.rows[k] = f.labelForField(self.sp[k])
        self.fill_btn = ColorButton()
        self.fill2_btn = ColorButton()
        self.line_btn = ColorButton()
        self.color_btn = ColorButton(allow_none=False)
        for b in (self.fill_btn, self.fill2_btn, self.line_btn, self.color_btn):
            b.changed.connect(self._props_edited)
        self.grad_combo = QComboBox()
        self.grad_combo.addItems(["vertical", "horizontal", "radial"])
        self.grad_combo.currentIndexChanged.connect(self._props_edited)
        self.align_combo = QComboBox()
        self.align_combo.addItems(["left", "center", "right"])
        self.align_combo.currentIndexChanged.connect(self._props_edited)
        self.dash_combo = QComboBox()
        self.dash_combo.addItems(["solid", "dashed", "dotted"])
        self.dash_combo.currentIndexChanged.connect(self._props_edited)
        self.text_edit = QPlainTextEdit()
        self.text_edit.setFixedHeight(70)
        self.text_edit.textChanged.connect(lambda: self._text_timer.start(500))
        f.addRow("fill  (right-click: none)", self.fill_btn)
        f.addRow("second colour (gradient)", self.fill2_btn)
        f.addRow("gradient", self.grad_combo)
        f.addRow("line  (right-click: none)", self.line_btn)
        f.addRow(labels["lw"], self.sp["lw"])
        f.addRow("line style", self.dash_combo)
        f.addRow(labels["opacity"], self.sp["opacity"])
        f.addRow("text colour", self.color_btn)
        f.addRow(labels["size"], self.sp["size"])
        f.addRow("alignment", self.align_combo)
        f.addRow("text / label", self.text_edit)
        self.edit_btn = QPushButton("Open / edit...")
        self.edit_btn.clicked.connect(lambda: self._open(self.canvas.sel))
        f.addRow(self.edit_btn)
        up, down, dup, rm = (QPushButton(t) for t in ("Bring forward", "Send back", "Duplicate",
                                                      "Delete"))
        up.clicked.connect(lambda: self.reorder_object(self.canvas.sel, 1))
        down.clicked.connect(lambda: self.reorder_object(self.canvas.sel, -1))
        dup.clicked.connect(lambda: self.duplicate_object(self.canvas.sel))
        rm.clicked.connect(lambda: self.delete_object(self.canvas.sel))
        for b in (up, down, dup, rm):
            f.addRow(b)
        self.props.setEnabled(False)

    @staticmethod
    def _focused(w) -> bool:
        return w.hasFocus() or (hasattr(w, "lineEdit") and w.lineEdit() is not None
                                and w.lineEdit().hasFocus())

    def _show_props(self, index, obj):
        self._loading = True
        try:
            k = obj["kind"]
            ph = k.startswith("ph-")
            self.kind_label.setText(self._label_for(k))
            line = k in ("line", "arrow")
            for key in ("x", "y", "w", "h", "x2", "y2", "angle", "lw", "opacity", "size"):
                if not self._focused(self.sp[key]):          # never overwrite what is being typed
                    self.sp[key].setValue(float(obj.get(key, 0)))
            for key in ("w", "h"):
                self.sp[key].setEnabled(not line)
            self.sp["angle"].setEnabled(not line and not ph)
            for key in ("x2", "y2"):
                self.sp[key].setEnabled(line)
            for wdg in (self.fill_btn, self.fill2_btn, self.grad_combo, self.line_btn,
                        self.dash_combo, self.align_combo, self.sp["lw"], self.sp["opacity"]):
                wdg.setEnabled(not ph)
            self.sp["size"].setEnabled(k in ("rect", "ellipse", "text", "ph-title", "ph-number",
                                             "ph-source"))
            self.color_btn.setEnabled(k not in ("line", "arrow", "sheet", "file", "ph-body"))
            self.fill_btn.set_value(obj.get("fill", "none"))
            self.fill2_btn.set_value(obj.get("fill2", "") or "none")
            self.line_btn.set_value(obj.get("line", "000000"))
            self.color_btn.set_value(obj.get("color", "000000"))
            self.grad_combo.setCurrentText(obj.get("grad", "vertical"))
            self.align_combo.setCurrentText(obj.get("align", "center"))
            self.dash_combo.setCurrentText(obj.get("dash", "solid"))
            if not self.text_edit.hasFocus() and self.text_edit.toPlainText() != obj.get("arg", ""):
                self.text_edit.setPlainText(
                    obj.get("arg", "") if k not in ("sheet", "file") and not ph else "")
            has_text = k in ("rect", "ellipse", "text")
            self.text_edit.setEnabled(has_text)
            self.edit_btn.setText({"sheet": "Open graph in NoorSuite", "file": "Edit in Inkscape"
                                   }.get(k, "Edit text..."))
            self.edit_btn.setVisible(k in ("sheet", "file") or has_text)
        finally:
            self._loading = False

    @staticmethod
    def _label_for(kind: str) -> str:
        if kind.startswith("ph-"):
            return "master placeholder: " + so.PH_LABELS.get(kind[3:], kind[3:])
        return kind

    def _on_selected(self, index):
        self.props.setEnabled(index >= 0)
        if index >= 0:
            self._show_props(index, self.canvas.objs[index])
        else:
            self.kind_label.setText("")

    def _collect(self) -> dict | None:
        i = self.canvas.sel
        if i < 0 or i >= len(self.canvas.objs):
            return None
        obj = dict(self.canvas.objs[i])
        for k in ("x", "y", "w", "h", "x2", "y2", "angle", "lw", "opacity", "size"):
            obj[k] = round(self.sp[k].value(), 2)
        if obj["kind"].startswith("ph-"):
            obj["color"] = self.color_btn.value() if self.color_btn.value() != "none" else ""
            return obj
        obj["fill"] = self.fill_btn.value()
        f2 = self.fill2_btn.value()
        obj["fill2"] = "" if f2 == "none" else f2
        obj["line"] = self.line_btn.value()
        obj["color"] = self.color_btn.value()
        obj["grad"] = self.grad_combo.currentText()
        obj["align"] = self.align_combo.currentText()
        obj["dash"] = self.dash_combo.currentText()
        if obj["kind"] in ("rect", "ellipse", "text"):
            obj["arg"] = self.text_edit.toPlainText()
        return obj

    def _props_edited(self, *_):
        if self._loading:
            return
        obj = self._collect()
        if obj is not None:
            self._commit(self.canvas.sel, obj)

    def _text_changed(self):
        if not self._loading:
            self._props_edited()

    # ---------------------------------------------------------------- source edits
    def _objects(self, source=None) -> list:
        return so.objects_in(source if source is not None else self.tab.source(), self.frame)

    def _commit(self, index, obj):
        src = self.tab.source()
        if obj["kind"].startswith("ph-"):
            phs = [dict(o) for o in self.canvas.objs if o["kind"].startswith("ph-")]
            for i, o in enumerate(phs):
                if o["kind"] == obj["kind"]:
                    phs[i] = obj
            self.tab.apply_source(so.write_placeholders(src, phs))
            self.refresh()
            return
        items = so.objects_in(src, self.frame)
        if not 0 <= index < len(items):
            return
        self.tab.apply_source(so.replace_object(src, self.frame, index, obj))
        self.refresh()

    def add_object(self, kind: str, **over):
        pw, ph = (v / 10 for v in self._page_mm())
        base = {"kind": kind, "x": (pw - 4) / 2, "y": (ph - 2.2) / 2, "w": 4.0, "h": 2.2,
                "arg": ""}
        presets = {
            "rect": {"fill": "DCE6F2", "line": "2F4B66", "lw": 1.5},
            "ellipse": {"fill": "FDEBD3", "line": "C8553D", "lw": 1.5, "h": 2.4},
            "line": {"x": pw / 2 - 2, "y": ph / 2, "x2": pw / 2 + 2, "y2": ph / 2, "lw": 2.0},
            "arrow": {"x": pw / 2 - 2, "y": ph / 2, "x2": pw / 2 + 2, "y2": ph / 2, "lw": 2.0},
            "text": {"w": 6.0, "h": 1.2, "arg": "Text", "size": 20.0, "line": "none",
                     "align": "left"},
        }
        obj = {**base, **presets.get(kind, {}), **over}
        src = self.tab.source()
        n = len(so.objects_in(src, self.frame))
        if not so.frames(src):
            return
        self.tab.apply_source(so.add_object(src, self.frame, obj))
        self.refresh(select=n)

    def add_graph(self):
        sheets = self.tab.host.document_sheets()
        if not sheets:
            QMessageBox.information(self, "Graph", "There are no sheets in this project yet.")
            return
        names = [f"{s.name}  [{s.sheet_id}]" for s in sheets]
        name, ok = QInputDialog.getItem(self, "Place a graph", "Sheet:", names, 0, False)
        if not ok:
            return
        sm = sheets[names.index(name)]
        pm = self.tab.host.sheet_thumbnail(sm.sheet_id, 360)
        ratio = (pm.height() / pm.width()) if pm is not None and not pm.isNull() and pm.width() else 0.7
        pw, ph = (v / 10 for v in self._page_mm())
        w = min(pw * 0.55, (ph * 0.7) / ratio)
        self.add_object("sheet", w=round(w, 2), h=round(w * ratio, 2), arg=sm.sheet_id,
                        x=round((pw - w) / 2, 2), y=round((ph - w * ratio) / 2, 2))

    def add_file(self):
        assets = self.tab.host.document_assets()
        names = [f"{a.name}  [{a.ext}]" for a in assets] + ["Import a file..."]
        name, ok = QInputDialog.getItem(self, "Place an SVG / picture", "Figure file:", names,
                                        len(names) - 1 if not assets else 0, False)
        if not ok:
            return
        if name == "Import a file...":
            made = self.tab.host.import_assets_dialog()
            if not made:
                return
            a = made[0]
        else:
            a = assets[names.index(name)]
        pm = self.tab.host.asset_thumbnail(a.asset_id, 360)
        ratio = (pm.height() / pm.width()) if pm is not None and not pm.isNull() and pm.width() else 0.7
        pw, ph = (v / 10 for v in self._page_mm())
        w = min(pw * 0.4, (ph * 0.6) / ratio)
        self.add_object("file", w=round(w, 2), h=round(w * ratio, 2), arg=a.asset_id,
                        x=round((pw - w) / 2, 2), y=round((ph - w * ratio) / 2, 2))

    def delete_object(self, index):
        src = self.tab.source()
        if 0 <= index < len(so.objects_in(src, self.frame)) and not \
                self.canvas.objs[index]["kind"].startswith("ph-"):
            self.tab.apply_source(so.delete_object(src, self.frame, index))
            self.canvas.sel = -1
            self.refresh(select=-1)

    def duplicate_object(self, index):
        src = self.tab.source()
        items = so.objects_in(src, self.frame)
        if not 0 <= index < len(items) or self.canvas.objs[index]["kind"].startswith("ph-"):
            return
        obj = dict(items[index])
        obj.pop("span", None)
        for k in ("x", "x2"):
            if k in obj:
                obj[k] = float(obj[k]) + 0.5
        for k in ("y", "y2"):
            if k in obj:
                obj[k] = float(obj[k]) + 0.5
        self.tab.apply_source(so.add_object(src, self.frame, obj))
        self.refresh(select=len(items))

    def reorder_object(self, index, delta):
        src = self.tab.source()
        n = len(so.objects_in(src, self.frame))
        if 0 <= index < n and 0 <= index + delta < n and not \
                self.canvas.objs[index]["kind"].startswith("ph-"):
            self.tab.apply_source(so.move_object(src, self.frame, index, delta))
            self.refresh(select=index + delta)

    def _open(self, index):
        if not 0 <= index < len(self.canvas.objs):
            return
        obj = self.canvas.objs[index]
        k = obj["kind"]
        if k == "sheet":
            self.tab._open_ref(obj["arg"])
        elif k == "file":
            self.tab._open_ref("asset:" + obj["arg"])
        elif k in ("rect", "ellipse", "text"):
            text, ok = QInputDialog.getMultiLineText(self, "Text", "Text (LaTeX allowed):",
                                                     obj.get("arg", ""))
            if ok:
                new = dict(obj)
                new["arg"] = text
                self._commit(index, new)

    def slide_setup(self):
        src = self.tab.source()
        pw, ph = (v / 10 for v in self._page_mm())
        dlg = SlideSetupDialog(so.read_setup(src), (pw, ph), self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.tab.apply_source(so.write_setup(src, dlg.values()))
            self.refresh()

    # ---------------------------------------------------------------- display
    def _page_mm(self) -> tuple:
        if self._doc.status() == QPdfDocument.Status.Ready and self._doc.pageCount():
            pts = self._doc.pagePointSize(0)
            return (pts.width() * 25.4 / 72.0, pts.height() * 25.4 / 72.0)
        return so.slide_size_mm(self.tab.source())

    def _pixmap_for(self, kind, key):
        host = self.tab.host
        try:
            if kind == "sheet":
                sm = next((s for s in host.document_sheets()
                           if s.sheet_id == key or s.name == key), None)
                return host.sheet_thumbnail(sm.sheet_id, 480) if sm else None
            return host.asset_thumbnail(key, 480)
        except Exception:
            return None

    def set_frame(self, i: int):
        """``i == -1``: the master layout (placeholders + objects drawn on every slide)."""
        n = len(so.frames(self.tab.source()))
        if n or i == so.MASTER:
            self.frame = so.MASTER if i == so.MASTER else max(0, min(i, n - 1))
            self.canvas.sel = -1
            self.refresh(select=-1)

    def _frame_chosen(self, i):
        if self._loading or i < 0:
            return
        want = i - 1                           # item 0 is the master layout
        if want != self.frame:
            self.set_frame(want)

    def follow_cursor(self):
        if self.frame == so.MASTER:
            return
        pos = self.tab.editor.textCursor().position()
        i = so.frame_at(self.tab.source(), pos)
        if i >= 0 and i != self.frame:
            self.set_frame(i)

    def _load_pdf(self):
        path = self.tab._pdf or ""
        if path != self._doc_path or self._doc.status() != QPdfDocument.Status.Ready:
            self._doc.close()
            if path:
                self._doc.load(path)
            self._doc_path = path

    def _sample_frame(self, built: str):
        """A content frame (with a title) to show the master on."""
        from . import pptxexport
        try:
            parsed = pptxexport.parse_presentation(built)
        except Exception:
            return None
        for i, f in enumerate(parsed["frames"]):
            if f["kind"] != "title" and f.get("title"):
                return i
        return None

    def _frame_page(self, built: str, frame=None):
        """PDF page (0-based) that shows the complete frame, or None."""
        if self._doc.status() != QPdfDocument.Status.Ready:
            return None
        from . import pptxexport
        frame = self.frame if frame is None else frame
        try:
            parsed = pptxexport.parse_presentation(built)
        except Exception:
            return None
        rows = pptxexport.page_map(parsed, self._doc.pageCount())
        pages = [p for p, r in enumerate(rows) if r[0] == frame]
        return pages[-1] if pages else None

    def _detect_layout(self, built: str) -> dict:
        """Where the theme put title / body / number / source on a sample slide, measured in the
        compiled PDF (cm): the starting point of the placeholders."""
        from . import pptxexport
        out = {}
        try:
            parsed = pptxexport.parse_presentation(built)
            fi = self._sample_frame(built)
            if fi is None:
                return out
            page = self._frame_page(built, fi)
            if page is None:
                return out
            fr = parsed["frames"][fi]
            text = self._doc.getAllText(page).text()
            k = 2.54 / 72.0

            def rect(s):
                s = s.strip()
                i = text.find(s) if s else -1
                if i < 0:
                    return None
                r = self._doc.getSelectionAtIndex(page, i, len(s)).boundingRectangle()
                return (r.x() * k, r.y() * k, r.width() * k, r.height() * k)
            pw = self._page_mm()[0] / 10
            t = rect(fr.get("title") or "")
            if t:
                out["title"] = {"x": round(t[0], 2), "y": round(t[1], 2),
                                "w": round(max(t[2] + 0.4, pw - 2 * t[0] - 1.6), 2),
                                "h": round(t[3], 2),
                                "size": float(max(10, min(48, round(t[3] / k / 1.25))))}
            first = None
            for b in fr["blocks"]:
                if b[0] == "list" and b[1]:
                    first = (b[1][0][1], 0.7)
                    break
                if b[0] == "text":
                    first = (b[1].lstrip("*"), 0.0)
                    break
            if first:
                r = rect(first[0][:24])
                if r:
                    ph = self._page_mm()[1] / 10
                    out["body"] = {"x": round(max(0.0, r[0] - first[1]), 2),
                                   "y": round(max(0.0, r[1] - 0.15), 2),
                                   "w": round(pw - 2 * max(0.0, r[0] - first[1]), 2),
                                   "h": round(ph - r[1] - 1.0, 2)}
            num = str(page + 1)
            stripped = text.rstrip()
            if stripped.endswith(num):
                i = len(stripped) - len(num)
                rr = self._doc.getSelectionAtIndex(page, i, len(num)).boundingRectangle()
                out["number"] = {"x": round(rr.x() * k, 2), "y": round(rr.y() * k, 2),
                                 "w": round(max(0.8, rr.width() * k + 0.3), 2),
                                 "h": round(rr.height() * k, 2),
                                 "size": float(max(6, min(30, round(rr.height() / 1.25)))),
                                 "color": self._contrast_color(page, rr)}
            if fr.get("source"):
                r = rect(fr["source"][:30])
                if r:
                    out["source"] = {"x": round(r[0], 2), "y": round(r[1], 2),
                                     "w": round(pw - 2 * r[0], 2), "h": round(r[3], 2),
                                     "size": float(max(6, min(20, round(r[3] / k / 1.25))))}
        except Exception:
            return out
        return out

    def _contrast_color(self, page: int, r) -> str:
        """White text on a dark patch of the page, black on a light one."""
        try:
            pts = self._doc.pagePointSize(page)
            img = self._doc.render(page, QSize(int(pts.width()), int(pts.height())))
            x = int(max(0, min(img.width() - 1, r.x() - 2)))
            y = int(max(0, min(img.height() - 1, r.y() + r.height() / 2)))
            c = img.pixelColor(x, y)
            lum = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
            return "FFFFFF" if lum < 128 else "000000"
        except Exception:
            return "000000"

    def refresh(self, select=None):
        """Re-read the source and redraw (``select``: object index to select, -1 none)."""
        src = self.tab.source()
        frs = so.frames(src)
        self._loading = True
        try:
            titles = ["\u2605 Master layout (all slides)"] + [
                f"{i + 1}: {t}" for i, t in enumerate(frame_titles(src))]
            if [self.frame_combo.itemText(i) for i in range(self.frame_combo.count())] != titles:
                self.frame_combo.clear()
                self.frame_combo.addItems(titles)
            if self.frame != so.MASTER:
                self.frame = max(0, min(self.frame, len(frs) - 1)) if frs else 0
            self.frame_combo.setCurrentIndex(self.frame + 1)
        finally:
            self._loading = False
        master = self.frame == so.MASTER
        objs = so.objects_in(src, self.frame) if (frs or master) else []
        n_real = len(objs)
        self._load_pdf()
        built = getattr(self.tab, "built_source", None)
        bg, dirty, cover = None, None, []
        same_layout = built is not None and len(so.frames(built)) == len(frs) \
            and self._doc.status() == QPdfDocument.Status.Ready
        phs = []
        if master:
            detected = self._detect_layout(built) if same_layout and not so.has_placeholders(
                src) else None
            pw, ph = (v / 10 for v in self._page_mm())
            phs = so.placeholders(src, (pw, ph), detected)
            objs = objs + phs
        if same_layout:
            page = self._frame_page(built, self._sample_frame(built) if master else None)
            if page is not None:
                pw, ph = self._page_mm()
                px = QSize(max(200, int(self.canvas.width() * 1.2)),
                           max(120, int(self.canvas.width() * 1.2 * ph / pw)))
                img = self._doc.render(page, px)
                bg = QPixmap.fromImage(img)
                old = so.objects_in(built, self.frame)
                fmt = lambda o: so.format_object(o)                              # noqa: E731
                dirty = set()
                for i, o in enumerate(objs[:n_real]):
                    if i >= len(old) or fmt(o) != fmt(old[i]):
                        dirty.add(i)
                for i, o in enumerate(old):
                    if i >= n_real or fmt(o) != fmt(objs[i]):
                        cover.append(self._cover_for(o, img))
                dirty.update(range(n_real, len(objs)))                    # placeholders
        self.canvas.set_state(objs, self._page_mm(), bg, dirty, cover,
                              keep_selection=select is None)
        if select is not None:
            self.canvas.select(select)
        if 0 <= self.canvas.sel < len(objs):
            self._show_props(self.canvas.sel, self.canvas.objs[self.canvas.sel])
            self.props.setEnabled(True)
        else:
            self.props.setEnabled(False)

    def _cover_for(self, obj, img: QImage):
        from .slidecanvas import to_mm
        g = to_mm(obj)
        pw, ph = self._page_mm()
        x = int(max(0, min(img.width() - 1, (g["cx"] - g["w"] / 2 - 2) / pw * img.width())))
        y = int(max(0, min(img.height() - 1, (g["cy"] - g["h"] / 2 - 2) / ph * img.height())))
        col = QColor(img.pixelColor(x, y))
        if col.alpha() == 0:
            col = QColor("white")
        return (g["cx"], g["cy"], g["w"], g["h"], g.get("a", 0.0), col)

    def resizeEvent(self, e):
        super().resizeEvent(e)
