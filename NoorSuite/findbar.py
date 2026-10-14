"""Find and replace for the LaTeX editor (Ctrl+F / Ctrl+H, F3 / Shift+F3, Esc).

``find_matches`` / ``expand`` are Qt-free so the search semantics can be tested directly; the
:class:`FindBar` widget sits under the editor and works on a ``QPlainTextEdit``.
"""

from __future__ import annotations

import re

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QKeySequence, QTextCursor
from PyQt6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
                             QPushButton, QTextEdit, QToolButton, QVBoxLayout, QWidget)

MAX_MARKS = 1500                      # highlighted matches (the count is always exact)


def compile_query(query: str, case: bool = False, word: bool = False, regex: bool = False):
    """Compiled pattern for the search options (raises ``re.error`` for a bad regex)."""
    pat = query if regex else re.escape(query)
    if word:
        pat = r"\b(?:" + pat + r")\b"
    return re.compile(pat, 0 if case else re.IGNORECASE | re.UNICODE)


def find_matches(text: str, query: str, case: bool = False, word: bool = False,
                 regex: bool = False) -> list:
    """All non-empty matches as ``[(start, end)]``."""
    if not query:
        return []
    return [(m.start(), m.end()) for m in compile_query(query, case, word, regex).finditer(text)
            if m.end() > m.start()]


def expand(match, replacement: str, regex: bool) -> str:
    """The replacement text for one match (``\\1`` / ``\\g<name>`` only in regex mode)."""
    if not regex:
        return replacement
    try:
        return match.expand(replacement)
    except (re.error, IndexError):
        return replacement


def replace_all_text(text: str, query: str, replacement: str, case=False, word=False,
                     regex=False):
    """-> (new text, number of replacements)."""
    if not query:
        return text, 0
    pat = compile_query(query, case, word, regex)
    n = 0

    def one(m):
        nonlocal n
        if m.end() == m.start():
            return m.group(0)
        n += 1
        return expand(m, replacement, regex)

    return pat.sub(one, text), n


def _u16(text: str):
    """Python index -> Qt (UTF-16) position; None when the two are identical."""
    if all(ord(c) < 0x10000 for c in text):
        return None
    pos, acc = [], 0
    for c in text:
        pos.append(acc)
        acc += 2 if ord(c) >= 0x10000 else 1
    pos.append(acc)
    return pos


class FindBar(QWidget):
    """Search / replace strip for a plain-text editor."""

    closed = pyqtSignal()

    def __init__(self, editor: QPlainTextEdit, parent=None):
        super().__init__(parent)
        self.editor = editor
        self._regex_ok = True
        self.find_edit = QLineEdit()
        self.find_edit.setPlaceholderText("Find")
        self.find_edit.setClearButtonEnabled(True)
        self.replace_edit = QLineEdit()
        self.replace_edit.setPlaceholderText("Replace with")
        self.count_label = QLabel("")
        self.count_label.setMinimumWidth(90)
        self.case_check = QCheckBox("Aa")
        self.case_check.setToolTip("Match case")
        self.word_check = QCheckBox("Word")
        self.word_check.setToolTip("Whole words only")
        self.regex_check = QCheckBox(".*")
        self.regex_check.setToolTip("Regular expression (\\1 in the replacement refers to a group)")
        self.prev_btn = QToolButton()
        self.prev_btn.setText("▲")
        self.prev_btn.setToolTip("Previous match (Shift+F3 / Shift+Enter)")
        self.next_btn = QToolButton()
        self.next_btn.setText("▼")
        self.next_btn.setToolTip("Next match (F3 / Enter)")
        self.close_btn = QToolButton()
        self.close_btn.setText("✕")
        self.close_btn.setToolTip("Close (Esc)")
        self.replace_btn = QPushButton("Replace")
        self.replace_all_btn = QPushButton("Replace all")
        self.toggle_btn = QToolButton()
        self.toggle_btn.setText("▶")
        self.toggle_btn.setCheckable(True)
        self.toggle_btn.setToolTip("Show replace (Ctrl+H)")

        top = QHBoxLayout()
        top.setContentsMargins(4, 2, 4, 2)
        for w in (self.toggle_btn, self.find_edit, self.prev_btn, self.next_btn, self.case_check,
                  self.word_check, self.regex_check, self.count_label, self.close_btn):
            top.addWidget(w)
        top.setStretch(1, 1)
        self._replace_row = QWidget()
        bot = QHBoxLayout(self._replace_row)
        bot.setContentsMargins(4, 0, 4, 2)
        spacer = QWidget()
        spacer.setFixedWidth(self.toggle_btn.sizeHint().width())
        bot.addWidget(spacer)
        bot.addWidget(self.replace_edit, 1)
        bot.addWidget(self.replace_btn)
        bot.addWidget(self.replace_all_btn)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addLayout(top)
        lay.addWidget(self._replace_row)
        self._replace_row.hide()
        self.hide()

        self.find_edit.textChanged.connect(lambda _t: self._refresh(jump=True))
        self.find_edit.returnPressed.connect(self._enter)
        self.replace_edit.returnPressed.connect(self.replace_current)
        for c in (self.case_check, self.word_check, self.regex_check):
            c.toggled.connect(lambda _on: self._refresh(jump=True))
        self.next_btn.clicked.connect(lambda: self.step(False))
        self.prev_btn.clicked.connect(lambda: self.step(True))
        self.close_btn.clicked.connect(self.close_bar)
        self.replace_btn.clicked.connect(self.replace_current)
        self.replace_all_btn.clicked.connect(self.replace_all)
        self.toggle_btn.toggled.connect(self._show_replace)
        editor.textChanged.connect(lambda: self.isVisible() and self._refresh(jump=False))
        self.find_edit.installEventFilter(self)
        self.replace_edit.installEventFilter(self)

    # -- opening / closing -----------------------------------------------------------------
    def open_find(self, replace: bool = False):
        sel = self.editor.textCursor().selectedText()
        if sel and " " not in sel and len(sel) < 200:
            self.find_edit.setText(sel)
        self.show()
        self.toggle_btn.setChecked(replace or self.toggle_btn.isChecked())
        self._show_replace(self.toggle_btn.isChecked())
        (self.replace_edit if replace and self.find_edit.text() else self.find_edit).setFocus()
        self.find_edit.selectAll() if not replace or not self.find_edit.text() else None
        self._refresh(jump=False)

    def close_bar(self):
        self._marks([])
        self.hide()
        self.editor.setFocus()
        self.closed.emit()

    def _show_replace(self, on: bool):
        self._replace_row.setVisible(on)
        self.toggle_btn.setText("▼" if on else "▶")

    def eventFilter(self, obj, event):
        if event.type() == event.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape:
                self.close_bar()
                return True
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and \
                    event.modifiers() & Qt.KeyboardModifier.ShiftModifier and obj is self.find_edit:
                self.step(True)
                return True
        return super().eventFilter(obj, event)

    def _enter(self):
        self.step(False)

    # -- search --------------------------------------------------------------------------
    def _options(self):
        return (self.case_check.isChecked(), self.word_check.isChecked(),
                self.regex_check.isChecked())

    def matches(self) -> list:
        """Matches as Qt positions of the editor text."""
        text = self.editor.toPlainText()
        try:
            found = find_matches(text, self.find_edit.text(), *self._options())
            self._regex_ok = True
        except re.error:
            self._regex_ok = False
            return []
        m16 = _u16(text)
        if m16:
            found = [(m16[a], m16[b]) for a, b in found]
        return found

    def _refresh(self, jump: bool):
        found = self.matches()
        self.find_edit.setStyleSheet("" if self._regex_ok else "background:#ffd6d6;color:#400;")
        q = bool(self.find_edit.text())
        if not self._regex_ok:
            self.count_label.setText("invalid pattern")
        elif not q:
            self.count_label.setText("")
        elif not found:
            self.count_label.setText("no match")
        else:
            cur = self.editor.textCursor()
            idx = next((i for i, (a, b) in enumerate(found)
                        if a == cur.selectionStart() and b == cur.selectionEnd()), None)
            self.count_label.setText(f"{idx + 1} of {len(found)}" if idx is not None
                                     else f"{len(found)} matches")
        self._marks(found)
        if jump and found:
            start = self.editor.textCursor().selectionStart()
            hit = next(((a, b) for a, b in found if a >= start), found[0])
            self._select(*hit)

    def _marks(self, found):
        sels = []
        fmt_color = QColor(255, 224, 80, 150)
        for a, b in found[:MAX_MARKS]:
            sel = QTextEdit.ExtraSelection()
            sel.format.setBackground(fmt_color)
            c = QTextCursor(self.editor.document())
            c.setPosition(a)
            c.setPosition(b, QTextCursor.MoveMode.KeepAnchor)
            sel.cursor = c
            sels.append(sel)
        self.editor.setExtraSelections(sels)

    def _select(self, a: int, b: int):
        c = self.editor.textCursor()
        c.setPosition(a)
        c.setPosition(b, QTextCursor.MoveMode.KeepAnchor)
        self.editor.setTextCursor(c)
        self.editor.ensureCursorVisible()
        self._update_count(a, b)

    def _update_count(self, a, b):
        found = self.matches()
        for i, (s, e) in enumerate(found):
            if (s, e) == (a, b):
                self.count_label.setText(f"{i + 1} of {len(found)}")
                return

    def step(self, backward: bool = False):
        found = self.matches()
        if not found:
            self._refresh(jump=False)
            return
        cur = self.editor.textCursor()
        if backward:
            hit = next(((a, b) for a, b in reversed(found) if b <= cur.selectionStart()),
                       found[-1])
        else:
            hit = next(((a, b) for a, b in found if a >= cur.selectionEnd()), found[0])
        self._select(*hit)

    # -- replace -------------------------------------------------------------------------
    def replace_current(self):
        found = self.matches()
        cur = self.editor.textCursor()
        sel = (cur.selectionStart(), cur.selectionEnd())
        if sel not in found:
            self.step(False)
            return
        case, word, regex = self._options()
        text = self.editor.toPlainText()
        m16 = _u16(text)
        py = lambda p: (m16.index(p) if m16 else p)               # noqa: E731
        pat = compile_query(self.find_edit.text(), case, word, regex)
        m = pat.search(text, py(sel[0]))
        new = expand(m, self.replace_edit.text(), regex) if m else self.replace_edit.text()
        cur.insertText(new)
        self.editor.setTextCursor(cur)
        self.step(False)

    def replace_all(self) -> int:
        """One undoable edit; returns the number of replacements."""
        case, word, regex = self._options()
        q = self.find_edit.text()
        if not q:
            return 0
        text = self.editor.toPlainText()
        try:
            pat = compile_query(q, case, word, regex)
        except re.error:
            return 0
        m16 = _u16(text)
        items = [m for m in pat.finditer(text) if m.end() > m.start()]
        if not items:
            return 0
        cur = self.editor.textCursor()
        cur.beginEditBlock()
        for m in reversed(items):                       # back to front keeps positions valid
            a, b = (m16[m.start()], m16[m.end()]) if m16 else (m.start(), m.end())
            cur.setPosition(a)
            cur.setPosition(b, QTextCursor.MoveMode.KeepAnchor)
            cur.insertText(expand(m, self.replace_edit.text(), regex))
        cur.endEditBlock()
        self.count_label.setText(f"{len(items)} replaced")
        self._marks([])
        return len(items)

    def shortcuts(self):
        """Key sequences handled by the owner: (find, replace, next, previous)."""
        return (QKeySequence.StandardKey.Find, QKeySequence("Ctrl+H"), QKeySequence("F3"),
                QKeySequence("Shift+F3"))
