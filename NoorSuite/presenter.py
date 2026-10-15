"""Presenter console for the slide show: current slide, next slide, notes, timer, laser pointer.

The audience view is :class:`NoorSuite.docview.PresentationView`; this window drives it (and follows
it when the audience view changes pages with keys or clicks).
"""

from __future__ import annotations

import time

from PyQt6.QtCore import QSettings, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QKeySequence, QPixmap, QShortcut
from PyQt6.QtPdf import QPdfDocument
from PyQt6.QtWidgets import (QGridLayout, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
                             QSizePolicy, QSpinBox, QVBoxLayout, QWidget)

_STYLE = """
QWidget { background: #15171c; color: #e8eaf0; }
QLabel#clock { font-size: 15px; color: #9aa3b5; }
QLabel#timer { font-size: 44px; font-weight: 600; }
QLabel#info { font-size: 15px; color: #9aa3b5; }
QLabel#slide { background: #000; border: 1px solid #2c303a; }
QPlainTextEdit { background: #1d2027; border: 1px solid #2c303a; font-size: 18px; padding: 6px; }
QPushButton { background: #2a2f3a; border: 1px solid #3a4050; padding: 6px 12px; border-radius: 4px; }
QPushButton:checked { background: #c0392b; }
QPushButton:hover { background: #343b49; }
QSpinBox { background: #1d2027; border: 1px solid #3a4050; padding: 3px; }
"""


def format_time(seconds: float) -> str:
    s = int(abs(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    sign = "-" if seconds < 0 else ""
    return f"{sign}{h}:{m:02d}:{sec:02d}" if h else f"{sign}{m:02d}:{sec:02d}"


class _SlideLabel(QLabel):
    """Shows one rendered page; mouse movement over it drives the laser pointer of the audience."""

    moved = pyqtSignal(float, float)         # normalised position inside the slide
    left = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setObjectName("slide")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(160, 90)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setMouseTracking(True)
        self._pix_size = QSize()

    def mouseMoveEvent(self, e):
        pm = self.pixmap()
        if pm is None or pm.isNull():
            return
        w, h = pm.width() / pm.devicePixelRatio(), pm.height() / pm.devicePixelRatio()
        x0, y0 = (self.width() - w) / 2, (self.height() - h) / 2
        nx, ny = (e.position().x() - x0) / w, (e.position().y() - y0) / h
        if 0 <= nx <= 1 and 0 <= ny <= 1:
            self.moved.emit(nx, ny)

    def leaveEvent(self, e):
        self.left.emit()


class PresenterConsole(QWidget):
    """``view`` = the audience :class:`PresentationView`; ``pages`` = ``page_map`` rows;
    ``notes`` = {frame index: text}; ``titles`` = {frame index: title}."""

    closed = pyqtSignal()

    def __init__(self, view, pdf_path: str, pages: list, notes: dict, titles: dict,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Presenter view")
        self.setStyleSheet(_STYLE)
        self.view, self.pages, self.notes, self.titles = view, pages, notes, titles
        self._doc = QPdfDocument(self)
        self._doc.load(pdf_path)
        self._settings = QSettings("NoorSuite", "NoorSuite")
        self._t0 = None                    # running since
        self._acc = 0.0                    # seconds before the last pause
        self._seen_first_change = False

        self.cur = _SlideLabel()
        self.next = QLabel()
        self.next.setObjectName("slide")
        self.next.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.next.setMinimumSize(160, 90)
        self.next.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.notes_view = QPlainTextEdit()
        self.notes_view.setReadOnly(True)
        self.notes_view.setPlaceholderText("No notes for this slide  (\\note{...} in the frame)")
        self.timer_label = QLabel("00:00")
        self.timer_label.setObjectName("timer")
        self.clock_label = QLabel("")
        self.clock_label.setObjectName("clock")
        self.info_label = QLabel("")
        self.info_label.setObjectName("info")
        self.next_label = QLabel("Next")
        self.next_label.setObjectName("info")
        self.target = QSpinBox()
        self.target.setRange(0, 600)
        self.target.setSuffix(" min")
        self.target.setSpecialValueText("no target")
        self.target.setValue(int(self._settings.value("presenter/target", 0) or 0))
        self.target.valueChanged.connect(
            lambda v: self._settings.setValue("presenter/target", v))
        self.pause_btn = QPushButton("Pause")
        self.pause_btn.setCheckable(True)
        self.pause_btn.toggled.connect(self._toggle_pause)
        reset_btn = QPushButton("Reset timer")
        reset_btn.clicked.connect(self.reset_timer)
        self.laser_btn = QPushButton("Laser (L)")
        self.laser_btn.setCheckable(True)
        self.laser_btn.toggled.connect(self.view.set_laser)
        prev_btn = QPushButton("◀  Back")
        prev_btn.clicked.connect(lambda: self.view.go(self.view.page - 1))
        next_btn = QPushButton("Next  ▶")
        next_btn.clicked.connect(lambda: self.view.go(self.view.page + 1))
        black_btn = QPushButton("Black (B)")
        black_btn.clicked.connect(lambda: self.view.toggle_cover("black"))
        end_btn = QPushButton("End show")
        end_btn.clicked.connect(self.close)

        right = QVBoxLayout()
        right.addWidget(self.next_label)
        right.addWidget(self.next, 2)
        right.addWidget(self.notes_view, 3)
        top = QHBoxLayout()
        top.addWidget(self.cur, 3)
        top.addLayout(right, 2)
        bar = QGridLayout()
        bar.addWidget(self.timer_label, 0, 0, 2, 1)
        bar.addWidget(self.clock_label, 0, 1)
        bar.addWidget(self.info_label, 1, 1)
        bar.addWidget(QLabel("Target"), 0, 2)
        bar.addWidget(self.target, 1, 2)
        btns = QHBoxLayout()
        for b in (prev_btn, next_btn, black_btn, self.laser_btn, self.pause_btn, reset_btn,
                  end_btn):
            btns.addWidget(b)
        bar.addLayout(btns, 0, 3, 2, 1)
        bar.setColumnStretch(3, 1)
        lay = QVBoxLayout(self)
        lay.addLayout(top, 1)
        lay.addLayout(bar)

        self.cur.moved.connect(self.view.laser_at)
        self.cur.left.connect(self.view.laser_hide)
        for keys, fn in (("Right", lambda: self.view.go(self.view.page + 1)),
                         ("Space", lambda: self.view.go(self.view.page + 1)),
                         ("Left", lambda: self.view.go(self.view.page - 1)),
                         ("B", lambda: self.view.toggle_cover("black")),
                         ("L", lambda: self.laser_btn.toggle()),
                         ("Escape", self.close)):
            QShortcut(QKeySequence(keys), self, activated=fn)
        self.view.pageChanged.connect(self.refresh)
        self.view.closed.connect(lambda _p: self.close())
        self._tick = QTimer(self)
        self._tick.timeout.connect(self._update_clock)
        self._tick.start(500)
        self.resize(1180, 700)
        self.start_timer()
        self.refresh(self.view.page)

    # -- timer ----------------------------------------------------------------------------
    def elapsed(self) -> float:
        return self._acc + ((time.time() - self._t0) if self._t0 else 0.0)

    def start_timer(self):
        if self._t0 is None:
            self._t0 = time.time()

    def reset_timer(self):
        self._acc = 0.0
        self._t0 = None if self.pause_btn.isChecked() else time.time()
        self._update_clock()

    def _toggle_pause(self, on: bool):
        if on:
            self._acc = self.elapsed()
            self._t0 = None
            self.pause_btn.setText("Resume")
        else:
            self._t0 = time.time()
            self.pause_btn.setText("Pause")

    def _update_clock(self):
        el = self.elapsed()
        mins = self.target.value()
        if mins:
            left = mins * 60 - el
            self.timer_label.setText(format_time(left))
            self.timer_label.setStyleSheet(
                "color:#ff6b5b;" if left < 0 else ("color:#ffd15b;" if left < 60 else ""))
        else:
            self.timer_label.setText(format_time(el))
            self.timer_label.setStyleSheet("")
        self.clock_label.setText(time.strftime("%H:%M"))

    # -- slides ---------------------------------------------------------------------------
    def _render(self, page: int, label: QLabel):
        if not 0 <= page < self._doc.pageCount():
            label.clear()
            return
        pts = self._doc.pagePointSize(page)
        w, h = max(1, label.width() - 4), max(1, label.height() - 4)
        scale = min(w / max(1.0, pts.width()), h / max(1.0, pts.height()))
        dpr = self.devicePixelRatioF()
        size = QSize(max(1, round(pts.width() * scale * dpr)),
                     max(1, round(pts.height() * scale * dpr)))
        img = self._doc.render(page, size)
        img.setDevicePixelRatio(dpr)
        label.setPixmap(QPixmap.fromImage(img))

    def refresh(self, page: int):
        n = self._doc.pageCount()
        self._render(page, self.cur)
        self._render(page + 1, self.next)
        if page + 1 >= n:
            self.next.clear()
            self.next.setText("End of the show")
        row = self.pages[page] if page < len(self.pages) else (0, 0, 1)
        frame, step, steps = row
        self.notes_view.setPlainText(self.notes.get(frame, ""))
        title = self.titles.get(frame, "")
        extra = f"   ·   step {step + 1} / {steps}" if steps > 1 else ""
        self.info_label.setText(f"Slide {page + 1} / {n}{extra}"
                                + (f"   ·   {title}" if title else ""))
        nxt = self.pages[page + 1] if page + 1 < len(self.pages) else None
        if nxt and nxt[0] == frame:
            self.next_label.setText(f"Next  (same slide, step {nxt[1] + 1})")
        else:
            self.next_label.setText("Next slide" if nxt else "Next")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        QTimer.singleShot(0, lambda: self.refresh(self.view.page))

    def closeEvent(self, e):
        self._tick.stop()
        try:
            self.view.pageChanged.disconnect(self.refresh)
        except TypeError:
            pass
        self.closed.emit()
        if self.view.isVisible():
            self.view.close()
        super().closeEvent(e)
