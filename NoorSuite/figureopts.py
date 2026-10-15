"""Placement options of ``\\noorsheet`` / ``\\noorfile`` calls in a document (size, rotation, shift).

``find_calls`` / ``set_options`` are Qt-free; :class:`FigureTransformDialog` edits one call.
"""

from __future__ import annotations

import re

from PyQt6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
                             QLabel)

_CALL = re.compile(r"\\(noorsheet|noorfile)\s*(\[[^\]]*\])?\s*\{([^{}]*)\}")
MANAGED = ("width", "height", "scale", "angle", "xshift", "yshift")


def find_calls(source: str) -> list:
    """``[{"cmd", "key", "opts", "start", "end", "line"}]`` for every figure call (comments skipped)."""
    out = []
    pos = 0
    for ln, text in enumerate(source.split("\n")):
        code = re.split(r"(?<!\\)%", text, maxsplit=1)[0]
        for m in _CALL.finditer(code):
            out.append({"cmd": m.group(1), "key": m.group(3).strip(),
                        "opts": (m.group(2) or "")[1:-1], "start": pos + m.start(),
                        "end": pos + m.end(), "line": ln})
        pos += len(text) + 1
    return out


def split_options(opts: str) -> list:
    """Top-level ``key=value`` / flag items of an option string."""
    items, depth, cur = [], 0, ""
    for c in opts:
        if c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
        if c == "," and depth == 0:
            items.append(cur.strip())
            cur = ""
        else:
            cur += c
    if cur.strip():
        items.append(cur.strip())
    return items


def get_options(opts: str) -> dict:
    out = {}
    for it in split_options(opts):
        k, _, v = it.partition("=")
        out[k.strip()] = v.strip()
    return out


def set_options(opts: str, updates: dict) -> str:
    """Replace / remove the managed keys (value None or "" removes); others stay in order."""
    keep = [it for it in split_options(opts)
            if it.partition("=")[0].strip() not in updates]
    for k in MANAGED:
        v = updates.get(k)
        if v not in (None, ""):
            keep.append(f"{k}={v}")
    return ",".join(keep)


def replace_call(source: str, call: dict, updates: dict) -> str:
    new_opts = set_options(call["opts"], updates)
    text = f"\\{call['cmd']}" + (f"[{new_opts}]" if new_opts else "") + f"{{{call['key']}}}"
    return source[:call["start"]] + text + source[call["end"]:]


def _dim_cm(v: str):
    """``8cm`` -> 8.0, ``0.8\\textwidth`` -> ("frac", 0.8), else None."""
    m = re.fullmatch(r"(-?[\d.]+)\s*cm", v or "")
    if m:
        return float(m.group(1))
    m = re.fullmatch(r"([\d.]+)\\(?:textwidth|linewidth|columnwidth)", v or "")
    if m:
        return ("frac", float(m.group(1)))
    return None


class FigureTransformDialog(QDialog):
    """Size / rotation / shift of one figure call."""

    def __init__(self, call: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Transform {call['key']}")
        o = get_options(call["opts"])
        form = QFormLayout(self)

        def spin(lo, hi, val, step=0.1, dec=2):
            s = QDoubleSpinBox()
            s.setRange(lo, hi)
            s.setDecimals(dec)
            s.setSingleStep(step)
            s.setValue(val)
            return s
        w = _dim_cm(o.get("width"))
        self.unit = QComboBox()
        self.unit.addItems(["cm", "% of the text width"])
        if isinstance(w, tuple):
            self.unit.setCurrentIndex(1)
            wv = w[1] * 100
        else:
            wv = w or 0.0
        self.width = spin(0, 500, wv)
        self.height = spin(0, 500, _dim_cm(o.get("height")) if isinstance(
            _dim_cm(o.get("height")), float) else 0.0)
        self.scale = spin(0, 20, float(o["scale"]) if _is_num(o.get("scale")) else 0.0)
        self.angle = spin(-360, 360, float(o["angle"]) if _is_num(o.get("angle")) else 0.0, 1, 1)
        xs, ys = _dim_cm(o.get("xshift")), _dim_cm(o.get("yshift"))
        self.xshift = spin(-50, 50, xs if isinstance(xs, float) else 0.0)
        self.yshift = spin(-50, 50, ys if isinstance(ys, float) else 0.0)
        row = QFormLayout()
        form.addRow("Width (0 = natural)", self.width)
        form.addRow("  unit", self.unit)
        form.addRow("Height (0 = keep aspect)", self.height)
        form.addRow("Scale factor (0 = none)", self.scale)
        form.addRow("Rotate counter-clockwise (\u00B0)", self.angle)
        form.addRow("Shift right (cm)", self.xshift)
        form.addRow("Shift up (cm)", self.yshift)
        form.addRow(QLabel("Width and height together distort the figure; leave the height at 0 "
                           "to keep the proportions."))
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                               | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        form.addRow(box)

    def updates(self) -> dict:
        def num(v):
            return f"{v:.3g}" if v else ""
        w = ""
        if self.width.value():
            w = (f"{self.width.value():.3g}cm" if self.unit.currentIndex() == 0
                 else f"{self.width.value() / 100:.3g}\\textwidth")
        return {"width": w, "height": f"{self.height.value():.3g}cm" if self.height.value() else "",
                "scale": num(self.scale.value()), "angle": num(self.angle.value()),
                "xshift": f"{self.xshift.value():.3g}cm" if self.xshift.value() else "",
                "yshift": f"{self.yshift.value():.3g}cm" if self.yshift.value() else ""}


def _is_num(v) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False
