"""The small windows around her: the chat bar, the hover pill and the mini player."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QLineEdit, QLabel,
)

import config

from . import motion
from .theming import _rgba, _theme
import startup_logging

from .topmost import _TopmostWindowMixin

_CHATBAR_WIDTH = 230


_CHATBAR_HEIGHT = 36


_CHATBAR_GAP = 6  # px between her and the bar


class ChatInputBar(_TopmostWindowMixin, QWidget):
    def __init__(self, companion, settings: dict, bridge):
        flags = Qt.FramelessWindowHint | Qt.Tool | Qt.NoDropShadowWindowHint
        if settings.get("always_on_top", True):
            flags |= Qt.WindowStaysOnTopHint
        super().__init__(
            None,
            flags,
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.companion = companion
        self.settings = settings
        self.bridge = bridge

        self.edit = QLineEdit(self)
        self.edit.returnPressed.connect(self._submit)
        self.edit.setContentsMargins(0, 0, 0, 0)
        self.edit.setMinimumHeight(32)
        self._update_placeholder()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addWidget(self.edit)

        self.resize(_CHATBAR_WIDTH, _CHATBAR_HEIGHT)
        self.apply_theme()


    def _update_placeholder(self):
        name = getattr(config, "ASSISTANT_NAME", "her")
        self.edit.setPlaceholderText(f"Type to {name}...")

    def apply_theme(self):
        """Re-apply light/dark styling (creation + Settings dark_mode change)."""
        t = _theme(self.settings)
        self.edit.setStyleSheet(f"""
            QLineEdit {{
                background: {_rgba(t['card'], 0.96)};
                border: 1.5px solid {_rgba(t['border'], 0.9)};
                border-radius: 16px;
                padding: 6px 14px;
                color: {t['text']};
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI Variable Display", "Segoe UI", "Inter", Roboto, sans-serif;
                font-size: 12px;
                font-weight: 500;
                selection-background-color: {t['accent']};
            }}
            QLineEdit:focus {{
                border: 1.5px solid {t['accent']};
                background: {t['card']};
            }}
        """)
        self._update_placeholder()

    def _submit(self):
        text = self.edit.text().strip()
        if not text:
            return
        self.edit.clear()
        self.bridge.text_queue.put(text)

    def apply_enabled(self):
        """Show/hide per settings['chatbox_enabled']."""
        if self.settings.get("chatbox_enabled", True):
            self.reposition()
            motion.fade_in(self)
        else:
            motion.fade_out(self)

    def reposition(self):
        """Re-anchor to companion: chatbox_position is bottom/left/right. Clamp to screen; do not flip sides."""
        cw = self.companion
        side = self.settings.get("chatbox_position", "bottom")

        # Same None-screen fallback as SpeechBubble._compute_position.
        screen = (
            QGuiApplication.screenAt(cw.pos())
            or QGuiApplication.primaryScreen()
            or (QGuiApplication.screens()[0] if QGuiApplication.screens() else None)
        )
        avail = screen.availableGeometry() if screen is not None else None

        if side == "left":
            x = cw.x() - self.width() - _CHATBAR_GAP
            y = cw.y() + cw.height() // 2 - self.height() // 2
        elif side == "right":
            x = cw.x() + cw.width() + _CHATBAR_GAP
            y = cw.y() + cw.height() // 2 - self.height() // 2
        else:  # "bottom" (also the fallback for any unrecognized value)
            x = cw.x() + cw.width() // 2 - self.width() // 2
            y = cw.y() + cw.height() + _CHATBAR_GAP

        # Clamp to screen; do not flip sides (would overlap the speech bubble above her).
        if avail is not None:
            x = max(avail.left(), min(x, avail.right() - self.width()))
            y = max(avail.top(), min(y, avail.bottom() - self.height()))
        self.move(x, y)


class HoverPill(_TopmostWindowMixin, QWidget):
    """Compact quick-action pill (TYPE / QUIET / RIDE) anchored near the companion."""

    def __init__(self, companion, settings: dict):
        flags = Qt.FramelessWindowHint | Qt.Tool | Qt.NoDropShadowWindowHint
        if settings.get("always_on_top", True):
            flags |= Qt.WindowStaysOnTopHint
        super().__init__(None, flags)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.companion = companion
        self.settings = settings

        from PySide6.QtWidgets import QPushButton

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(4)

        self.heart_label = QLabel(self)
        layout.addWidget(self.heart_label)
        self.type_btn = QPushButton("TYPE", self)
        self.quiet_btn = QPushButton("QUIET", self)
        self.ride_btn = QPushButton("RIDE", self)
        self.chat_btn = QPushButton("CHAT", self)
        self.chat_btn.setToolTip("Chat history")

        for btn in (self.type_btn, self.chat_btn, self.quiet_btn, self.ride_btn):
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(22)
            layout.addWidget(btn)

        self.type_btn.clicked.connect(self._on_type_clicked)
        self.quiet_btn.clicked.connect(self._on_quiet_clicked)
        self.ride_btn.clicked.connect(self._on_ride_clicked)
        self.chat_btn.clicked.connect(self._on_chat_clicked)

        self.resize(262, 30)
        self.refresh_state()
        self.hide()

    def _on_type_clicked(self):
        if self.companion.chatbar is not None:
            self.companion.chatbar.show()
            self.companion.chatbar.raise_()
            self.companion.chatbar.activateWindow()
            self.companion.chatbar.edit.setFocus()

    def _on_chat_clicked(self):
        from .chat_history import open_for
        open_for(self.companion)

    def _on_quiet_clicked(self):
        config.QUIET_MODE = not bool(getattr(config, "QUIET_MODE", False))
        self.refresh_state()

    def _on_ride_clicked(self):
        self.companion.toggle_cursor_ride()
        self.refresh_state()

    def refresh_state(self):
        quiet = bool(getattr(config, "QUIET_MODE", False))
        riding = bool(getattr(self.companion, "_riding_cursor", False))
        self.quiet_btn.setText("QUIET: ON" if quiet else "QUIET")
        self.ride_btn.setText("HOP OFF" if riding else "RIDE")
        self._refresh_heart()
        self.apply_theme()

    def _refresh_heart(self):
        import fondness
        value = fondness.score()
        self.heart_label.setText(f"♥ {value}")
        self.heart_label.setToolTip(f"Fondness: {fondness.level(value)[0]} ({value}/100)")

    def apply_theme(self):
        t = _theme(self.settings)
        quiet = bool(getattr(config, "QUIET_MODE", False))
        accent = t["accent"]
        self.setStyleSheet(f"""
            QPushButton {{
                background: {_rgba(t['card'], 0.94)};
                border: 1px solid {_rgba(t['border'], 0.85)};
                border-radius: 9px;
                color: {t['text']};
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI Variable Text", "Segoe UI", "Inter", sans-serif;
                font-size: 9px;
                font-weight: 700;
                letter-spacing: 0.4px;
                padding: 2px 7px;
            }}
            QPushButton:hover {{
                border: 1.5px solid {accent};
                background: {_rgba(accent, 0.12)};
            }}
            QPushButton:pressed {{
                background: {_rgba(accent, 0.22)};
            }}
            QLabel {{
                background: {_rgba(t['card'], 0.94)};
                border: 1px solid {_rgba(t['border'], 0.85)};
                border-radius: 9px;
                color: {accent};
                font-size: 9px;
                font-weight: 700;
                padding: 2px 7px;
            }}
        """)
        if quiet:
            self.quiet_btn.setStyleSheet(
                f"""
                QPushButton {{
                    background: {accent};
                    border: 1px solid {accent};
                    color: #ffffff;
                    border-radius: 9px;
                    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI Variable Text", "Segoe UI", "Inter", sans-serif;
                    font-size: 9px;
                    font-weight: 700;
                    letter-spacing: 0.4px;
                    padding: 2px 7px;
                }}
                QPushButton:hover {{
                    background: {_rgba(accent, 0.9)};
                }}
                """
            )

    def reposition(self):
        # Over her lower edge: the space above her belongs to the speech bubble.
        cw = self.companion
        x = cw.x() + cw.width() // 2 - self.width() // 2
        y = cw.y() + cw.height() - self.height() - 10
        self.move(x, max(0, y))

    def enterEvent(self, event):
        super().enterEvent(event)
        self.companion.keep_hover_pill()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.companion.release_hover_pill()


class MiniPlayerWidget(_TopmostWindowMixin, QWidget):
    """Cyberpunk mini music player bar that docks near the companion."""

    def __init__(self, companion, settings: dict):
        flags = Qt.FramelessWindowHint | Qt.Tool | Qt.NoDropShadowWindowHint
        if settings.get("always_on_top", True):
            flags |= Qt.WindowStaysOnTopHint
        super().__init__(None, flags)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.companion = companion
        self.settings = settings

        from PySide6.QtWidgets import QPushButton

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        self.status_label = QLabel("♪ AUDIO DECK", self)
        self.prev_btn = QPushButton("⏮", self)
        self.play_btn = QPushButton("⏯", self)
        self.next_btn = QPushButton("⏭", self)

        for btn in (self.prev_btn, self.play_btn, self.next_btn):
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedSize(26, 24)

        layout.addWidget(self.status_label, 1)
        layout.addWidget(self.prev_btn)
        layout.addWidget(self.play_btn)
        layout.addWidget(self.next_btn)

        self.prev_btn.clicked.connect(self._on_prev)
        self.play_btn.clicked.connect(self._on_play_pause)
        self.next_btn.clicked.connect(self._on_next)

        self.resize(220, 36)
        self.apply_theme()
        self.hide()

    def _on_prev(self):
        try:
            import actions
            actions.media_previous_track()
            self.status_label.setText("♪ PREV TRACK")
        except Exception as e:
            startup_logging.warn_once("mini player prev", e)

    def _on_play_pause(self):
        try:
            import actions
            actions.media_play_pause()
            self.status_label.setText("♪ PLAY / PAUSE")
        except Exception as e:
            startup_logging.warn_once("mini player play/pause", e)

    def _on_next(self):
        try:
            import actions
            actions.media_next_track()
            self.status_label.setText("♪ NEXT TRACK")
        except Exception as e:
            startup_logging.warn_once("mini player next", e)

    def apply_theme(self):
        t = _theme(self.settings)
        accent = t["accent"]
        self.setStyleSheet(f"""
            QLabel {{
                background: {_rgba(t['card'], 0.94)};
                border: 1px solid {_rgba(t['border'], 0.85)};
                border-radius: 12px;
                color: {t['text']};
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI Variable Text", "Segoe UI", "Inter", sans-serif;
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 0.5px;
                padding: 3px 8px;
            }}
            QPushButton {{
                background: {_rgba(t['card'], 0.94)};
                border: 1px solid {_rgba(t['border'], 0.85)};
                border-radius: 12px;
                color: {t['text']};
                font-size: 11px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                border: 1.5px solid {accent};
                background: {_rgba(accent, 0.12)};
            }}
            QPushButton:pressed {{
                background: {_rgba(accent, 0.22)};
            }}
        """)

    def apply_enabled(self):
        if self.settings.get("mini_player_enabled", False):
            self.reposition()
            motion.fade_in(self)
        else:
            motion.fade_out(self)

    def reposition(self):
        cw = self.companion
        x = cw.x() + cw.width() // 2 - self.width() // 2
        offset_y = cw.y() + cw.height() + (_CHATBAR_HEIGHT + 12 if self.settings.get("chatbox_enabled", True) else 6)
        self.move(x, offset_y)
