"""Author list editor: names, affiliation keys, e-mail, corresponding author, and the
affiliations themselves. The document's front matter is written in whatever convention its
layout uses (authblk, achemso, elsarticle, RSC block) -- see :mod:`NoorSuite.templates`."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QLabel,
                             QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)


class AuthorsDialog(QDialog):
    """``authors`` = [{name, affils[keys], email, corresponding}], ``affiliations`` =
    {key: text} (what :func:`NoorSuite.templates.parse_authors` returns)."""

    def __init__(self, authors, affiliations, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Author list")
        self.resize(780, 560)
        self.result_authors: list = []
        self.result_affiliations: dict = {}
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(
            "Authors: affiliation keys are the keys of the table below, comma separated "
            "(e.g. 1,2). Tick 'corresponding' and give an e-mail for the corresponding author."))
        self.a_table = QTableWidget(0, 4)
        self.a_table.setHorizontalHeaderLabels(["Name", "Affiliations (keys)", "E-mail",
                                                "Corresponding"])
        self.a_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self.a_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self.a_table, 3)
        row = QHBoxLayout()
        for text, fn in (("Add author", lambda: self._add_author()),
                         ("Remove", lambda: self._remove(self.a_table)),
                         ("Up", lambda: self._move(self.a_table, -1)),
                         ("Down", lambda: self._move(self.a_table, 1))):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)

        lay.addWidget(QLabel("Affiliations (key = what the authors refer to):"))
        self.f_table = QTableWidget(0, 2)
        self.f_table.setHorizontalHeaderLabels(["Key", "Affiliation"])
        self.f_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self.f_table, 2)
        row2 = QHBoxLayout()
        add_f = QPushButton("Add affiliation")
        add_f.clicked.connect(lambda: self._add_affil())
        rm_f = QPushButton("Remove")
        rm_f.clicked.connect(lambda: self._remove(self.f_table))
        row2.addWidget(add_f)
        row2.addWidget(rm_f)
        row2.addStretch(1)
        lay.addLayout(row2)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

        for a in authors or []:
            self._add_author(a)
        for k, t in (affiliations or {}).items():
            self._add_affil(k, t)
        if not authors:
            self._add_author()

    # ----------------------------------------------------------------- rows
    def _add_author(self, a=None):
        a = a or {"name": "", "affils": [], "email": "", "corresponding": False}
        r = self.a_table.rowCount()
        self.a_table.insertRow(r)
        self.a_table.setItem(r, 0, QTableWidgetItem(a.get("name", "")))
        self.a_table.setItem(r, 1, QTableWidgetItem(",".join(a.get("affils", []))))
        self.a_table.setItem(r, 2, QTableWidgetItem(a.get("email", "")))
        c = QTableWidgetItem()
        c.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
        c.setCheckState(Qt.CheckState.Checked if a.get("corresponding")
                        else Qt.CheckState.Unchecked)
        self.a_table.setItem(r, 3, c)

    def _add_affil(self, key=None, text=""):
        r = self.f_table.rowCount()
        self.f_table.insertRow(r)
        self.f_table.setItem(r, 0, QTableWidgetItem(key if key is not None else str(r + 1)))
        self.f_table.setItem(r, 1, QTableWidgetItem(text))

    @staticmethod
    def _remove(table):
        r = table.currentRow()
        if r >= 0:
            table.removeRow(r)

    def _move(self, table, d):
        r = table.currentRow()
        if r < 0 or not 0 <= r + d < table.rowCount():
            return
        cols = table.columnCount()
        for c in range(cols):
            a, b = table.takeItem(r, c), table.takeItem(r + d, c)
            table.setItem(r, c, b)
            table.setItem(r + d, c, a)
        table.setCurrentCell(r + d, 0)

    # --------------------------------------------------------------- result
    def _accept(self):
        aff = {}
        for r in range(self.f_table.rowCount()):
            k = (self.f_table.item(r, 0).text() if self.f_table.item(r, 0) else "").strip()
            t = (self.f_table.item(r, 1).text() if self.f_table.item(r, 1) else "").strip()
            if k and t:
                aff[k] = t
        authors = []
        for r in range(self.a_table.rowCount()):
            name = (self.a_table.item(r, 0).text() if self.a_table.item(r, 0) else "").strip()
            if not name:
                continue
            keys = [k.strip() for k in (self.a_table.item(r, 1).text()
                                        if self.a_table.item(r, 1) else "").split(",") if k.strip()]
            authors.append({
                "name": name, "affils": [k for k in keys if k in aff],
                "email": (self.a_table.item(r, 2).text() if self.a_table.item(r, 2) else "").strip(),
                "corresponding": self.a_table.item(r, 3).checkState() == Qt.CheckState.Checked})
        self.result_authors, self.result_affiliations = authors, aff
        self.accept()
