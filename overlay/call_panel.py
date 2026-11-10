"""Cyberpunk Call Mode HUD Overlay.

Inspired by Capri Desktop's `CallPanel.cs`:
- Active Call Pill and expanded HUD with live call duration clock.
- Three audio-reactive speaker rings with expanding ripples:
  1. `You` (User microphone)
  2. `Her` (Alyssa voice playback)
  3. `Call` (Discord / Zoom / meeting voices)
- Real-time live diarized captions banner ("who said what").
- Red hang-up button to disconnect and exit call mode.
"""

from __future__ import annotations

import math
import time

from PySide6.QtCore import QObject, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from call_ears import get_call_session
from .widgets import _TopmostWindowMixin
from .theming import _rgba, _theme


class _CallPanelSignals(QObject):
    state_changed = Signal()
    caption_received = Signal(str, str)
    levels_updated = Signal(float, float, float)


class CallPanelWidget(_TopmostWindowMixin, QWidget):
    """Floating cyberpunk Call & Meeting HUD."""

    def __init__(self, companion, settings: dict):
        flags = Qt.FramelessWindowHint | Qt.Tool | Qt.NoDropShadowWindowHint
        if settings.get("always_on_top", True):
            flags |= Qt.WindowStaysOnTopHint
        super().__init__(None, flags)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.companion = companion
        self.settings = settings
        self.session = get_call_session()

        self._minimized = False
        self._ripple_phase = 0.0

        # UI Layout
        self._init_ui()

        # Thread-safe signal bridge for background audio thread callbacks
        self._bus = _CallPanelSignals()
        self._bus.state_changed.connect(self._on_session_state)
        self._bus.caption_received.connect(self._on_caption)
        self._bus.levels_updated.connect(self._on_levels)

        self.session.add_state_listener(self._bus.state_changed.emit)
        self.session.add_caption_listener(self._bus.caption_received.emit)
        self.session.add_levels_listener(self._bus.levels_updated.emit)

        # 30 FPS animation & clock timer
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(33)
        self._anim_timer.timeout.connect(self._on_tick)
        self._anim_timer.start()

        self.resize(320, 110)
        self.hide()

    def _init_ui(self):
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(10, 8, 10, 8)
        self.main_layout.setSpacing(6)

        # Top bar: App name + Call clock + Min button + End Call button
        self.top_row = QHBoxLayout()
        self.top_row.setSpacing(8)

        self.title_label = QLabel("DISCORD CALL", self)
        self.title_label.setStyleSheet("font-weight: 800; font-size: 11px; letter-spacing: 1px;")

        self.clock_label = QLabel("00:00", self)
        self.clock_label.setStyleSheet("font-family: monospace; font-weight: 700; font-size: 11px;")

        self.min_btn = QPushButton("—", self)
        self.min_btn.setFixedSize(20, 20)
        self.min_btn.setCursor(Qt.PointingHandCursor)
        self.min_btn.clicked.connect(self.toggle_minimize)

        self.end_btn = QPushButton("END", self)
        self.end_btn.setFixedHeight(22)
        self.end_btn.setCursor(Qt.PointingHandCursor)
        self.end_btn.clicked.connect(self._on_end_clicked)

        self.top_row.addWidget(self.title_label)
        self.top_row.addWidget(self.clock_label)
        self.top_row.addStretch()
        self.top_row.addWidget(self.min_btn)
        self.top_row.addWidget(self.end_btn)
        self.main_layout.addLayout(self.top_row)

        # Caption label
        self.caption_label = QLabel("Waiting for speech...", self)
        self.caption_label.setWordWrap(True)
        self.caption_label.setStyleSheet("font-size: 11px; font-weight: 500; min-height: 28px;")
        self.main_layout.addWidget(self.caption_label)

    def _on_session_state(self):
        if self.session.enabled:
            self.title_label.setText(f"{self.session.app_name.upper()} CALL")
            self.reposition()
            self.show()
            self.raise_()
        else:
            self.hide()

    def _on_caption(self, speaker: str, text: str):
        if not text:
            return
        prefix = f"<b>{speaker}:</b> " if speaker else ""
        self.caption_label.setText(f"{prefix}{text}")

    def _on_levels(self, you: float, her: float, call: float):
        self.update()

    def _on_tick(self):
        if not self.isVisible():
            return
        # Update clock
        secs = int(self.session.call_duration)
        mins = secs // 60
        s = secs % 60
        self.clock_label.setText(f"{mins:02d}:{s:02d}")

        # Advance ripple phase
        self._ripple_phase = (self._ripple_phase + 0.08) % (2.0 * math.pi)
        self.update()

    def _on_end_clicked(self):
        self.session.end_call()

    def toggle_minimize(self):
        self._minimized = not self._minimized
        if self._minimized:
            self.caption_label.hide()
            self.resize(220, 38)
            self.min_btn.setText("+")
        else:
            self.caption_label.show()
            self.resize(320, 110)
            self.min_btn.setText("—")
        self.reposition()

    def reposition(self):
        cw = self.companion
        if cw is not None:
            x = cw.x() + cw.width() + 12
            y = cw.y()
            self.move(x, max(20, y))

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        t = _theme(self.settings)
        bg = QColor(14, 18, 28, 235)
        border = QColor(t.get("accent", "#6366F1"))
        border.setAlpha(120)

        # Draw panel container card
        painter.setPen(QPen(border, 1.2))
        painter.setBrush(bg)
        painter.drawRoundedRect(QRectF(1, 1, self.width() - 2, self.height() - 2), 8, 8)

        # Style buttons and labels
        accent = t.get("accent", "#6366F1")
        self.title_label.setStyleSheet(f"color: {accent}; font-weight: 800; font-size: 10px; letter-spacing: 1px;")
        self.clock_label.setStyleSheet(f"color: {t.get('text', '#F8FAFC')}; font-family: monospace; font-weight: 700; font-size: 11px;")
        self.caption_label.setStyleSheet(f"color: {t.get('text', '#F8FAFC')}; font-size: 11px; font-weight: 500;")
        self.min_btn.setStyleSheet(f"""
            QPushButton {{
                background: {_rgba(t['card'], 0.8)};
                color: {t['text']};
                border: 1px solid {_rgba(t['border'], 0.7)};
                border-radius: 4px;
                font-weight: 700;
            }}
            QPushButton:hover {{ border-color: {accent}; }}
        """)
        self.end_btn.setStyleSheet("""
            QPushButton {
                background: #D8283F;
                color: #FFFFFF;
                border: none;
                border-radius: 4px;
                font-weight: 800;
                font-size: 9px;
                padding: 2px 8px;
            }
            QPushButton:hover { background: #EF4444; }
        """)

        # If not minimized, draw the three animated speaker audio rings:
        # Ring 0: You (Cyan/Green), Ring 1: Her (Purple/Pink), Ring 2: Call (Amber/Red)
        if not self._minimized:
            rings_y = 96.0
            spacing = 90.0
            start_x = 45.0

            speakers = [
                ("YOU", self.session.level_you, QColor(16, 185, 129)),     # Emerald
                ("ALYSSA", self.session.level_her, QColor(168, 85, 247)),  # Purple
                ("CALL", self.session.level_call, QColor(245, 158, 11)),   # Amber
            ]

            font = painter.font()
            font.setPointSize(8)
            font.setBold(True)
            painter.setFont(font)

            for i, (name, lvl, color) in enumerate(speakers):
                cx = start_x + i * spacing
                cy = rings_y

                # Base dot
                base_r = 4.0
                painter.setPen(Qt.NoPen)
                painter.setBrush(color)
                painter.drawEllipse(QPointF(cx, cy), base_r, base_r)

                # Reactive pulse ripple
                if lvl > 0.08:
                    ripple_r = base_r + lvl * 14.0 + math.sin(self._ripple_phase) * 2.0
                    ripple_col = QColor(color)
                    ripple_col.setAlpha(int(max(20, min(180, lvl * 200))))
                    painter.setPen(QPen(ripple_col, 1.5))
                    painter.setBrush(Qt.NoBrush)
                    painter.drawEllipse(QPointF(cx, cy), ripple_r, ripple_r)

                # Label text
                painter.setPen(QColor(160, 174, 192))
                painter.drawText(QRectF(cx - 30, cy - 18, 60, 14), Qt.AlignCenter, name)
