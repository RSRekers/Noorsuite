"""Editor behaviour for the LaTeX text: autocomplete popup with snippets, bracket / quote handling
around selections, auto-closing brackets, auto ``\\end{env}`` and picture paste.

:class:`AssistEditor` is a plain ``QPlainTextEdit``; the document tab subclasses it (figure double
click, drops) and sets :attr:`extras_provider` (cite keys, sheets, labels ...).
"""

from __future__ import annotations

import re

from PyQt6.QtCore import QMimeData, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import QListWidget, QListWidgetItem, QPlainTextEdit

from . import latexcomplete as lc

# typed on a selection: wrap it instead of replacing it
WRAP_PAIRS = {"(": ")", "[": "]", "{": "}", "<": ">", '"': '"', "'": "'", "`": "`", "$": "$",
              "*": "*", "_": "_", "~": "~", "|": "|", "=": "="}
AUTO_CLOSE = {"(": ")", "[": "]", "{": "}"}
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".svg", ".pdf", ".bmp", ".gif", ".tif", ".tiff")


class CompletionPopup(QListWidget):
    """Frameless list under the caret; it never takes the keyboard focus."""

    def __init__(self, editor):
        super().__init__(editor)
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("QListWidget { background:#ffffff; color:#000000; border:1px solid #888;"
                           " font-family:Consolas,monospace; }"
                           " QListWidget::item:selected { background:#cfe3ff; color:#000000; }")
        self.itemClicked.connect(lambda it: editor.accept_completion(it.data(Qt.ItemDataRole.UserRole)))

    def set_items(self, items):
        self.clear()
        for it in items:
            text = it.label + (f"    {it.detail}" if it.detail else "")
            w = QListWidgetItem(text)
            w.setData(Qt.ItemDataRole.UserRole, it)
            self.addItem(w)
        self.setCurrentRow(0)

    def step(self, delta: int):
        n = self.count()
        if n:
            self.setCurrentRow((self.currentRow() + delta) % n if abs(delta) == 1
                               else max(0, min(n - 1, self.currentRow() + delta)))


class AssistEditor(QPlainTextEdit):
    imagePasted = pyqtSignal(object)           # QImage from the clipboard
    filesPasted = pyqtSignal(list)             # local picture files from the clipboard

    def __init__(self, parent=None):
        super().__init__(parent)
        self.autoclose = True
        self.autocomplete = True
        self.extras_provider = None            # () -> dict for latexcomplete.complete
        self._popup = CompletionPopup(self)
        self._pop_start = 0
        self._navigated = False
        self._stops: list = []                 # QTextCursor objects of the open snippet
        self.cursorPositionChanged.connect(self._cursor_moved)

    # ------------------------------------------------------------------ popup
    def popup_visible(self) -> bool:
        return self._popup.isVisible()

    def hide_popup(self):
        self._popup.hide()
        self._navigated = False

    def _extras(self) -> dict:
        try:
            return self.extras_provider() if self.extras_provider else {}
        except Exception:
            return {}

    def update_popup(self):
        cur = self.textCursor()
        if not self.autocomplete or cur.hasSelection() or self.isReadOnly():
            return self.hide_popup()
        block = cur.block()
        col = cur.positionInBlock()
        line = block.text()[:col]
        if re.search(r"\\end\{[A-Za-z*]*$", line):
            before = self.toPlainText()[:cur.position()]       # needs the open environments
        else:
            before = line
        start, items = lc.complete(before, self._extras())
        typed = before[start:]
        if not items or (len(items) == 1 and items[0].label == typed):
            return self.hide_popup()
        self._pop_start = cur.position() - (len(before) - start)
        self._popup.set_items(items)
        self._navigated = False
        rect = self.cursorRect(cur)
        pt = self.viewport().mapToGlobal(rect.bottomLeft())
        rows = min(self._popup.count(), 9)
        self._popup.resize(max(300, min(560, self._popup.sizeHintForColumn(0) + 24)),
                           rows * max(18, self._popup.sizeHintForRow(0)) + 6)
        self._popup.move(pt)
        if not self._popup.isVisible():
            self._popup.show()
        self._popup.raise_()

    def accept_completion(self, item):
        """Replace the typed prefix by the item's snippet and walk into its first stop."""
        if item is None:
            return
        cur = self.textCursor()
        end = cur.position()
        doc = self.document()
        cur.beginEditBlock()
        cur.setPosition(self._pop_start)
        cur.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        nxt = doc.characterAt(end)
        if item.kind == "env" and nxt == "}":              # swallow the auto-closed brace
            cur.setPosition(end + 1, QTextCursor.MoveMode.KeepAnchor)
        line = cur.block().text()
        indent = line[:len(line) - len(line.lstrip())]
        text, stops = lc.expand(item.insert, indent)
        base = self._pop_start
        cur.insertText(text)
        cur.endEditBlock()
        cursors = []
        for s, e in stops:
            c = QTextCursor(doc)
            c.setPosition(base + s)
            c.setPosition(base + e, QTextCursor.MoveMode.KeepAnchor)
            cursors.append(c)
        self.hide_popup()
        first = cursors.pop(0)
        self.setTextCursor(first)
        self._stops = cursors if cursors else []
        if not first.hasSelection() and text.endswith(("{", "[")):
            QTimer.singleShot(0, self.update_popup)            # \begin{ -> offer the environments
        self.setFocus()

    def _cursor_moved(self):
        if self._stops:
            pos = self.textCursor().position()
            lo = min(c.selectionStart() for c in self._stops)
            hi = max(c.selectionEnd() for c in self._stops)
            if pos < lo - 400 or pos > hi + 400:
                self._stops = []

    def _next_stop(self, back: bool = False) -> bool:
        if not self._stops:
            return False
        c = self._stops.pop(0)
        self.setTextCursor(c)
        return True

    # ------------------------------------------------------------------ keys
    def keyPressEvent(self, e):
        key = e.key()
        mods = e.modifiers()
        if self._popup.isVisible():
            if key == Qt.Key.Key_Down:
                self._popup.step(1)
                self._navigated = True
                return
            if key == Qt.Key.Key_Up:
                self._popup.step(-1)
                self._navigated = True
                return
            if key in (Qt.Key.Key_PageDown, Qt.Key.Key_PageUp):
                self._popup.step(8 if key == Qt.Key.Key_PageDown else -8)
                self._navigated = True
                return
            if key == Qt.Key.Key_Tab or (key in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                                         and self._navigated):
                it = self._popup.currentItem() or self._popup.item(0)
                self.accept_completion(it.data(Qt.ItemDataRole.UserRole) if it else None)
                return
            if key == Qt.Key.Key_Escape:
                self.hide_popup()
                return
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.hide_popup()
        if key == Qt.Key.Key_Tab and self._stops and self._next_stop():
            return
        if key == Qt.Key.Key_Escape and self._stops:
            self._stops = []
            return
        text = e.text()
        ctrl_only = bool(mods & Qt.KeyboardModifier.ControlModifier) and not (
            mods & Qt.KeyboardModifier.AltModifier)
        if len(text) == 1 and text.isprintable() and not ctrl_only:
            if self._typed(text):
                self.update_popup()
                return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not mods & ~Qt.KeyboardModifier.KeypadModifier \
                and self._auto_end():
            return
        if key == Qt.Key.Key_Backspace and not mods and self._backspace_pair():
            self.update_popup()
            return
        super().keyPressEvent(e)
        if text and text.isprintable() or key in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
            self.update_popup()
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Home, Qt.Key.Key_End,
                     Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.hide_popup()

    def focusOutEvent(self, e):
        self.hide_popup()
        super().focusOutEvent(e)

    def mousePressEvent(self, e):
        self.hide_popup()
        self._stops = []
        super().mousePressEvent(e)

    # ------------------------------------------------------------------ typing helpers
    def _typed(self, ch: str) -> bool:
        """Selection wrapping, closer skipping and auto-closing; True when the key was handled."""
        cur = self.textCursor()
        if cur.hasSelection():
            if ch in WRAP_PAIRS:
                self.wrap_selection(ch, WRAP_PAIRS[ch])
                return True
            return False
        if not self.autoclose:
            return False
        doc = self.document()
        pos = cur.position()
        nxt = doc.characterAt(pos)
        prev = doc.characterAt(pos - 1) if pos > 0 else ""
        if ch in (")", "]", "}") and nxt == ch:
            cur.setPosition(pos + 1)
            self.setTextCursor(cur)
            return True
        if ch in AUTO_CLOSE and prev != "\\" and (not nxt.strip() or nxt in ")]}"):
            cur.beginEditBlock()
            cur.insertText(ch + AUTO_CLOSE[ch])
            cur.setPosition(pos + 1)
            cur.endEditBlock()
            self.setTextCursor(cur)
            return True
        return False

    def wrap_selection(self, open_c: str, close_c: str):
        cur = self.textCursor()
        a, b = cur.selectionStart(), cur.selectionEnd()
        inner = cur.selectedText().replace("\u2029", "\n")
        cur.beginEditBlock()
        cur.insertText(open_c + inner + close_c)
        cur.setPosition(a + len(open_c))
        cur.setPosition(a + len(open_c) + len(inner), QTextCursor.MoveMode.KeepAnchor)
        cur.endEditBlock()
        self.setTextCursor(cur)                           # inner text stays selected: type again to nest

    def _backspace_pair(self) -> bool:
        cur = self.textCursor()
        if cur.hasSelection() or not self.autoclose:
            return False
        pos = cur.position()
        doc = self.document()
        if pos > 0 and AUTO_CLOSE.get(doc.characterAt(pos - 1)) == doc.characterAt(pos):
            cur.beginEditBlock()
            cur.setPosition(pos - 1)
            cur.setPosition(pos + 1, QTextCursor.MoveMode.KeepAnchor)
            cur.removeSelectedText()
            cur.endEditBlock()
            self.setTextCursor(cur)
            return True
        return False

    def _auto_end(self) -> bool:
        """Enter after ``\\begin{env}`` that has no ``\\end{env}`` -> add it."""
        cur = self.textCursor()
        if cur.hasSelection() or not self.autocomplete:
            return False
        block = cur.block()
        text = block.text()
        if cur.positionInBlock() != len(text):
            return False
        m = re.match(r"^(\s*)\\begin\{([A-Za-z*]+)\}(?:\[[^\]]*\])?(?:\{[^{}]*\})*\s*$", text)
        if not m or m.group(2) in ("document",):
            return False
        env = m.group(2)
        full = self.toPlainText()
        if full.count("\\begin{" + env + "}") <= full.count("\\end{" + env + "}"):
            return False
        indent = m.group(1)
        pos0 = cur.position()
        cur.beginEditBlock()
        cur.insertText("\n" + indent + "  \n" + indent + "\\end{" + env + "}")
        cur.setPosition(pos0 + 1 + len(indent) + 2)
        cur.endEditBlock()
        self.setTextCursor(cur)
        return True

    # ------------------------------------------------------------------ paste
    @staticmethod
    def picture_files(md: QMimeData) -> list:
        out = []
        if md.hasUrls():
            for u in md.urls():
                if u.isLocalFile() and u.toLocalFile().lower().endswith(IMAGE_SUFFIXES):
                    out.append(u.toLocalFile())
        return out

    def canInsertFromMimeData(self, source) -> bool:
        return source.hasImage() or bool(self.picture_files(source)) or super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source):
        files = self.picture_files(source)
        if files:
            self.filesPasted.emit(files)
            return
        if source.hasImage() and not source.hasText():
            img = source.imageData()
            if img is not None:
                self.imagePasted.emit(img)
                return
        super().insertFromMimeData(source)
