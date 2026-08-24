import queue
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
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.companion = companion
        self.settings = settings
        self._tail_x = 0  # tail tip position, in this widget's local coords
        self._tail_on_top = True  # tail points down (bubble above her) by default

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

        t = _theme(self.settings)
        self.label.setStyleSheet(f"QLabel {{ background: transparent; color: {t['bubble_text']}; }}")

        self.label.setText(text)
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


class CompanionWindow(
    RenderingMixin, InteractionMixin, WindowMixin, SettingsMixin, TalkStateMixin, QWidget
):
    def __init__(self, settings: dict, bridge: Bridge):
        flags = Qt.FramelessWindowHint | Qt.Tool | Qt.NoDropShadowWindowHint
        if settings.get("always_on_top", True):
            flags |= Qt.WindowStaysOnTopHint
        super().__init__(
            None,
            flags,
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.settings = settings
        self.bridge = bridge
        self.bubble = None  # set from run_with_assistant()
        self.chatbar = None  # set from run_with_assistant()
        self.tray = None

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
