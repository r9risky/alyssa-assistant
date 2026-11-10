import math
import queue
import random
import sys

from PySide6.QtCore import (
    Qt, QTimer, QPoint, QSize, Signal, QObject, QRect,
    QPropertyAnimation, QEasingCurve, QParallelAnimationGroup,
)
from PySide6.QtGui import (
    QPainter, QColor, QGuiApplication,
    QPainterPath, QLinearGradient, QFont,
)
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QLineEdit, QLabel,
)

import config

from .rendering import BASE_W, BASE_H, MIN_W, save_overlay_settings
from .theming import _rgba, _theme
from .companion.rendering_mixin import RenderingMixin
from .companion.interaction_mixin import InteractionMixin
from .companion.window_mixin import WindowMixin
from .companion.settings_mixin import SettingsMixin
from .companion.talk_state_mixin import TalkStateMixin

class Bridge(QObject):
    """Thread-safe bus: assistant loop (background) → GUI (main thread) via these signals only."""

    speak_signal = Signal(str)
    error_signal = Signal(str)
    gemini_key_needed = Signal()
    reply_pending_signal = Signal()  # reply is visible; audio is still being synthesized
    talk_start_signal = Signal()  # emitted right before she starts talking
    talk_end_signal = Signal()  # emitted right after she finishes talking
    thinking_signal = Signal()  # emitted right after a command is captured, before the model replies
    mood_signal = Signal(str)  # emitted to set companion emotional mood ("happy", "surprised", etc.)

    def __init__(self):
        super().__init__()
        # ChatInputBar → main.run_assistant_loop (background poll, not a Qt Signal).
        self.text_queue = queue.Queue()


class _TopmostWindowMixin:
    """Re-apply the Windows topmost flag after a tool window is shown."""

    def showEvent(self, event):
        super().showEvent(event)
        if sys.platform == "win32":
            QTimer.singleShot(
                0,
                lambda: self.companion._apply_windows_topmost(
                    self, bool(self.settings.get("always_on_top", True))
                ),
            )


_BUBBLE_TAIL_H = 12  # px, height of the little triangle pointing at her


_BUBBLE_RADIUS = 6


_BUBBLE_PAD_X = 16


_BUBBLE_PAD_Y = 12


class SpeechBubble(_TopmostWindowMixin, QWidget):
    def __init__(self, companion, settings: dict):
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
        self._tail_x = 0  # tail tip position, in this widget's local coords
        self._tail_on_top = True  # tail points down (bubble above her) by default
        self._full_text = ""
        self._pages: list[str] = []
        self._page_index = 0

        self.label = QLabel("", self)
        self.label.setWordWrap(True)
        self.label.setMaximumWidth(250)
        font = QFont("Segoe UI", 10)
        font.setWeight(QFont.Medium)
        self.label.setFont(font)
        _t0 = _theme(settings)
        self.label.setStyleSheet(f"QLabel {{ background: transparent; color: {_t0['bubble_text']}; }}")
        self.label.move(_BUBBLE_PAD_X, _BUBBLE_PAD_Y)

        # Linger starts on talk_end; bubble_seconds is linger, not total display time.
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._fade_out)
        # Safety net if talk_end never arrives (e.g. TTS throws before finally).
        self._safety_timer = QTimer(self)
        self._safety_timer.setSingleShot(True)
        self._safety_timer.timeout.connect(self._fade_out)

        self._opacity_anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._pos_anim = QPropertyAnimation(self, b"pos", self)
        self._show_group = QParallelAnimationGroup(self)
        self._show_group.addAnimation(self._opacity_anim)
        self._show_group.addAnimation(self._pos_anim)
        # Track the one-shot fade-out callback; disconnect only when attached (PySide warns otherwise).
        self._hide_on_fade_finished = False

        self._talking = False
        self.setWindowOpacity(0.0)
        self.hide()

        # Thinking dots until show_message; driven by thinking_signal.
        self._thinking = False
        self._thinking_dots = 0
        self._thinking_timer = QTimer(self)
        self._thinking_timer.timeout.connect(self._on_thinking_tick)

    @staticmethod
    def _paginate_text(text: str, max_chars: int = 220) -> list[str]:
        text = (text or "").strip()
        if len(text) <= max_chars:
            return [text] if text else []
        words = text.split()
        pages: list[str] = []
        current: list[str] = []
        current_len = 0
        for w in words:
            add_len = len(w) + (1 if current else 0)
            if current and current_len + add_len > max_chars:
                pages.append(" ".join(current))
                current = [w]
                current_len = len(w)
            else:
                current.append(w)
                current_len += add_len
        if current:
            pages.append(" ".join(current))
        return pages or [text]

    def _format_current_page(self) -> str:
        if not self._pages:
            return self._full_text
        page_text = self._pages[self._page_index]
        if len(self._pages) > 1:
            return f"{page_text}\n({self._page_index + 1}/{len(self._pages)})"
        return page_text

    def next_page(self) -> bool:
        if len(self._pages) <= 1 or self._page_index >= len(self._pages) - 1:
            return False
        self._page_index += 1
        self._render_current_page()
        return True

    def prev_page(self) -> bool:
        if len(self._pages) <= 1 or self._page_index <= 0:
            return False
        self._page_index -= 1
        self._render_current_page()
        return True

    def _render_current_page(self):
        self.label.setText(self._format_current_page())
        self.label.adjustSize()
        self._resize_to_content()
        self.reposition()

    def copy_to_clipboard(self) -> str:
        """Copy the full bubble reply to the clipboard and briefly display `> sys: copied`."""
        if not self._full_text:
            return ""
        cb = QGuiApplication.clipboard()
        if cb is not None:
            cb.setText(self._full_text)
        self.label.setText("> sys: copied")
        self.label.adjustSize()
        self._resize_to_content()
        self.reposition()
        QTimer.singleShot(900, self._render_current_page)
        return self._full_text

    def wheelEvent(self, event):
        if len(self._pages) > 1:
            if event.angleDelta().y() < 0:
                self.next_page()
            elif event.angleDelta().y() > 0:
                self.prev_page()
            event.accept()
            return
        super().wheelEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            self.copy_to_clipboard()
            event.accept()
            return
        if event.button() == Qt.LeftButton and len(self._pages) > 1:
            if not self.next_page():
                self._page_index = 0
                self._render_current_page()
            event.accept()
            return
        super().mousePressEvent(event)

    # -- showing / hiding, driven by speech ------------------------------
    def show_thinking(self):
        try:
            self._show_thinking_impl()
        except Exception:
            import traceback
            print("[bubble ERROR] show_thinking raised an exception:")
            traceback.print_exc()

    def _show_thinking_impl(self):
        self._hide_timer.stop()
        self._safety_timer.stop()
        self._show_group.stop()
        self._thinking = True
        self._thinking_dots = 0

        t = _theme(self.settings)
        self.label.setStyleSheet(f"QLabel {{ background: transparent; color: {t['bubble_text']}; }}")
        self.label.setText("Thinking")
        self.label.adjustSize()
        self._resize_to_content()
        target_pos = self._compute_position()

        start_offset = -14 if self._tail_on_top else 14
        self.move(target_pos.x(), target_pos.y() + start_offset)
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()

        self._opacity_anim.stop()
        self._clear_fade_hide_callback()
        self._opacity_anim.setDuration(220)
        self._opacity_anim.setStartValue(0.0)
        self._opacity_anim.setEndValue(1.0)
        self._opacity_anim.setEasingCurve(QEasingCurve.OutCubic)

        self._pos_anim.stop()
        self._pos_anim.setDuration(220)
        self._pos_anim.setStartValue(QPoint(target_pos.x(), target_pos.y() + start_offset))
        self._pos_anim.setEndValue(target_pos)
        self._pos_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._show_group.start()

        # Stay until show_message clears _thinking; long safety net if upstream hangs.
        self._thinking_timer.start(450)
        seconds = max(1, int(self.settings.get("bubble_seconds", 7)))
        self._safety_timer.start(max(20, seconds * 6) * 1000)

    def _on_thinking_tick(self):
        if not self._thinking:
            self._thinking_timer.stop()
            return
        self._thinking_dots = (self._thinking_dots + 1) % 4
        self.label.setText("Thinking" + "." * self._thinking_dots)
        self.label.adjustSize()
        self._resize_to_content()

    def show_message(self, text: str):
        try:
            self._show_message_impl(text)
        except Exception:
            import traceback
            print("[bubble ERROR] show_message raised an exception:")
            traceback.print_exc()

    def _clear_fade_hide_callback(self):
        """Remove the one-shot fade-out callback without Qt warnings."""
        if self._hide_on_fade_finished:
            self._opacity_anim.finished.disconnect(self.hide)
            self._hide_on_fade_finished = False

    def _show_message_impl(self, text: str):
        text = (text or "").strip()
        if not text:
            return
        self._thinking = False
        self._thinking_timer.stop()
        self._hide_timer.stop()
        self._safety_timer.stop()
        self._show_group.stop()

        self._full_text = text
        self._pages = self._paginate_text(text)
        self._page_index = 0

        t = _theme(self.settings)
        self.label.setStyleSheet(f"QLabel {{ background: transparent; color: {t['bubble_text']}; }}")

        self.label.setText(self._format_current_page())
        self.label.adjustSize()
        self._resize_to_content()
        target_pos = self._compute_position()

        start_offset = -14 if self._tail_on_top else 14
        self.move(target_pos.x(), target_pos.y() + start_offset)
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()

        self._opacity_anim.stop()
        # _opacity_anim is also used for fade-out; drop hide-on-finished before fade-in or this completion would hide immediately.
        self._clear_fade_hide_callback()
        self._opacity_anim.setDuration(220)
        self._opacity_anim.setStartValue(0.0)
        self._opacity_anim.setEndValue(1.0)
        self._opacity_anim.setEasingCurve(QEasingCurve.OutCubic)

        self._pos_anim.stop()
        self._pos_anim.setDuration(220)
        self._pos_anim.setStartValue(QPoint(target_pos.x(), target_pos.y() + start_offset))
        self._pos_anim.setEndValue(target_pos)
        self._pos_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._show_group.start()

        # Hide by ~4x linger even if talk_end never fires.
        seconds = max(1, int(self.settings.get("bubble_seconds", 7)))
        self._safety_timer.start(max(8, seconds * 4) * 1000)

        # Not mid-talk (SPEAK_RESPONSES off / shown outside speak()): timed hide so it doesn't linger forever.
        if not self._talking:
            self._hide_timer.start(seconds * 1000)

    def on_talk_start(self):
        """Keep the bubble up for the duration of speech."""
        self._talking = True
        self._hide_timer.stop()
        self._safety_timer.stop()

    def on_reply_pending(self):
        """Keep a newly shown reply visible while TTS is being prepared."""
        self._talking = True
        self._hide_timer.stop()
        self._safety_timer.stop()

    def on_talk_end(self):
        """Start linger → fade, timed from actual talk_end (not from when text first appeared)."""
        self._talking = False
        seconds = max(1, int(self.settings.get("bubble_seconds", 7)))
        self._hide_timer.start(seconds * 1000)

    def _fade_out(self):
        if not self.isVisible():
            return
        self._show_group.stop()
        self._opacity_anim.stop()
        self._opacity_anim.setDuration(320)
        self._opacity_anim.setStartValue(self.windowOpacity())
        self._opacity_anim.setEndValue(0.0)
        self._opacity_anim.setEasingCurve(QEasingCurve.InCubic)
        self._clear_fade_hide_callback()
        self._opacity_anim.finished.connect(self.hide)
        self._hide_on_fade_finished = True
        self._opacity_anim.start()

    def _resize_to_content(self):
        w = self.label.width() + _BUBBLE_PAD_X * 2
        h = self.label.height() + _BUBBLE_PAD_Y * 2 + _BUBBLE_TAIL_H
        self.resize(w, h)

    def _compute_position(self) -> QPoint:
        cw = self.companion
        x = cw.x() + cw.width() // 2 - self.width() // 2
        y = cw.y() - self.height() - 4

        screen = (
            QGuiApplication.screenAt(cw.pos())
            or QGuiApplication.primaryScreen()
            or (QGuiApplication.screens()[0] if QGuiApplication.screens() else None)
        )
        if screen is None:
            # No screens: unclamped companion position rather than crashing the slot.
            self._tail_on_top = True
            self._tail_x = max(_BUBBLE_RADIUS + 6, self.width() // 2)
            self.label.move(_BUBBLE_PAD_X, _BUBBLE_PAD_Y)
            return QPoint(x, y)

        geo = screen.availableGeometry()
        x = max(geo.left(), min(x, geo.right() - self.width()))

        # Always above her (no below-her flip). Clamp y to screen top.
        self._tail_on_top = True
        y = max(geo.top() + 4, y)

        # Tail at her horizontal center, clamped inside the rounded body.
        target_center = cw.x() + cw.width() // 2
        self._tail_x = max(
            _BUBBLE_RADIUS + 6, min(target_center - x, self.width() - _BUBBLE_RADIUS - 6)
        )
        self.label.move(_BUBBLE_PAD_X, _BUBBLE_PAD_Y)
        return QPoint(x, y)

    def reposition(self):
        """Re-anchor to the companion without restarting fade/slide (drag/resize)."""
        if not self.isVisible():
            return
        pos = self._compute_position()
        self.move(pos)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        t = _theme(self.settings)

        body_top = 0 if self._tail_on_top else _BUBBLE_TAIL_H
        body_h = self.height() - _BUBBLE_TAIL_H
        body_rect = QRect(0, body_top, self.width(), body_h)

        path = QPainterPath()
        path.addRoundedRect(
            body_rect.x() + 1, body_rect.y() + 1,
            body_rect.width() - 2, body_rect.height() - 2,
            _BUBBLE_RADIUS, _BUBBLE_RADIUS,
        )

        tail = QPainterPath()
        tip_x = self._tail_x
        if self._tail_on_top:
            base_y = body_rect.bottom() - 1
            tail.moveTo(tip_x - 9, base_y)
            tail.lineTo(tip_x + 9, base_y)
            tail.lineTo(tip_x, base_y + _BUBBLE_TAIL_H)
        else:
            base_y = body_rect.top() + 1
            tail.moveTo(tip_x - 9, base_y)
            tail.lineTo(tip_x + 9, base_y)
            tail.lineTo(tip_x, base_y - _BUBBLE_TAIL_H)
        tail.closeSubpath()
        full = path.united(tail)

        # Manual layered shadow: QGraphicsEffect fights translucent frameless always-on-top windows.
        shadow_r, shadow_g, shadow_b = t["bubble_shadow"]
        for i, alpha in ((6, 12), (4, 18), (2, 26)):
            shadow_path = QPainterPath()
            shadow_path.addRoundedRect(
                body_rect.x() + 1, body_rect.y() + 1 + i,
                body_rect.width() - 2, body_rect.height() - 2,
                _BUBBLE_RADIUS, _BUBBLE_RADIUS,
            )
            painter.fillPath(shadow_path, QColor(shadow_r, shadow_g, shadow_b, alpha))

        gradient = QLinearGradient(0, body_rect.top(), 0, body_rect.bottom())
        gradient.setColorAt(0.0, QColor(*t["bubble_top"]))
        gradient.setColorAt(1.0, QColor(*t["bubble_bottom"]))
        painter.fillPath(full, gradient)
        painter.setPen(QColor(255, 255, 255, 70))
        painter.drawPath(full)
        painter.setPen(QColor(*t["bubble_border"]))
        painter.drawPath(full)


_CHATBAR_WIDTH = 230


_CHATBAR_HEIGHT = 36


_CHATBAR_GAP = 6  # px between her and the bar


class ChatInputBar(_TopmostWindowMixin, QWidget):
    def __init__(self, companion, settings: dict, bridge: Bridge):
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
                background: {_rgba(t['card'], 0.95)};
                border: 1px solid {_rgba(t['border'], 0.8)};
                border-radius: 4px;
                padding: 8px 14px;
                color: {t['text']};
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
            self.show()
        else:
            self.hide()

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

        self.type_btn = QPushButton("TYPE", self)
        self.quiet_btn = QPushButton("QUIET", self)
        self.ride_btn = QPushButton("RIDE", self)

        for btn in (self.type_btn, self.quiet_btn, self.ride_btn):
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(22)
            layout.addWidget(btn)

        self.type_btn.clicked.connect(self._on_type_clicked)
        self.quiet_btn.clicked.connect(self._on_quiet_clicked)
        self.ride_btn.clicked.connect(self._on_ride_clicked)

        self.resize(154, 28)
        self.apply_theme()
        self.hide()

    def _on_type_clicked(self):
        if self.companion.chatbar is not None:
            self.companion.chatbar.show()
            self.companion.chatbar.raise_()
            self.companion.chatbar.activateWindow()
            self.companion.chatbar.edit.setFocus()

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
        self.apply_theme()

    def apply_theme(self):
        t = _theme(self.settings)
        quiet = bool(getattr(config, "QUIET_MODE", False))
        accent = t["accent"]
        self.setStyleSheet(f"""
            QPushButton {{
                background: {_rgba(t['card'], 0.92)};
                border: 1px solid {_rgba(t['border'], 0.85)};
                border-radius: 4px;
                color: {t['text']};
                font-size: 9px;
                font-weight: 700;
                padding: 2px 6px;
            }}
            QPushButton:hover {{
                border: 1px solid {accent};
            }}
        """)
        if quiet:
            self.quiet_btn.setStyleSheet(
                f"background: {accent}; color: #ffffff; border-radius: 4px; font-size: 9px; font-weight: 700; padding: 2px 6px;"
            )

    def reposition(self):
        cw = self.companion
        x = cw.x() + cw.width() // 2 - self.width() // 2
        y = cw.y() - self.height() - 4
        self.move(x, max(0, y))


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

        self.resize(210, 34)
        self.apply_theme()
        self.hide()

    def _on_prev(self):
        try:
            import actions
            actions.media_previous_track()
            self.status_label.setText("♪ PREV TRACK")
        except Exception:
            pass

    def _on_play_pause(self):
        try:
            import actions
            actions.media_play_pause()
            self.status_label.setText("♪ PLAY / PAUSE")
        except Exception:
            pass

    def _on_next(self):
        try:
            import actions
            actions.media_next_track()
            self.status_label.setText("♪ NEXT TRACK")
        except Exception:
            pass

    def apply_theme(self):
        t = _theme(self.settings)
        self.setStyleSheet(f"""
            QLabel {{
                background: {_rgba(t['card'], 0.94)};
                border: 1px solid {_rgba(t['border'], 0.85)};
                border-radius: 4px;
                color: {t['text']};
                font-size: 10px;
                font-weight: 600;
                padding: 2px 6px;
            }}
            QPushButton {{
                background: {_rgba(t['card'], 0.94)};
                border: 1px solid {_rgba(t['border'], 0.85)};
                border-radius: 4px;
                color: {t['text']};
                font-size: 11px;
            }}
            QPushButton:hover {{
                border: 1px solid {t['accent']};
            }}
        """)

    def apply_enabled(self):
        if self.settings.get("mini_player_enabled", False):
            self.reposition()
            self.show()
        else:
            self.hide()

    def reposition(self):
        cw = self.companion
        x = cw.x() + cw.width() // 2 - self.width() // 2
        offset_y = cw.y() + cw.height() + (_CHATBAR_HEIGHT + 12 if self.settings.get("chatbox_enabled", True) else 6)
        self.move(x, offset_y)


_ACTIVE_COMPANION_WINDOW = None


def get_active_companion():
    """Return the active CompanionWindow instance if one exists."""
    return _ACTIVE_COMPANION_WINDOW


get_active_companion_window = get_active_companion


_WANDER_INTERVALS = {
    "rarely": 180.0,
    "sometimes": 75.0,
    "often": 30.0,
    "restless": 12.0,
}


class CompanionWindow(
    RenderingMixin, InteractionMixin, WindowMixin, SettingsMixin, TalkStateMixin, QWidget
):
    def __init__(self, settings: dict, bridge: Bridge):
        global _ACTIVE_COMPANION_WINDOW
        flags = Qt.FramelessWindowHint | Qt.Tool | Qt.NoDropShadowWindowHint
        if settings.get("always_on_top", True):
            flags |= Qt.WindowStaysOnTopHint
        super().__init__(
            None,
            flags,
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.settings = settings
        self.bridge = bridge
        self.bubble = None  # set from run_with_assistant()
        self.chatbar = None  # set from run_with_assistant()
        self.hover_pill = None
        self.mini_player = None
        self.call_panel = None
        self.game_hud = None
        self.tray = None
        _ACTIVE_COMPANION_WINDOW = self

        import time as _time

        self._dragging = False
        self._resizing = False
        self._drag_offset = QPoint()
        self._resize_start_pos = QPoint()
        self._resize_start_size = QSize()
        self._talking = False
        self._mouth_open = False
        self._talk_tick = 0
        self._next_flap_tick = 2
        self._bounce_offset = 0.0
        self._riding_cursor = False
        self._sleeping = False
        self._last_interaction_time = _time.monotonic()
        self._last_wander_time = _time.monotonic()

        w = int(BASE_W * settings.get("scale", 1.0))
        self.resize(max(MIN_W, w), int(max(MIN_W, w) * BASE_H / BASE_W))

        if settings.get("pos_x") is not None and settings.get("pos_y") is not None:
            self.move(int(settings["pos_x"]), int(settings["pos_y"]))
        else:
            self._reset_position_only()

        # QMovie per path, lazy; only the shown GIF plays (not a frozen first frame).
        self._movies: dict = {}
        self._active_movie_path = None

        self._pixmap = None
        self._update_pixmap()
        self._update_effective_opacity()

        self._talk_anim_timer = QTimer(self)
        self._talk_anim_timer.timeout.connect(self._on_talk_tick)

        # Bounce eases independently so settle-down continues after mouth-flap timer stops.
        self._bounce_target = 0.0
        self._bounce_anim_timer = QTimer(self)
        self._bounce_anim_timer.timeout.connect(self._on_bounce_anim_tick)

        # Cursor-ride timer (30 FPS)
        self._cursor_ride_timer = QTimer(self)
        self._cursor_ride_timer.timeout.connect(self._on_cursor_ride_tick)

        # Smooth wander animation
        self._wander_anim = QPropertyAnimation(self, b"pos", self)
        self._wander_anim.setDuration(650)
        self._wander_anim.setEasingCurve(QEasingCurve.InOutQuad)
        self._wander_anim.valueChanged.connect(lambda _v: self._reposition_attachments())

        # Companion life timer (sleep/idle & wandering)
        self._life_timer = QTimer(self)
        self._life_timer.timeout.connect(self._on_life_tick)
        self._life_timer.start(1000)

        # Expressiveness & Animation Physics (breathing, cursor gaze, blinking, mood emotes)
        self._breathing_phase = 0.0
        self._breathing_offset = 0.0
        self._gaze_x_offset = 0.0
        self._gaze_y_offset = 0.0
        self._blink_active = False
        self._blink_tick_countdown = random.randint(70, 160)
        self._current_emote = None
        self._emote_timer = 0
        self._emote_float_phase = 0.0
        self._current_mood = "neutral"

        self._physics_timer = QTimer(self)
        self._physics_timer.timeout.connect(self._on_expressive_physics_tick)
        self._physics_timer.start(33)

    def _reposition_attachments(self):
        if self.bubble:
            self.bubble.reposition()
        if self.chatbar:
            self.chatbar.reposition()
        if self.hover_pill and self.hover_pill.isVisible():
            self.hover_pill.reposition()
        if self.mini_player and self.mini_player.isVisible():
            self.mini_player.reposition()
        if self.call_panel and self.call_panel.isVisible():
            self.call_panel.reposition()
        if self.game_hud and self.game_hud.isVisible():
            self.game_hud.reposition()

    def start_cursor_ride(self) -> str:
        """Attach the companion to ride alongside the mouse cursor."""
        if not self.settings.get("cursor_ride_enabled", True):
            return "Cursor riding is disabled in Settings."
        self._riding_cursor = True
        self._record_user_activity()
        self._on_cursor_ride_tick()
        if not self._cursor_ride_timer.isActive():
            self._cursor_ride_timer.start(33)
        if self.hover_pill is not None:
            self.hover_pill.refresh_state()
        return "Hopped onto your cursor! Press Ctrl+Alt+C or click me to hop off."

    def stop_cursor_ride(self) -> str:
        """Detach the companion from the mouse cursor."""
        self._riding_cursor = False
        self._cursor_ride_timer.stop()
        if self.settings.get("perch_on_windows", True):
            from .ledges import snap_to_nearest_ledge
            sx, sy, _l = snap_to_nearest_ledge(self.x(), self.y(), self.width(), self.height())
            self.move(sx, sy)
        self.settings["pos_x"] = self.x()
        self.settings["pos_y"] = self.y()
        save_overlay_settings(self.settings)
        self._reposition_attachments()
        if self.hover_pill is not None:
            self.hover_pill.refresh_state()
        return "Hopped off your cursor."

    def toggle_cursor_ride(self) -> str:
        if self._riding_cursor:
            return self.stop_cursor_ride()
        return self.start_cursor_ride()

    def _on_cursor_ride_tick(self):
        if not self._riding_cursor:
            self._cursor_ride_timer.stop()
            return
        from PySide6.QtGui import QCursor
        cursor_pos = QCursor.pos()
        target = QPoint(cursor_pos.x() + 18, cursor_pos.y() + 18 - self.height() // 3)
        if target != self.pos():
            self.move(target)
            self._reposition_attachments()

    def come_to_cursor(self) -> str:
        """Move the companion next to the user's current mouse cursor position."""
        from PySide6.QtGui import QCursor
        self._record_user_activity()
        cursor_pos = QCursor.pos()
        target = QPoint(cursor_pos.x() - self.width() // 2, cursor_pos.y() - self.height() // 2)
        self.move(target)
        self.settings["pos_x"] = self.x()
        self.settings["pos_y"] = self.y()
        save_overlay_settings(self.settings)
        self._reposition_attachments()
        return "Right here!"

    def enter_sleep(self) -> str:
        """Put the companion into idle sleep state."""
        if self._sleeping:
            return "Already napping."
        self._sleeping = True
        self.set_mood("sleepy")
        self._emote_timer = -1  # persistent while sleeping
        self._update_effective_opacity()
        if self.bubble and not self._talking:
            self.bubble.show_message("Zzz...")
        return "Taking a quick nap. Say my name or click me to wake me up."

    def wake_up(self) -> str:
        """Wake the companion from idle sleep state."""
        import time as _time
        was_sleeping = self._sleeping
        self._sleeping = False
        self._last_interaction_time = _time.monotonic()
        self.set_mood("sparkle")
        self._update_effective_opacity()
        if was_sleeping and self.bubble and not self._talking:
            self.bubble.show_message("I'm awake!")
        return "I'm awake!"

    def wander_step(self) -> QPoint:
        """Pick a ledge/floor destination and smoothly glide there."""
        import time as _time
        from .ledges import pick_wander_destination
        self._last_wander_time = _time.monotonic()
        dest = pick_wander_destination(
            self.x(),
            self.y(),
            self.width(),
            self.height(),
            perch_on_windows=bool(self.settings.get("perch_on_windows", True)),
        )
        if self.isVisible():
            self._wander_anim.stop()
            self._wander_anim.setStartValue(self.pos())
            self._wander_anim.setEndValue(dest)
            self._wander_anim.start()
        else:
            self.move(dest)
            self._reposition_attachments()
        return dest

    def set_mood(self, mood: str) -> str:
        """Set companion emotional state and display mood emote badge."""
        mood = str(mood or "").strip().lower()
        self._current_mood = mood
        emote_map = {
            "happy": "💖",
            "sparkle": "✨",
            "excited": "✨",
            "thinking": "💭",
            "surprised": "⚡",
            "perked": "⚡",
            "sleepy": "💤",
            "love": "❤️",
            "cool": "😎",
            "music": "🎶",
            "neutral": None,
        }
        emote = emote_map.get(mood, "✨" if mood else None)
        self._current_emote = emote
        self._emote_timer = 90 if emote else 0  # ~3 seconds display
        self._emote_float_phase = 0.0
        self._update_mask()
        self.update()
        return f"Mood set to {mood}."

    def _on_expressive_physics_tick(self):
        if not self.isVisible():
            return

        dt = 0.033
        needs_repaint = False

        # 1. Idle breathing physics
        if self.settings.get("idle_breathing_enabled", True) and not self._talking and not self._dragging:
            freq = 0.7 if self._sleeping else 1.4
            amp = 2.2 if self._sleeping else 1.4
            self._breathing_phase += dt * freq * math.pi
            new_breathing = math.sin(self._breathing_phase) * amp
            if abs(new_breathing - self._breathing_offset) > 0.04:
                self._breathing_offset = new_breathing
                needs_repaint = True
        else:
            if self._breathing_offset != 0.0:
                self._breathing_offset = 0.0
                needs_repaint = True

        # 2. Cursor gaze & parallax tracking
        if (
            self.settings.get("cursor_gaze_enabled", True)
            and not self._sleeping
            and not self._riding_cursor
            and not self._dragging
        ):
            from PySide6.QtGui import QCursor
            cpos = QCursor.pos()
            cx = self.x() + self.width() // 2
            cy = self.y() + self.height() // 3
            dx = cpos.x() - cx
            dy = cpos.y() - cy
            dist = math.hypot(dx, dy)
            if dist > 8.0:
                target_x = max(-3.5, min(3.5, (dx / 250.0) * 3.5))
                target_y = max(-2.0, min(2.0, (dy / 250.0) * 2.0))
            else:
                target_x = 0.0
                target_y = 0.0
            new_gx = self._gaze_x_offset + (target_x - self._gaze_x_offset) * 0.18
            new_gy = self._gaze_y_offset + (target_y - self._gaze_y_offset) * 0.18
            if abs(new_gx - self._gaze_x_offset) > 0.04 or abs(new_gy - self._gaze_y_offset) > 0.04:
                self._gaze_x_offset = new_gx
                self._gaze_y_offset = new_gy
                needs_repaint = True
        else:
            if self._gaze_x_offset != 0.0 or self._gaze_y_offset != 0.0:
                self._gaze_x_offset = 0.0
                self._gaze_y_offset = 0.0
                needs_repaint = True

        # 3. Natural Blinking
        if (
            self.settings.get("blinking_enabled", True)
            and not self._sleeping
            and not self._talking
        ):
            self._blink_tick_countdown -= 1
            if self._blink_tick_countdown <= 0:
                if not self._blink_active:
                    self._blink_active = True
                    self._blink_tick_countdown = 4  # ~130ms blink
                    needs_repaint = True
                else:
                    self._blink_active = False
                    self._blink_tick_countdown = random.randint(80, 180)  # 2.5 - 6s until next blink
                    needs_repaint = True
        else:
            if self._blink_active:
                self._blink_active = False
                needs_repaint = True

        # 4. Emote floating animation and countdown
        if self._current_emote:
            self._emote_float_phase += dt * 4.0
            needs_repaint = True
            if self._emote_timer > 0:
                self._emote_timer -= 1
                if self._emote_timer <= 0:
                    self._current_emote = None
                    self._update_mask()
                    needs_repaint = True

        if needs_repaint:
            self.update()

    def _on_life_tick(self):
        import time as _time
        if not self.isVisible() or self._dragging or self._resizing or self._talking or self._riding_cursor:
            return
        now = _time.monotonic()
        sleep_mins = int(self.settings.get("sleep_after_minutes", 15) or 0)
        if sleep_mins > 0 and not self._sleeping:
            if now - self._last_interaction_time >= sleep_mins * 60.0:
                self.enter_sleep()
                return
        wander_mode = str(self.settings.get("wander_mode", "off") or "off").lower()
        interval = _WANDER_INTERVALS.get(wander_mode)
        if interval and not self._sleeping:
            if now - self._last_wander_time >= interval:
                self.wander_step()

