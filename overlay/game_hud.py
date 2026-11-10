"""Cyberpunk Game Co-Pilot HUD Overlay.

Inspired by Capri Desktop's `GameHud.cs`:
- Floating click-through or draggable gaming HUD.
- Framed avatar panel displaying Alyssa's mood / expression:
  (`idle`, `focus`, `talk`, `happy`, `hit`, `surprise`).
- Keybind cheat sheet for the active game (W/A/S/D, abilities, heal, scanner).
- Real-time spoken dialogue / reply box for tips and reactions.
- Status bar for active replay clipping buffer ('REC') and visual guards ('SENSE').
"""

from __future__ import annotations

from typing import Dict, Optional

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from actions.game_sense import get_game_controls
from .widgets import _TopmostWindowMixin
from .theming import _rgba, _theme


class GameHudWidget(_TopmostWindowMixin, QWidget):
    """Floating cyberpunk HUD for gaming co-pilot."""

    MOOD_COLORS = {
        "idle": "#7F8EA3",
        "focus": "#00E5FF",      # Cyberpunk cyan
        "talk": "#A855F7",       # Electric purple
        "happy": "#10B981",      # Emerald
        "hit": "#EF4444",        # Crimson hurt
        "surprise": "#F59E0B",   # Amber
    }

    def __init__(self, companion, settings: dict):
        flags = Qt.FramelessWindowHint | Qt.Tool | Qt.NoDropShadowWindowHint
        if settings.get("always_on_top", True):
            flags |= Qt.WindowStaysOnTopHint
        super().__init__(None, flags)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.companion = companion
        self.settings = settings

        self._mood = "idle"
        self._current_game = "No Game Active"
        self._current_line = "Co-pilot standby. Ready for your game."
        self._controls: Dict[str, str] = {}
        self._minimized = False
        self._drag_start_pos: Optional[QPoint] = None

        self._init_ui()
        self.resize(340, 180)
        self.hide()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        # Header bar
        header = QHBoxLayout()
        header.setSpacing(6)

        self.game_badge = QLabel("🎮 GAME CO-PILOT")
        self.game_badge.setStyleSheet(
            "color: #00E5FF; font-weight: bold; font-size: 11px; letter-spacing: 1px;"
        )
        header.addWidget(self.game_badge, 1)

        self.rec_badge = QLabel("● REC")
        self.rec_badge.setStyleSheet("color: #EF4444; font-weight: bold; font-size: 10px;")
        header.addWidget(self.rec_badge)

        self.sense_badge = QLabel("SENSE")
        self.sense_badge.setStyleSheet("color: #10B981; font-weight: bold; font-size: 10px;")
        header.addWidget(self.sense_badge)

        self.min_btn = QPushButton("—")
        self.min_btn.setFixedSize(18, 18)
        self.min_btn.setCursor(Qt.PointingHandCursor)
        self.min_btn.setStyleSheet(
            "QPushButton { background: rgba(255,255,255,0.1); color: #fff; border: none; border-radius: 9px; font-size: 10px; }"
            "QPushButton:hover { background: rgba(255,255,255,0.25); }"
        )
        self.min_btn.clicked.connect(self.toggle_minimized)
        header.addWidget(self.min_btn)

        root.addLayout(header)

        # Body area
        self.body_widget = QWidget()
        body_layout = QVBoxLayout(self.body_widget)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(4)

        # Spoken line / reaction bubble
        self.line_label = QLabel(self._current_line)
        self.line_label.setWordWrap(True)
        self.line_label.setStyleSheet(
            "color: #E6EDF3; font-size: 11px; background: rgba(255,255,255,0.06); "
            "padding: 6px 8px; border-radius: 6px; border-left: 3px solid #00E5FF;"
        )
        body_layout.addWidget(self.line_label)

        # Controls cheat sheet area
        self.controls_label = QLabel("No controls loaded.")
        self.controls_label.setWordWrap(True)
        self.controls_label.setStyleSheet("color: #94A3B8; font-size: 10px; padding: 2px;")
        body_layout.addWidget(self.controls_label)

        root.addWidget(self.body_widget)

    def set_game(self, game_name: str):
        """Update active game and load its controls."""
        self._current_game = game_name
        self.game_badge.setText(f"🎮 {game_name.upper()}")
        self._controls = get_game_controls(game_name)
        if self._controls:
            items = [f"<b>{k}:</b> {v}" for k, v in list(self._controls.items())[:6]]
            self.controls_label.setText("  •  ".join(items))
        else:
            self.controls_label.setText("Controls: press wake hotkey to teach Alyssa.")
        self.update()

    def set_mood(self, mood: str):
        """Update Alyssa's expression: idle, focus, talk, happy, hit, surprise."""
        self._mood = mood.lower() if mood.lower() in self.MOOD_COLORS else "idle"
        accent = self.MOOD_COLORS[self._mood]
        self.line_label.setStyleSheet(
            f"color: #E6EDF3; font-size: 11px; background: rgba(255,255,255,0.06); "
            f"padding: 6px 8px; border-radius: 6px; border-left: 3px solid {accent};"
        )
        self.update()

    def say(self, line: str, mood: Optional[str] = None):
        """Update reaction/speech line."""
        self._current_line = line
        self.line_label.setText(line)
        if mood:
            self.set_mood(mood)
        else:
            self.set_mood("talk")
        self.update()

    def toggle_minimized(self):
        self._minimized = not self._minimized
        self.body_widget.setVisible(not self._minimized)
        if self._minimized:
            self.min_btn.setText("+")
            self.resize(240, 36)
        else:
            self.min_btn.setText("—")
            self.resize(340, 180)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Cyberpunk glass card background
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect().adjusted(1, 1, -1, -1)), 8, 8)
        painter.fillPath(path, QColor(13, 17, 23, 235))

        # Mood-reactive neon border
        accent_color = QColor(self.MOOD_COLORS.get(self._mood, "#00E5FF"))
        painter.strokePath(path, QPen(accent_color, 1.2))

    def reposition(self):
        cw = self.companion
        if cw is not None and not getattr(self, "_user_moved", False):
            x = max(20, cw.x() - self.width() - 12)
            y = max(20, cw.y())
            self.move(x, y)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_start_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and self._drag_start_pos:
            self.move(event.globalPosition().toPoint() - self._drag_start_pos)
            self._user_moved = True
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_start_pos = None
