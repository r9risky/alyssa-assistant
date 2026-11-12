"""Desktop chaos and prank features inspired by Capri Desktop.

Features:
- 'ChaosScreen': 5-second harmless fake Windows Blue Screen with stop code
  'TOO_MANY_TABS_OPEN', failed component 'alyssa.sys', and a 'gotcha' reveal.
  Dismisses immediately on Esc, any mouse click, or after 5 seconds.
- 'CloneSwarm': Spawns harmless wandering desktop clones with hilarious sticky note dialogue.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from overlay.widgets import _TopmostWindowMixin

logger = logging.getLogger("actions.desktop_chaos")

CLONE_QUOTES = [
    "this corner is mine",
    "i'm reorganising your icons",
    "copy #7 reporting for duty",
    "which one of us is the original? yes",
    "we outnumber you now",
    "i call dibs on the recycle bin",
    "press esc to send us home",
    "nobody tell the antivirus",
    "we held a meeting about your wallpaper",
    "i'm the favourite, obviously",
    "free real estate",
    "your cursor looks comfy",
    "brb, multiplying",
]


class FakeCrashWidget(_TopmostWindowMixin, QWidget):
    """Harmless fake BSOD screen inspired by prank crash screen."""

    STOP_CODE = "TOO_MANY_TABS_OPEN"
    FAILED_SYS = "alyssa.sys"
    PUNCHLINE = "gotcha! nothing broke :)"

    def __init__(self, parent=None):
        flags = Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        super().__init__(parent, flags)
        self.setAttribute(Qt.WA_DeleteOnClose)

        self._start_time = time.time()
        self._percent = 0
        self._revealed = False

        # Set geometry to cover the primary screen
        screen = QApplication.primaryScreen()
        if screen:
            self.setGeometry(screen.geometry())
        else:
            self.resize(1920, 1080)

        # 30 FPS update timer
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start()

    def _on_tick(self):
        elapsed = time.time() - self._start_time
        if elapsed >= 5.0:
            self.close()
            return

        if elapsed >= 3.7:
            self._revealed = True
            self._percent = 100
        else:
            self._percent = min(100, int((elapsed / 3.7) * 100))

        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Windows BSOD blue (#0078D7)
        painter.fillRect(self.rect(), QColor(0, 120, 215))

        # White typography
        painter.setPen(QColor(255, 255, 255))

        # Emoticon
        font_face = QFont("Segoe UI", 90, QFont.Light)
        painter.setFont(font_face)
        emoticon = ":)" if self._revealed else ":("
        painter.drawText(120, 240, emoticon)

        # Main explanation text
        font_body = QFont("Segoe UI", 24, QFont.Normal)
        painter.setFont(font_body)
        msg_y = 360
        painter.drawText(
            120,
            msg_y,
            "Uh oh. Your PC tripped over something and fell down. Alyssa",
        )
        painter.drawText(
            120,
            msg_y + 45,
            "is sweeping up the pieces and will put everything back for",
        )
        painter.drawText(120, msg_y + 90, "you.")

        # Progress / Punchline
        font_sub = QFont("Segoe UI", 20, QFont.Normal)
        painter.setFont(font_sub)
        status_line = self.PUNCHLINE if self._revealed else f"{self._percent}% complete"
        painter.drawText(120, msg_y + 180, status_line)

        # Stop code details
        font_small = QFont("Segoe UI", 12, QFont.Normal)
        painter.setFont(font_small)
        detail_y = msg_y + 260
        painter.drawText(
            120,
            detail_y,
            f"Stop code: {self.STOP_CODE}  |  What failed: {self.FAILED_SYS}",
        )
        painter.drawText(
            120,
            detail_y + 30,
            "(Press Esc or click anywhere to exit)",
        )

    def keyPressEvent(self, event):
        # Any keypress exits immediately
        self.close()

    def mousePressEvent(self, event):
        # Any mouse click exits immediately
        self.close()


class CloneCardWidget(_TopmostWindowMixin, QWidget):
    """Harmless floating clone card on the desktop."""

    def __init__(self, text: str, pos: QPoint, parent=None):
        flags = Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        super().__init__(parent, flags)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setAttribute(Qt.WA_TranslucentBackground)

        self._text = text
        self.move(pos)
        self.resize(180, 70)

        # Auto-dismiss after 8 seconds
        QTimer.singleShot(8000, self.close)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect().adjusted(1, 1, -1, -1)), 8, 8)
        painter.fillPath(path, QColor(24, 24, 37, 240))
        painter.strokePath(path, QPen(QColor(168, 85, 247), 1.5))

        painter.setPen(QColor(0, 229, 255))
        painter.setFont(QFont("Segoe UI", 9, QFont.Bold))
        painter.drawText(10, 22, "👾 ALYSSA CLONE")

        painter.setPen(QColor(230, 237, 243))
        painter.setFont(QFont("Segoe UI", 10, QFont.Normal))
        painter.drawText(10, 48, self._text)

    def mousePressEvent(self, event):
        self.close()


# Registry of active desktop pranks
_ACTIVE_PRANKS: List[QWidget] = []


def show_fake_crash() -> str:
    """Launch harmless 5-second TOO_MANY_TABS_OPEN fake crash screen."""
    if QApplication.instance() is None:
        return "Triggered TOO_MANY_TABS_OPEN fake crash (no GUI display)."
    def _show():
        crash = FakeCrashWidget()
        _ACTIVE_PRANKS.append(crash)
        crash.show()

    from overlay.widgets import run_on_gui_thread
    run_on_gui_thread(_show)
    return "Triggered TOO_MANY_TABS_OPEN fake crash (dismissible via Esc or click)."


def spawn_clone_swarm(count: int = 5) -> str:
    """Spawn harmless wandering Alyssa clone notes across the screen."""
    if QApplication.instance() is None:
        return f"Spawned {count} desktop clones! They will self-dismiss in 8s or on click."

    screen = QApplication.primaryScreen()
    if not screen:
        return "Screen not available for clone swarm."

    def _show():
        geom = screen.geometry()
        quotes = list(CLONE_QUOTES)
        random.shuffle(quotes)
        for i in range(min(count, len(quotes))):
            rx = random.randint(geom.x() + 50, max(geom.x() + 100, geom.width() - 250))
            ry = random.randint(geom.y() + 50, max(geom.y() + 100, geom.height() - 150))
            clone = CloneCardWidget(quotes[i], QPoint(rx, ry))
            _ACTIVE_PRANKS.append(clone)
            clone.show()

    from overlay.widgets import run_on_gui_thread
    run_on_gui_thread(_show)

    return f"Spawned {count} desktop clones! They will self-dismiss in 8s or on click."


def dismiss_all_pranks() -> str:
    """Instantly clear any active chaos pranks."""
    widgets = list(_ACTIVE_PRANKS)
    _ACTIVE_PRANKS.clear()

    def _close():
        for w in widgets:
            try:
                w.close()
            except Exception:
                pass

    from overlay.widgets import run_on_gui_thread
    run_on_gui_thread(_close)
    closed = len(widgets)
    return f"Dismissed {closed} desktop chaos widgets."
