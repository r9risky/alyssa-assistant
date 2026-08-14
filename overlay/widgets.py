import json
import math
import os
import queue
import random
import re
import subprocess
import sys
import threading
from urllib.parse import urlsplit

import requests

from PySide6.QtCore import (
    Qt, QTimer, QPoint, QSize, Signal, QObject, QRect,
    QPropertyAnimation, QEasingCurve, QParallelAnimationGroup,
)
from PySide6.QtGui import (
    QPixmap, QPainter, QColor, QRegion, QGuiApplication, QMovie,
    QPainterPath, QLinearGradient, QFont, QFontDatabase, QIcon,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QApplication, QWidget, QMenu, QDialog, QFormLayout, QVBoxLayout,
    QHBoxLayout, QGridLayout, QLineEdit, QComboBox, QSizePolicy,
    QCheckBox, QSlider, QSpinBox, QPushButton, QLabel, QFileDialog,
    QMessageBox, QInputDialog, QStackedWidget, QSystemTrayIcon,
    QScrollArea, QFrame, QSplitter, QListWidget, QListWidgetItem,
    QPlainTextEdit, QGraphicsDropShadowEffect, QGraphicsOpacityEffect,
)

_QW = QWidget

import config

from .rendering import (
    BASE_W, BASE_H, MIN_W, RESIZE_GRIP, _resolve_character_path,
    _scale_centered, render_character, save_overlay_settings,
)
from .theming import _build_menu_style, _build_messagebox_style, _rgba, _theme

class Bridge(QObject):
    """Thread-safe signal bus: the assistant loop runs on a background
    thread and talks to the GUI (which lives on the main thread) only
    through these signals."""

    speak_signal = Signal(str)
    error_signal = Signal(str)
    gemini_key_needed = Signal()
    reply_pending_signal = Signal()  # reply is visible; audio is still being synthesized
    talk_start_signal = Signal()  # emitted right before she starts talking
    talk_end_signal = Signal()  # emitted right after she finishes talking
    thinking_signal = Signal()  # emitted right after a command is captured, before the model replies

    def __init__(self):
        super().__init__()
        # Messages typed into the chat box (ChatInputBar). A plain
        # queue.Queue rather than a Signal because the consumer
        # (main.run_assistant_loop, background thread) just polls it
        # alongside the mic and never touches a Qt object.
        self.text_queue = queue.Queue()


_BUBBLE_TAIL_H = 12  # px, height of the little triangle pointing at her


_BUBBLE_RADIUS = 6


_BUBBLE_PAD_X = 16


_BUBBLE_PAD_Y = 12


class SpeechBubble(QWidget):
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

        # Talk-synced visibility: rather than a fixed "show for N seconds"
        # timer, the bubble watches her actual talk_start/talk_end signals
        # and only starts its short linger-then-fade countdown once she's
        # done speaking. bubble_seconds in Settings is that linger, not a
        # hard total display time.
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._fade_out)
        # Absolute safety net in case talk_end somehow never arrives (e.g.
        # SPEAK_RESPONSES/voice playback throws before the finally block -
        # shouldn't happen, but a stuck-forever bubble would be worse).
        self._safety_timer = QTimer(self)
        self._safety_timer.setSingleShot(True)
        self._safety_timer.timeout.connect(self._fade_out)

        self._opacity_anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._pos_anim = QPropertyAnimation(self, b"pos", self)
        self._show_group = QParallelAnimationGroup(self)
        self._show_group.addAnimation(self._opacity_anim)
        self._show_group.addAnimation(self._pos_anim)
        # PySide warns when disconnect() is called with no matching slot.
        # Keep track of our one temporary fade-out callback so we disconnect
        # it only when it is actually attached.
        self._hide_on_fade_finished = False

        self._talking = False
        self.setWindowOpacity(0.0)
        self.hide()

        # -- "Thinking..." indicator ---------------------------------
        # Shown while a command has been captured but the model hasn't
        # replied yet (see show_thinking / bridge.thinking_signal). Its
        # own timer animates the dots; it's cleared the moment a real
        # message comes in via show_message.
        self._thinking = False
        self._thinking_dots = 0
        self._thinking_timer = QTimer(self)
        self._thinking_timer.timeout.connect(self._on_thinking_tick)

    def showEvent(self, event):
        super().showEvent(event)
        if sys.platform == "win32":
            QTimer.singleShot(
                0,
                lambda: self.companion._apply_windows_topmost(
                    self, bool(self.settings.get("always_on_top", True))
                ),
            )

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

        # No linger/hide timer here - she stays "thinking" until the real
        # reply arrives via show_message() (which clears self._thinking).
        # Only a generous safety net in case something upstream hangs.
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
            # Guaranteed to print regardless of how Qt's queued-slot
            # dispatch handles (or swallows) exceptions - don't rely on
            # sys.excepthook alone for this.
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

        # Slide in from a little below/above its resting spot (matching
        # whichever side the tail is on) while fading in - a lot livelier
        # than just popping into existence.
        start_offset = -14 if self._tail_on_top else 14
        self.move(target_pos.x(), target_pos.y() + start_offset)
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()

        self._opacity_anim.stop()
        # _opacity_anim is reused for both fade-in (here) and fade-out
        # (_fade_out, below), which connects `finished` to self.hide().
        # Clear that connection before every fade-in, or this fade-in's
        # own completion would immediately re-trigger hide().
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

        # Safety net: hide no matter what at most ~4x the linger setting
        # past the normal minimum, even if talk_end never fires.
        seconds = max(1, int(self.settings.get("bubble_seconds", 7)))
        self._safety_timer.start(max(8, seconds * 4) * 1000)

        # If she isn't actually mid-talk right now (SPEAK_RESPONSES off, or
        # shown outside the normal speak() flow), fall back to a timed hide
        # so the bubble doesn't linger forever.
        if not self._talking:
            self._hide_timer.start(seconds * 1000)

    def on_talk_start(self):
        """Wired to bridge.talk_start_signal - keeps the bubble on screen
        for as long as she's actually speaking."""
        self._talking = True
        self._hide_timer.stop()
        self._safety_timer.stop()

    def on_reply_pending(self):
        """Keeps a newly shown reply visible while TTS is being prepared."""
        self._talking = True
        self._hide_timer.stop()
        self._safety_timer.stop()

    def on_talk_end(self):
        """Wired to bridge.talk_end_signal - starts the short linger before
        fading the bubble out, timed to when she actually finished talking
        rather than a fixed guess from when the text first appeared."""
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

    # -- layout / painting ------------------------------------------------
    def _resize_to_content(self):
        w = self.label.width() + _BUBBLE_PAD_X * 2
        h = self.label.height() + _BUBBLE_PAD_Y * 2 + _BUBBLE_TAIL_H
        self.resize(w, h)

    def _compute_position(self) -> QPoint:
        cw = self.companion
        x = cw.x() + cw.width() // 2 - self.width() // 2
        y = cw.y() - self.height() - 4

        # QGuiApplication.screenAt()/primaryScreen() can both momentarily
        # return None (a display was just unplugged, a DPI change is in
        # progress, etc.) - previously this fell through to
        # availableGeometry() and raised AttributeError, which Qt swallows
        # silently on a queued slot, so the bubble just never appeared with
        # no error printed. Fall back to any available screen instead.
        screen = (
            QGuiApplication.screenAt(cw.pos())
            or QGuiApplication.primaryScreen()
            or (QGuiApplication.screens()[0] if QGuiApplication.screens() else None)
        )
        if screen is None:
            # No screens reported at all - nothing sensible to clamp
            # against, just use her own position unclamped rather than
            # crashing the slot.
            self._tail_on_top = True
            self._tail_x = max(_BUBBLE_RADIUS + 6, self.width() // 2)
            self.label.move(_BUBBLE_PAD_X, _BUBBLE_PAD_Y)
            return QPoint(x, y)

        geo = screen.availableGeometry()
        x = max(geo.left(), min(x, geo.right() - self.width()))

        # Keep the bubble above her at all times instead of switching to a
        # below-her placement when there isn't enough room above.
        self._tail_on_top = True
        y = max(geo.top() + 4, y)

        # Tail tip aligns with her horizontal center, clamped so it never
        # points outside the rounded corners of the bubble.
        target_center = cw.x() + cw.width() // 2
        self._tail_x = max(
            _BUBBLE_RADIUS + 6, min(target_center - x, self.width() - _BUBBLE_RADIUS - 6)
        )
        self.label.move(_BUBBLE_PAD_X, _BUBBLE_PAD_Y)
        return QPoint(x, y)

    def reposition(self):
        """Re-anchors the bubble to the companion's current position
        without restarting the fade/slide animation - used while dragging
        or resizing her."""
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

        # Tail (small triangle) touching the rounded body and pointing at her.
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

        # Soft layered "shadow" (cheap manual blur -- a few translucent
        # copies offset slightly downward, since QGraphicsEffect doesn't
        # play well with a translucent, frameless, always-on-top window).
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


class ChatInputBar(QWidget):
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

    def showEvent(self, event):
        super().showEvent(event)
        if sys.platform == "win32":
            QTimer.singleShot(
                0,
                lambda: self.companion._apply_windows_topmost(
                    self, bool(self.settings.get("always_on_top", True))
                ),
            )

    def _update_placeholder(self):
        name = getattr(config, "ASSISTANT_NAME", "her")
        self.edit.setPlaceholderText(f"Type to {name}...")

    def apply_theme(self):
        """Re-applies light/dark styling - called on creation and whenever
        Settings changes dark_mode (see CompanionWindow.apply_companion_settings)."""
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
        # Thread-safe hand-off to main.run_assistant_loop, which polls this
        # queue on its own background thread alongside the mic - see
        # Bridge.__init__ for why this is a plain queue.Queue.
        self.bridge.text_queue.put(text)

    def apply_enabled(self):
        """Shows/hides the bar per settings["chatbox_enabled"] - called on
        creation and whenever Settings changes that checkbox."""
        if self.settings.get("chatbox_enabled", True):
            self.reposition()
            self.show()
        else:
            self.hide()

    def reposition(self):
        """Re-anchors the bar next to the companion's current
        position/size, on whichever side settings["chatbox_position"] says
        ("bottom" / "left" / "right") - called any time she's moved or
        resized (see CompanionWindow.mouseMoveEvent), same as
        SpeechBubble.reposition()."""
        cw = self.companion
        side = self.settings.get("chatbox_position", "bottom")

        # Same screen-edge fallback as SpeechBubble._compute_position -
        # fall back to any available screen rather than letting a None
        # screen silently swallow this move.
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

        # Clamp to the screen instead of flipping sides when there's not
        # enough room - she can sit close enough to an edge that flipping
        # would be surprising, and "bottom" flipping above her would land
        # the chat box on top of the speech bubble, which also lives above her.

        if avail is not None:
            x = max(avail.left(), min(x, avail.right() - self.width()))
            y = max(avail.top(), min(y, avail.bottom() - self.height()))
        self.move(x, y)


class CompanionWindow(QWidget):
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
        # -- PNGTuber-style talk state --------------------------------
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

        # -- animated GIF playback (real frame-by-frame, not a frozen
        # first frame) - QMovie instances, keyed by path, created lazily
        # and only the currently-shown one actually playing. --
        self._movies: dict = {}
        self._active_movie_path = None

        self._pixmap = None
        self._update_pixmap()
        self._update_effective_opacity()

        self._talk_anim_timer = QTimer(self)
        self._talk_anim_timer.timeout.connect(self._on_talk_tick)

        # Bounce: eases up when talking starts, holds while talking, eases
        # back down when it stops - runs on its own timer so it keeps
        # animating the settle-back-down after the mouth-flap timer stops.
        self._bounce_target = 0.0
        self._bounce_anim_timer = QTimer(self)
        self._bounce_anim_timer.timeout.connect(self._on_bounce_anim_tick)

    # -- rendering -----------------------------------------------------
    def _update_pixmap(self):
        mouth_open = self._talking and self._mouth_open and self.settings.get("talk_mouth_flap_enabled", True)
        path = _resolve_character_path(self.settings, mouth_open)
        if path.lower().endswith(".gif"):
            self._pixmap = self._frame_from_movie(path, mouth_open)
        else:
            self._stop_active_movie()
            self._pixmap = render_character(self.settings, self.size(), mouth_open=mouth_open)
        self._update_mask()
        self.update()

    def _frame_from_movie(self, path: str, mouth_open: bool) -> QPixmap:
        """Returns the current frame of the GIF at path, scaled/centered
        to the window's current size - and makes sure that GIF's QMovie
        is the one actually playing (stopping whichever one was playing
        before, if this is a switch between her idle/talking GIFs, so
        only one decodes frames at a time)."""
        movie = self._movies.get(path)
        if movie is None:
            movie = QMovie(path)
            movie.setCacheMode(QMovie.CacheAll)
            # Some GIFs have a finite (or zero) authored loop count - as a
            # background character she should loop forever regardless.
            movie.finished.connect(movie.start)
            movie.frameChanged.connect(self._on_movie_frame)
            self._movies[path] = movie

        if self._active_movie_path != path:
            self._stop_active_movie()
            self._active_movie_path = path
            movie.start()

        frame = movie.currentPixmap()
        if frame.isNull():
            # First call, before the movie has decoded frame 0 yet - fall
            # back to a static render just for this one paint so there's
            # never a blank flash; the next frameChanged tick replaces it.
            return render_character(self.settings, self.size(), mouth_open=mouth_open)
        return _scale_centered(frame, self.size())

    def _stop_active_movie(self):
        if self._active_movie_path is not None:
            movie = self._movies.get(self._active_movie_path)
            if movie is not None:
                movie.stop()
            self._active_movie_path = None

    def _on_movie_frame(self, _frame_number: int):
        # QMovie advances frames on its own internal timer, so this is the
        # actual animation tick for a GIF - only repaint if the signal came
        # from whichever movie is currently active/shown (a previous movie
        # can still be mid-decode of a queued frame right as we switch away).
        if self.sender() is self._movies.get(self._active_movie_path):
            self._update_pixmap()

    def _update_mask(self):
        if self._pixmap is None or self._pixmap.isNull():
            return
        region = QRegion(self._pixmap.mask()).translated(0, int(self._bounce_offset))
        # Always keep the resize grip clickable even if that corner of the
        # image happens to be transparent.
        gx = self.width() - RESIZE_GRIP
        gy = self.height() - RESIZE_GRIP
        region += QRegion(gx, gy, RESIZE_GRIP, RESIZE_GRIP, QRegion.Ellipse)
        self.setMask(region)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if self._pixmap is not None:
            painter.drawPixmap(0, int(self._bounce_offset), self._pixmap)

        # Resize grip glyph, always visible so it's discoverable even on a
        # transparent corner of the artwork. Color pulls from the current
        # theme accent so it matches the UI palette instead of being arbitrary.
        gx = self.width() - RESIZE_GRIP
        gy = self.height() - RESIZE_GRIP
        t_grip = _theme(self.settings)
        accent_hex = t_grip["accent"]
        r = int(accent_hex[1:3], 16)
        g = int(accent_hex[3:5], 16)
        b = int(accent_hex[5:7], 16)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(r, g, b, 80))
        painter.drawEllipse(gx, gy, RESIZE_GRIP, RESIZE_GRIP)
        painter.setPen(QColor(255, 255, 255, 180))
        for i in range(3):
            off = 5 + i * 4
            painter.drawLine(
                gx + RESIZE_GRIP - off, gy + RESIZE_GRIP - 3,
                gx + RESIZE_GRIP - 3, gy + RESIZE_GRIP - off,
            )

    # -- thread-safe entry points for Bridge signals ----------------------
    # Real bound methods (not a lambda) so PySide6 detects they belong to
    # a GUI-thread QObject and auto-queues the call rather than running it
    # directly on the background thread - see run_with_assistant() below.
    def start_talking(self):
        self.set_talking(True)

    def stop_talking(self):
        self.set_talking(False)

    def show_error(self, message: str):
        QMessageBox.critical(self, config.ASSISTANT_NAME, message)

    # -- PNGTuber talk animation (mouth flap + bounce) --------------------
    def set_talking(self, talking: bool):
        """Called from the assistant loop (via Bridge signals) right
        before/after she speaks. Drives the mouth-open image swap and the
        bounce animation; both are individually toggleable in Settings."""
        if talking == self._talking:
            return
        self._talking = talking
        if talking:
            self._talk_tick = 0
            # Snap the mouth open immediately (not on the first 50ms timer
            # tick) so bounce, mouth, and playback all start on the same
            # frame; the random flap cadence below takes over right after.
            self._mouth_open = True
            self._next_flap_tick = random.randint(2, 4)
            self._talk_anim_timer.start(50)
            self._update_pixmap()  # repaint now - don't wait for the first timer tick
        else:
            self._talk_anim_timer.stop()
            self._mouth_open = False
            self._update_pixmap()

        # Ease toward "up" the instant talking starts, and toward "down"
        # the instant it stops - the ease timer keeps running until she
        # actually settles, even after the mouth-flap timer has stopped.
        height = max(0, float(self.settings.get("talk_bounce_height", 0)))
        height *= self.settings.get("scale", 1.0)
        self._bounce_target = -height if (talking and self.settings.get("talk_bounce_enabled", True)) else 0.0
        if not self._bounce_anim_timer.isActive():
            self._bounce_anim_timer.start(20)

        self._update_effective_opacity()

    def _on_bounce_anim_tick(self):
        # Simple ease toward the current target - quick but not instant, so
        # both the "pop up" and the "settle back down" read as motion
        # rather than a snap.
        diff = self._bounce_target - self._bounce_offset
        if abs(diff) < 0.4:
            self._bounce_offset = self._bounce_target
            self._bounce_anim_timer.stop()
        else:
            self._bounce_offset += diff * 0.35
        self._update_pixmap()

    def _on_talk_tick(self):
        self._talk_tick += 1

        # Flap the mouth open/closed a few times a second, like a
        # PNGTuber. A slightly randomized interval reads closer to real
        # speech cadence than a rigid fixed one.
        if self.settings.get("talk_mouth_flap_enabled", True):
            if self._talk_tick >= self._next_flap_tick:
                self._mouth_open = not self._mouth_open
                # Open->closed snaps back a bit quicker than closed->open,
                # which is what a mouth actually does when talking.
                gap = random.randint(2, 4) if self._mouth_open else random.randint(1, 3)
                self._next_flap_tick = self._talk_tick + gap
        else:
            self._mouth_open = False

        self._update_pixmap()

    def _update_effective_opacity(self):
        """Combines the base "Opacity" setting with the optional
        "dim while idle" setting -- she fades out a bit whenever she isn't
        talking, and comes back to full (base) opacity while she is."""
        base = float(self.settings.get("opacity", 1.0))
        if self.settings.get("dim_when_idle_enabled", False) and not self._talking:
            dim_pct = float(self.settings.get("dim_when_idle_opacity", 55)) / 100.0
            effective = base * dim_pct
        else:
            effective = base
        self.setWindowOpacity(max(0.05, min(1.0, effective)))

    # -- mouse interaction -------------------------------------------------
    def _in_resize_grip(self, pos: QPoint) -> bool:
        return pos.x() >= self.width() - RESIZE_GRIP and pos.y() >= self.height() - RESIZE_GRIP

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            pos = event.position().toPoint()
            if self._in_resize_grip(pos):
                self._resizing = True
                self._resize_start_pos = event.globalPosition().toPoint()
                self._resize_start_size = self.size()
            else:
                self._dragging = True
                self._drag_offset = event.globalPosition().toPoint() - self.pos()
        elif event.button() == Qt.RightButton:
            self._show_context_menu(event.globalPosition().toPoint())

    def mouseMoveEvent(self, event):
        gp = event.globalPosition().toPoint()
        if self._resizing:
            delta = gp.x() - self._resize_start_pos.x()
            new_w = max(MIN_W, self._resize_start_size.width() + delta)
            new_h = int(new_w * BASE_H / BASE_W)
            self.resize(new_w, new_h)
            self._update_pixmap()
            if self.bubble:
                self.bubble.reposition()
            if self.chatbar:
                self.chatbar.reposition()
        elif self._dragging:
            self.move(gp - self._drag_offset)
            if self.bubble:
                self.bubble.reposition()
            if self.chatbar:
                self.chatbar.reposition()

    def mouseReleaseEvent(self, event):
        if self._dragging or self._resizing:
            self._dragging = False
            self._resizing = False
            self.settings["scale"] = round(self.width() / BASE_W, 3)
            self.settings["pos_x"] = self.x()
            self.settings["pos_y"] = self.y()
            save_overlay_settings(self.settings)
            dialog = getattr(self, "_settings_dialog", None)
            if dialog is not None:
                try:
                    dialog.sync_companion_scale(self.settings["scale"])
                except RuntimeError:
                    self._settings_dialog = None

    def mouseDoubleClickEvent(self, event):
        # A little life sign so it's obvious she's interactive.
        if self.bubble:
            self.bubble.show_message(random.choice([
                "Yes? I'm listening whenever you say my name.",
                "Right-click me any time you'd like to change my settings.",
                "At your service.",
            ]))

    # -- context menu / settings ----------------------------------------
    def _show_context_menu(self, global_pos):
        menu = QMenu()
        menu.setAttribute(Qt.WA_TranslucentBackground)
        menu.setStyleSheet(_build_menu_style(self.settings))
        settings_action = menu.addAction("Settings…")
        menu.addSeparator()
        top_action = menu.addAction("Always on Top")
        top_action.setCheckable(True)
        top_action.setChecked(bool(self.settings.get("always_on_top", True)))
        reset_action = menu.addAction("Reset Position && Size")
        menu.addSeparator()
        hide_action = menu.addAction("Hide (right-click the tray icon to bring her back)")
        quit_action = menu.addAction("Quit Alyssa")

        chosen = menu.exec(global_pos)
        if chosen is None:
            return
        if chosen == settings_action:
            self.open_settings()
        elif chosen == top_action:
            self._set_always_on_top(top_action.isChecked())
        elif chosen == reset_action:
            self._reset_position_only()
            self.settings["scale"] = 1.0
            self.resize(BASE_W, BASE_H)
            self._update_pixmap()
            dialog = getattr(self, "_settings_dialog", None)
            if dialog is not None:
                dialog.sync_companion_scale(1.0)
            save_overlay_settings(self.settings)
        elif chosen == hide_action:
            self.hide()
        elif chosen == quit_action:
            QApplication.instance().quit()

    def _apply_windows_topmost(self, window: QWidget, on: bool) -> bool:
        if sys.platform != "win32" or not window.isVisible():
            return False
        try:
            import ctypes
            hwnd = int(window.winId())
            insert_after = -1 if on else -2  # HWND_TOPMOST / HWND_NOTOPMOST
            flags = 0x0001 | 0x0002 | 0x0010 | 0x0200  # NOSIZE|NOMOVE|NOACTIVATE|NOOWNERZORDER
            return bool(ctypes.windll.user32.SetWindowPos(hwnd, insert_after, 0, 0, 0, 0, flags))
        except Exception:
            return False

    def _set_always_on_top(self, on: bool, persist: bool = True):
        """Change Z-order without losing the overlay's geometry or visibility."""
        on = bool(on)
        self.settings["always_on_top"] = on
        for window in (self, self.bubble, self.chatbar):
            if window is None or self._apply_windows_topmost(window, on):
                continue
            geometry = QRect(window.geometry())
            was_visible = window.isVisible()
            window.setWindowFlag(Qt.WindowStaysOnTopHint, on)
            window.setGeometry(geometry)
            if was_visible:
                window.show()
        if persist:
            save_overlay_settings(self.settings)

    def showEvent(self, event):
        super().showEvent(event)
        if sys.platform == "win32":
            QTimer.singleShot(
                0,
                lambda: self._apply_windows_topmost(
                    self,
                    bool(self.settings.get("always_on_top", True))
                ),
            )

    def _reset_position_only(self):
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(screen.left() + 24, screen.bottom() - self.height() - 24)

    def open_settings(self, focus_gemini: bool = False):
        # If Settings is already open, just bring it to front instead of
        # spawning a second one on top of it.
        existing = getattr(self, "_settings_dialog", None)
        if existing is not None:
            try:
                if existing.isMinimized():
                    existing.showNormal()
                else:
                    existing.show()
                existing.raise_()
                existing.activateWindow()
                existing.setFocus(Qt.ActiveWindowFocusReason)
                if sys.platform == "win32":
                    try:
                        import ctypes
                        hwnd = int(existing.winId())
                        ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                        ctypes.windll.user32.SetForegroundWindow(hwnd)
                    except Exception:
                        pass
                if focus_gemini:
                    existing.tabs.setCurrentIndex(0)
                    existing.provider_combo.setCurrentText("gemini")
                    existing.gemini_setup_hint.setVisible(True)
                    existing.gemini_key_edit.setFocus()
                return
            except RuntimeError:
                # The underlying C++ object was already destroyed (closed
                # since we last checked, e.g. via WA_DeleteOnClose) -
                # fall through and open a fresh one below.
                self._settings_dialog = None

        from .settings_dialog import ConfigDialog

        dialog = ConfigDialog(self, focus_gemini=focus_gemini)
        # Keep a reference on self so Python doesn't garbage-collect the
        # dialog the moment this method returns - unlike exec()'s blocking
        # local event loop, show() returns immediately, so nothing else
        # would otherwise keep it alive.
        self._settings_dialog = dialog
        dialog.finished.connect(self._on_settings_dialog_finished)
        dialog.ensurePolished()
        dialog.updateGeometry()
        QTimer.singleShot(0, lambda: self._show_settings_dialog(dialog))

    def _show_settings_dialog(self, dialog):
        if getattr(self, "_settings_dialog", None) is not dialog:
            return
        dialog.show()
        dialog.finalize_initial_layout()
        QTimer.singleShot(0, dialog.finalize_initial_layout)
        dialog.raise_()
        dialog.activateWindow()

    def _on_settings_dialog_finished(self, *_args):
        self._settings_dialog = None

    def prompt_gemini_key_setup(self):
        """Friendly, beginner-guided version of 'you need an API key' -
        shown instead of a scary error box when Gemini is selected but no
        key is set yet, e.g. on first launch."""
        if self.bubble:
            self.bubble.show_message(
                "I need a free Gemini API key before I can think! "
                "Right-click me and choose Settings to add one →"
            )
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle(f"Let's get {config.ASSISTANT_NAME} set up")
        box.setStyleSheet(_build_messagebox_style(self.settings))
        box.setText(
            "One quick step before I can start listening: I need a free "
            "Gemini API key.\n\n"
            "1. Get a free key at aistudio.google.com/apikey\n"
            "2. Come back here and click \"Open Settings\" below\n"
            "3. Paste the key into \"Gemini API key\" (Assistant tab)\n"
            "That's it - it takes effect immediately, no restart or Save "
            "needed.\n\n"
            "(You can always get back to this screen later by "
            "right-clicking me and choosing Settings.)"
        )
        open_btn = box.addButton("Open Settings", QMessageBox.AcceptRole)
        box.addButton("I'll do it later", QMessageBox.RejectRole)
        box.setDefaultButton(open_btn)
        box.exec()
        if box.clickedButton() == open_btn:
            self.open_settings(focus_gemini=True)

    def apply_companion_settings(self, new_settings: dict):
        changed = {
            key for key, value in new_settings.items()
            if self.settings.get(key) != value
        }
        if not changed:
            return
        self.settings.update(new_settings)

        if "scale" in changed:
            w = max(MIN_W, int(BASE_W * self.settings.get("scale", 1.0)))
            self.resize(w, int(w * BASE_H / BASE_W))

        if changed & {"opacity", "dim_when_idle_enabled", "dim_when_idle_opacity"}:
            self._update_effective_opacity()

        if "always_on_top" in changed:
            self._set_always_on_top(
                bool(self.settings.get("always_on_top", True)), persist=False
            )

        if changed & {"scale", "talk_bounce_enabled", "talk_bounce_height"}:
            height = max(0, float(self.settings.get("talk_bounce_height", 0)))
            height *= self.settings.get("scale", 1.0)
            self._bounce_target = -height if (
                self._talking and self.settings.get("talk_bounce_enabled", True)
            ) else 0.0
            if self._bounce_target != self._bounce_offset and not self._bounce_anim_timer.isActive():
                self._bounce_anim_timer.start(20)

        if "talk_mouth_flap_enabled" in changed and not self.settings.get(
            "talk_mouth_flap_enabled", True
        ):
            self._mouth_open = False

        if changed & {
            "scale", "character_image", "character_image_talking",
            "talk_mouth_flap_enabled",
        }:
            self._update_pixmap()
        elif changed & {"color_theme", "dark_mode"}:
            self.update()

        theme_changed = bool(changed & {"color_theme", "dark_mode"})
        if self.bubble is not None and theme_changed:
            t = _theme(self.settings)
            self.bubble.label.setStyleSheet(f"QLabel {{ background: transparent; color: {t['bubble_text']}; }}")
            self.bubble.update()  # re-paint with the new theme if it's on screen right now
        if self.bubble is not None and "scale" in changed:
            self.bubble.reposition()
        if self.chatbar is not None:
            if theme_changed:
                self.chatbar.apply_theme()
            if changed & {"chatbox_enabled", "chatbox_position", "scale"}:
                self.chatbar.apply_enabled()
        if self.tray is not None and theme_changed:
            from .app_shell import _build_app_icon

            self.tray.setIcon(_build_app_icon(theme=_theme(self.settings)))
        save_overlay_settings(self.settings)
