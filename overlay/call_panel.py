"""Call Mode HUD overlay.

Inspired by Capri Desktop's `CallPanel.cs`:
- Header with a live dot, call app/channel, duration clock, minimize and hang-up.
- Three audio-reactive speaker rings: You (mic), Alyssa (voice), Call (friends).
- Live captions of who said what.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

import config
from call_ears import get_call_session
from .widgets import _TopmostWindowMixin
from . import motion
from .theming import _rgba, _theme

_PANEL_WIDTH = 300
_FONT_STACK = '-apple-system, BlinkMacSystemFont, "Segoe UI Variable Text", "Segoe UI", "Inter", sans-serif'


class _CallPanelSignals(QObject):
    state_changed = Signal()
    caption_received = Signal(str, str)
    levels_updated = Signal(float, float, float)


class _SpeakerRings(QWidget):
    """Three speaker dots with level-driven ripples, laid out evenly."""

    def __init__(self, panel: "CallPanelWidget"):
        super().__init__(panel)
        self.panel = panel
        self.setFixedHeight(54)

    def paintEvent(self, _event):
        t = _theme(self.panel.settings)
        session = self.panel.session
        speakers = [
            ("You", session.level_you, QColor(t.get("success", "#10B981"))),
            (getattr(config, "ASSISTANT_NAME", "Alyssa"), session.level_her, QColor(t.get("accent", "#6366F1"))),
            ("Call", session.level_call, QColor(t.get("warning", "#F59E0B"))),
        ]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        font = painter.font()
        font.setPixelSize(10)
        font.setBold(True)
        painter.setFont(font)

        slot_w = self.width() / len(speakers)
        cy = 20.0
        phase = self.panel._ripple_phase
        for i, (name, lvl, color) in enumerate(speakers):
            cx = slot_w * (i + 0.5)
            # Soft halo + solid dot
            halo = QColor(color)
            halo.setAlpha(45)
            painter.setPen(Qt.NoPen)
            painter.setBrush(halo)
            painter.drawEllipse(QPointF(cx, cy), 11.0, 11.0)
            painter.setBrush(color)
            painter.drawEllipse(QPointF(cx, cy), 6.0, 6.0)

            if lvl > 0.08:
                ripple = QColor(color)
                ripple.setAlpha(int(max(40, min(200, lvl * 220))))
                painter.setPen(QPen(ripple, 1.6))
                painter.setBrush(Qt.NoBrush)
                r = 11.0 + lvl * 6.0 + math.sin(phase) * 1.5
                painter.drawEllipse(QPointF(cx, cy), r, r)

            painter.setPen(QColor(t.get("subtext", "#94A3B8")))
            painter.drawText(QRectF(cx - slot_w / 2, cy + 15, slot_w, 16), Qt.AlignHCenter | Qt.AlignTop, name)
        painter.end()


class CallPanelWidget(_TopmostWindowMixin, QWidget):
    """Floating Call & Meeting HUD that docks beside the companion."""

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

        self._init_ui()
        self.apply_theme()

        # Session callbacks fire on worker/audio threads; the signals queue them onto the GUI thread.
        self._bus = _CallPanelSignals()
        self._bus.state_changed.connect(self._on_session_state)
        self._bus.caption_received.connect(self._on_caption)
        self._bus.levels_updated.connect(self._on_levels)

        self.session.add_state_listener(self._bus.state_changed.emit)
        self.session.add_caption_listener(self._bus.caption_received.emit)
        self.session.add_levels_listener(self._bus.levels_updated.emit)

        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(33)
        self._anim_timer.timeout.connect(self._on_tick)
        self._anim_timer.start()

        self.setFixedWidth(_PANEL_WIDTH)
        self.adjustSize()
        self.hide()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 12, 12)
        layout.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(6)
        self.live_dot = QLabel("●", self)
        self.title_label = QLabel("Discord call", self)
        self.title_label.setObjectName("callTitle")
        self.clock_label = QLabel("00:00", self)
        self.clock_label.setObjectName("callClock")

        self.min_btn = QPushButton("–", self)
        self.min_btn.setObjectName("callMin")
        self.min_btn.setFixedSize(24, 24)
        self.min_btn.setCursor(Qt.PointingHandCursor)
        self.min_btn.setToolTip("Minimize")
        self.min_btn.clicked.connect(self.toggle_minimize)

        self.end_btn = QPushButton("Leave", self)
        self.end_btn.setObjectName("callEnd")
        self.end_btn.setFixedHeight(24)
        self.end_btn.setCursor(Qt.PointingHandCursor)
        self.end_btn.setToolTip("Leave the call")
        self.end_btn.clicked.connect(self._on_end_clicked)

        top.addWidget(self.live_dot)
        top.addWidget(self.title_label, 1)
        top.addWidget(self.clock_label)
        top.addSpacing(4)
        top.addWidget(self.min_btn)
        top.addWidget(self.end_btn)
        layout.addLayout(top)

        self.rings = _SpeakerRings(self)
        layout.addWidget(self.rings)

        self.caption_label = QLabel("Listening to the call…", self)
        self.caption_label.setObjectName("callCaption")
        self.caption_label.setWordWrap(True)
        self.caption_label.setTextFormat(Qt.RichText)
        layout.addWidget(self.caption_label)

    def apply_theme(self):
        """Style child widgets once (never from paintEvent: restyling there re-triggers paints forever)."""
        t = _theme(self.settings)
        accent = t["accent"]
        danger = t.get("danger", "#EF4444")
        self.setStyleSheet(f"""
            QLabel {{
                background: transparent;
                color: {t['text']};
                font-family: {_FONT_STACK};
            }}
            QLabel#callTitle {{ font-size: 12px; font-weight: 700; }}
            QLabel#callClock {{
                color: {t['subtext']};
                font-family: Consolas, "Cascadia Mono", monospace;
                font-size: 11px;
            }}
            QLabel#callCaption {{
                background: {_rgba(t['card'], 0.9)};
                border: 1px solid {_rgba(t['border'], 0.8)};
                border-radius: 8px;
                padding: 6px 8px;
                font-size: 11px;
            }}
            QPushButton#callMin {{
                background: {_rgba(t['card'], 0.9)};
                color: {t['text']};
                border: 1px solid {_rgba(t['border'], 0.85)};
                border-radius: 12px;
                font-size: 12px;
                font-weight: 700;
            }}
            QPushButton#callMin:hover {{ border-color: {accent}; background: {_rgba(accent, 0.15)}; }}
            QPushButton#callEnd {{
                background: {danger};
                color: #FFFFFF;
                border: none;
                border-radius: 12px;
                font-family: {_FONT_STACK};
                font-size: 11px;
                font-weight: 700;
                padding: 0 12px;
            }}
            QPushButton#callEnd:hover {{ background: {_rgba(danger, 0.85)}; }}
        """)
        self.live_dot.setStyleSheet(f"color: {t.get('success', '#10B981')}; font-size: 10px;")
        self.update()

    def _on_session_state(self):
        if self.session.enabled:
            app = self.session.app_name or "Discord"
            channel = self.session.channel_name
            self.title_label.setText(f"{app} · #{channel}" if channel else f"{app} call")
            self.caption_label.setText("Listening to the call…")
            self.reposition()
            motion.fade_in(self)
            self.raise_()
        else:
            motion.fade_out(self)

    def _on_caption(self, speaker: str, text: str):
        if not text:
            return
        from html import escape
        prefix = f"<b>{escape(speaker)}:</b> " if speaker else ""
        self.caption_label.setText(f"{prefix}{escape(text)}")
        self.adjustSize()

    def _on_levels(self, _you: float, _her: float, _call: float):
        self.rings.update()

    def _on_tick(self):
        if not self.isVisible():
            return
        secs = int(self.session.call_duration)
        self.clock_label.setText(f"{secs // 60:02d}:{secs % 60:02d}")
        self._ripple_phase = (self._ripple_phase + 0.08) % (2.0 * math.pi)
        if not self._minimized:
            self.rings.update()

    def _on_end_clicked(self):
        self.session.end_call()

    def toggle_minimize(self):
        self._minimized = not self._minimized
        self.rings.setVisible(not self._minimized)
        self.caption_label.setVisible(not self._minimized)
        self.min_btn.setText("+" if self._minimized else "–")
        self.min_btn.setToolTip("Expand" if self._minimized else "Minimize")
        self.adjustSize()
        self.reposition()

    def reposition(self):
        cw = self.companion
        if cw is None:
            return
        x = cw.x() + cw.width() + 12
        y = max(20, cw.y())
        screen = cw.screen() if hasattr(cw, "screen") else None
        if screen is not None:
            area = screen.availableGeometry()
            if x + self.width() > area.right():
                x = cw.x() - self.width() - 12  # no room on the right: dock on the left
            y = min(y, area.bottom() - self.height() - 8)
        self.move(x, y)

    def paintEvent(self, _event):
        t = _theme(self.settings)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        border = QColor(t["accent"])
        border.setAlpha(150)
        painter.setPen(QPen(border, 1.2))
        bg = QColor(t.get("panel", t["bg"]))
        bg.setAlpha(242)
        painter.setBrush(bg)
        painter.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), 12, 12)
        painter.end()
