"""The data-to-table editor: pick a data object, its columns (header, number format, alignment,
scale), the rows, and see the LaTeX that will be written (see :mod:`NoorSuite.tables`)."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFontDatabase
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                             QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                             QPlainTextEdit, QPushButton, QSpinBox, QTableWidget,
                             QTableWidgetItem, QVBoxLayout, QWidget)

from . import tables
from .model import TableModel


class TableEditorDialog(QDialog):
    """Create (``table=None``) or edit a :class:`TableModel`. ``self.table`` is the result."""

    def __init__(self, host, table: TableModel | None = None, parent=None, data_id: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Table from data" if table is None else f"Table - {table.name}")
        self.host = host
        self.table = table
        self._loading = False
        self._new = table is None
        self.resize(900, 640)

        lay = QVBoxLayout(self)
        top = QFormLayout()
        self.name_edit = QLineEdit(table.name if table else "")
        self.name_edit.setPlaceholderText("name used in the document: \\noortable{name}")
        top.addRow("Name:", self.name_edit)
        self.data_combo = QComboBox()
        for obj in host.repository.values():
            self.data_combo.addItem(f"{obj.name}  ({obj.nrows} rows)", obj.id)
        want = table.data_id if table else data_id
        i = self.data_combo.findData(want)
        if i >= 0:
            self.data_combo.setCurrentIndex(i)
        self.data_combo.currentIndexChanged.connect(self._data_changed)
        top.addRow("Data object:", self.data_combo)
        lay.addLayout(top)

        self.col_table = QTableWidget(0, 6)
        self.col_table.setHorizontalHeaderLabels(
            ["Column (tick = shown)", "Header (empty = name + unit)", "Format", "Digits",
             "Align", "Scale x"])
        self.col_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self.col_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self.col_table, 2)
        mv = QHBoxLayout()
        up, down = QPushButton("Move up"), QPushButton("Move down")
        up.clicked.connect(lambda: self._move(-1))
        down.clicked.connect(lambda: self._move(1))
        mv.addWidget(up)
        mv.addWidget(down)
        mv.addStretch(1)
        lay.addLayout(mv)

        opts = QHBoxLayout()
        self.first_spin = QSpinBox()
        self.first_spin.setRange(1, 10 ** 9)
        self.last_spin = QSpinBox()
        self.last_spin.setRange(0, 10 ** 9)
        self.last_spin.setSpecialValueText("last")
        self.step_spin = QSpinBox()
        self.step_spin.setRange(1, 10 ** 6)
        self.max_spin = QSpinBox()
        self.max_spin.setRange(0, 10 ** 6)
        self.max_spin.setSpecialValueText("no limit")
        self.sort_combo = QComboBox()
        self.desc_check = QCheckBox("descending")
        self.rules_check = QCheckBox("booktabs rules")
        for lab, w in (("Rows from", self.first_spin), ("to", self.last_spin),
                       ("every", self.step_spin), ("at most", self.max_spin),
                       ("sort by", self.sort_combo)):
            opts.addWidget(QLabel(lab))
            opts.addWidget(w)
        opts.addWidget(self.desc_check)
        opts.addWidget(self.rules_check)
        opts.addStretch(1)
        lay.addLayout(opts)

        lay.addWidget(QLabel("LaTeX (what the document gets; a live table follows the data):"))
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        lay.addWidget(self.preview, 2)

        self.live_note = QLabel("")
        lay.addWidget(self.live_note)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText(
            "Insert" if self._new else "Apply")
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

        for w in (self.first_spin, self.last_spin, self.step_spin, self.max_spin):
            w.valueChanged.connect(self._refresh)
        self.sort_combo.currentIndexChanged.connect(self._refresh)
        self.desc_check.toggled.connect(self._refresh)
        self.rules_check.toggled.connect(self._refresh)
        self.col_table.itemChanged.connect(self._refresh)
        self._load_columns()

    # -- columns --------------------------------------------------------------------
    def _data(self):
        return self.host.repository.get(self.data_combo.currentData())

    def _data_changed(self, _i):
        if not self._loading:
            if self.table is not None:
                self.table.columns = []
            self._load_columns()

    def _load_columns(self):
        data = self._data()
        self._loading = True
        try:
            self.col_table.setRowCount(0)
            self.sort_combo.clear()
            self.sort_combo.addItem("(data order)", "")
            if data is None:
                return
            have = {c["col"]: c for c in (self.table.columns if self.table else [])}
            # the table's own order first, then the remaining columns of the object
            order = [c for c in have if c in data.columns] + \
                    [c for c in data.column_order if c not in have]
            for name in order:
                spec = have.get(name)
                self._add_row(data, name, spec)
                self.sort_combo.addItem(name, name)
            t = self.table
            self.first_spin.setValue(int(t.row_start) if t else 1)
            self.last_spin.setValue(int(t.row_end) if t and t.row_end else 0)
            self.step_spin.setValue(int(t.step) if t else 1)
            self.max_spin.setValue(int(t.max_rows) if t else 0)
            self.desc_check.setChecked(bool(t.descending) if t else False)
            self.rules_check.setChecked(bool(t.booktabs) if t else True)
            j = self.sort_combo.findData(t.sort_by if t else "")
            self.sort_combo.setCurrentIndex(max(0, j))
            if not self.name_edit.text().strip():
                self.name_edit.setText(data.name)
        finally:
            self._loading = False
        self._refresh()

    def _add_row(self, data, name, spec):
        r = self.col_table.rowCount()
        self.col_table.insertRow(r)
        it = QTableWidgetItem(name + (f"  [{data.units[name]}]" if data.units.get(name) else ""))
        it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
                    | Qt.ItemFlag.ItemIsSelectable)
        it.setCheckState(Qt.CheckState.Checked if (spec is not None or self.table is None)
                         else Qt.CheckState.Unchecked)
        it.setData(Qt.ItemDataRole.UserRole, name)
        self.col_table.setItem(r, 0, it)
        self.col_table.setItem(r, 1, QTableWidgetItem((spec or {}).get("header", "")))
        fmt = QComboBox()
        fmt.addItems(tables.FORMAT_LABELS)
        fmt.setCurrentIndex(tables.FORMATS.index((spec or {}).get("fmt", "auto"))
                            if (spec or {}).get("fmt", "auto") in tables.FORMATS else 0)
        fmt.currentIndexChanged.connect(self._refresh)
        self.col_table.setCellWidget(r, 2, fmt)
        dig = QSpinBox()
        dig.setRange(0, 15)
        dig.setValue(int((spec or {}).get("digits", 3)))
        dig.valueChanged.connect(self._refresh)
        self.col_table.setCellWidget(r, 3, dig)
        al = QComboBox()
        al.addItems(tables.ALIGN_LABELS)
        al.setCurrentIndex(tables.ALIGNS.index((spec or {}).get("align", "r"))
                           if (spec or {}).get("align", "r") in tables.ALIGNS else 2)
        al.currentIndexChanged.connect(self._refresh)
        self.col_table.setCellWidget(r, 4, al)
        sc = QDoubleSpinBox()
        sc.setRange(-1e12, 1e12)
        sc.setDecimals(6)
        sc.setValue(float((spec or {}).get("scale", 1.0)))
        sc.valueChanged.connect(self._refresh)
        self.col_table.setCellWidget(r, 5, sc)

    def _move(self, d):
        r = self.col_table.currentRow()
        if r < 0 or not 0 <= r + d < self.col_table.rowCount():
            return
        specs = self._specs(include_unchecked=True)
        specs[r], specs[r + d] = specs[r + d], specs[r]
        data = self._data()
        self._loading = True
        try:
            self.col_table.setRowCount(0)
            for s in specs:
                self._add_row(data, s["col"], s)
                self.col_table.item(self.col_table.rowCount() - 1, 0).setCheckState(
                    Qt.CheckState.Checked if s.get("_on") else Qt.CheckState.Unchecked)
        finally:
            self._loading = False
        self.col_table.setCurrentCell(r + d, 0)
        self._refresh()

    def _specs(self, include_unchecked=False) -> list:
        out = []
        for r in range(self.col_table.rowCount()):
            it = self.col_table.item(r, 0)
            on = it.checkState() == Qt.CheckState.Checked
            if not on and not include_unchecked:
                continue
            out.append({
                "col": it.data(Qt.ItemDataRole.UserRole),
                "header": (self.col_table.item(r, 1).text() if self.col_table.item(r, 1) else ""),
                "fmt": tables.FORMATS[self.col_table.cellWidget(r, 2).currentIndex()],
                "digits": self.col_table.cellWidget(r, 3).value(),
                "align": tables.ALIGNS[self.col_table.cellWidget(r, 4).currentIndex()],
                "scale": self.col_table.cellWidget(r, 5).value() or 1.0,
                "_on": on})
        return out

    # -- model <-> widgets ----------------------------------------------------------------
    def _build(self) -> TableModel:
        t = TableModel(self.name_edit.text().strip() or "Table",
                       self.data_combo.currentData() or "",
                       [{k: v for k, v in s.items() if k != "_on"} for s in self._specs()],
                       self.table.table_id if self.table else None)
        t.row_start = self.first_spin.value()
        t.row_end = self.last_spin.value() or None
        t.step = self.step_spin.value()
        t.max_rows = self.max_spin.value()
        t.sort_by = self.sort_combo.currentData() or ""
        t.descending = self.desc_check.isChecked()
        t.booktabs = self.rules_check.isChecked()
        return t

    def _refresh(self, *_):
        if self._loading:
            return
        t = self._build()
        data = self._data()
        self.preview.setPlainText(tables.render_table(t, data))
        shown = len(tables._row_indices(t, data.nrows if data else 0, None))
        self.live_note.setText(f"{len(t.columns)} column(s), about {shown} row(s)")

    def _accept(self):
        t = self._build()
        if not t.columns or not t.data_id:
            return
        self.table = t
        self.accept()
