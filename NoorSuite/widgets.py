"""Small shared Qt widgets used across the GUI modules."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QToolButton, QVBoxLayout, QWidget


class CollapsibleSection(QWidget):
    """A titled section whose body collapses / expands when the header is clicked."""

    toggled = pyqtSignal(bool)

    def __init__(self, title: str, body: QWidget, expanded: bool = True, parent=None):
        super().__init__(parent)
        self._body = body
        self.toggle = QToolButton()
        self.toggle.setText(title)
        self.toggle.setCheckable(True)
        self.toggle.setChecked(expanded)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.toggle.setStyleSheet("QToolButton { border: none; font-weight: bold; }")
        self.toggle.toggled.connect(self._on_toggled)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        lay.addWidget(self.toggle)
        lay.addWidget(body)
        body.setVisible(expanded)

    def set_title(self, title: str) -> None:
        self.toggle.setText(title)

    def set_expanded(self, expanded: bool) -> None:
        self.toggle.setChecked(expanded)

    def is_expanded(self) -> bool:
        return self.toggle.isChecked()

    def _on_toggled(self, checked: bool):
        self._body.setVisible(checked)
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)
        self.toggled.emit(checked)
