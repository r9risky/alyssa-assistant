import html
import math
import os
import re
import time
import queue
import random
import sys
import threading

from PySide6.QtCore import (
    Qt, QTimer, QPoint, QSize, Signal, QObject, QRect, QCoreApplication, QThread,
    QPropertyAnimation, QEasingCurve, QParallelAnimationGroup,
)
from PySide6.QtGui import (
    QPainter, QColor, QGuiApplication,
    QPainterPath, QLinearGradient, QFont, QFontMetrics,
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
    spoken_signal = Signal(str)  # she just started saying this sentence; the bubble types it out
    thinking_signal = Signal()  # emitted right after a command is captured, before the model replies
    fondness_signal = Signal()  # fondness score changed; the hover pill re-reads it
    steps_signal = Signal(list)  # [(label, 'running'|'done'|'failed')] tool steps of the current turn
    sources_signal = Signal(list)  # [{url, title, site}] web pages behind the last reply
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


_BUBBLE_RADIUS = 12


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
        self._steps: list = []  # [(label, state)] while she works through tools
        self._sources: list[dict] = []
        self._sources_for = ""  # the reply text the sources belong to
        # Typing: _full_text is everything known so far, _shown how much of it is visible.
        self._shown = 0.0
        self._spoken_words = None  # words she has started saying (None: not synced to her voice)
        self._awaiting_voice_since = None  # reply is in, her voice isn't yet: hold the text back
        self._last_rendered = None
        self._type_clock = 0.0
        self._type_timer = QTimer(self)
        self._type_timer.setInterval(16)
        self._type_timer.timeout.connect(self._on_type_tick)

        self.label = QLabel("", self)
        self.label.setWordWrap(True)
        self.label.setMaximumWidth(260)
        font = QFont("Segoe UI Variable Text", 10)
        font.setStyleHint(QFont.SansSerif)
        font.setWeight(QFont.Medium)
        self.label.setFont(font)
        self.label.setTextFormat(Qt.RichText)
        self.label.setOpenExternalLinks(True)
        self.label.setTextInteractionFlags(Qt.LinksAccessibleByMouse)
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
        """The current page as label HTML, with source links under the last page."""
        if self._thinking:
            return self._thinking_html()
        if not self._typing_done():
            revealed = self._revealed()
            if not revealed.strip():
                return self._thinking_html()
            # Mid-sentence there are no page turns by hand: show the page she's on.
            page = self._paginate_text(revealed)[-1]
            return html.escape(page).replace("\n", "<br>") + self._steps_html()
        if not self._pages:
            return html.escape(self._full_text).replace("\n", "<br>") + self._steps_html()
        page_html = html.escape(self._pages[self._page_index]).replace("\n", "<br>")
        if len(self._pages) > 1:
            page_html += f"<br>({self._page_index + 1}/{len(self._pages)})"
        page_html += self._steps_html()
        if self._has_sources() and self._page_index == len(self._pages) - 1:
            color = _theme(self.settings)["bubble_text"]
            links = " · ".join(
                f'<a href="{html.escape(s["url"], quote=True)}" style="color:{color};">'
                f'{html.escape(s["site"])}</a>'
                for s in self._sources
            )
            page_html += f'<br><span style="font-size:8pt;">Sources: {links}</span>'
        return page_html

    _STEP_MARKS = {"running": "▸", "done": "✓", "failed": "✗"}
    MAX_STEPS_SHOWN = 5

    def _steps_html(self) -> str:
        if not self._steps:
            return ""
        rows = [
            f"{self._STEP_MARKS.get(state, '·')} {html.escape(label)}"
            for label, state in self._steps[-self.MAX_STEPS_SHOWN:]
        ]
        return '<br><span style="font-size:8pt;">' + "<br>".join(rows) + "</span>"

    def show_steps(self, steps: list):
        """Show the tool steps of the current turn (from bridge.steps_signal); [] hides them."""
        self._steps = list(steps or [])
        if self._thinking or self._full_text:
            self._render(force=True)

    def _thinking_html(self) -> str:
        return "Thinking" + "." * self._thinking_dots + self._steps_html()

    def _has_sources(self) -> bool:
        return bool(self._sources) and self._sources_for == self._full_text

    def show_sources(self, sources: list):
        """Link the web pages behind the reply now showing (from bridge.sources_signal)."""
        self._sources = list(sources or [])
        self._sources_for = self._full_text
        self._render(force=True)

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

    def _render(self, force: bool = False):
        """Show the current text; skips the relayout when nothing visible changed."""
        markup = self._format_current_page()
        if markup == self._last_rendered and not force:
            return
        self._last_rendered = markup
        self.label.setText(markup)
        self.label.adjustSize()
        self._resize_to_content()
        self.reposition()

    # -- typing in step with her voice -----------------------------------
    _SPEAK_CPS = 16.0  # about how fast her voices talk, in characters per second
    _READ_CPS = 90.0  # when she isn't talking: quick, but still visibly typing
    _MAX_VOICE_WAIT = 4.0  # seconds to hold text back for a voice that may never start

    def _revealed(self) -> str:
        return self._full_text[: int(self._shown)]

    def _typing_done(self) -> bool:
        return int(self._shown) >= len(self._full_text)

    def _char_index_after_words(self, words: int) -> int:
        for count, match in enumerate(re.finditer(r"\S+", self._full_text), 1):
            if count >= words:
                return match.end()
        return len(self._full_text)

    def _reveal_limit(self) -> int:
        if self._awaiting_voice_since is not None:
            if time.monotonic() - self._awaiting_voice_since < self._MAX_VOICE_WAIT:
                return 0
            self._awaiting_voice_since = None
        if self._spoken_words is None:
            return len(self._full_text)
        return self._char_index_after_words(self._spoken_words)

    def _start_typing(self):
        if not self._type_timer.isActive():
            self._type_clock = time.monotonic()
            self._type_timer.start()

    def _on_type_tick(self):
        now = time.monotonic()
        elapsed, self._type_clock = now - self._type_clock, now
        rate = self._SPEAK_CPS if (self._talking or self._spoken_words is not None) else self._READ_CPS
        self._shown = min(max(self._shown, 0.0) + rate * elapsed, float(max(self._reveal_limit(), int(self._shown))))
        if self._typing_done():
            self._finish_typing()
        else:
            self._render()

    def finish_typing(self):
        """Show the whole reply now (she finished talking, or was cut off)."""
        self._awaiting_voice_since = None
        self._shown = float(len(self._full_text))
        self._finish_typing()

    def _finish_typing(self):
        self._type_timer.stop()
        self._pages = self._paginate_text(self._full_text)
        self._page_index = max(0, len(self._pages) - 1)
        self._render()
        if not self._talking and self.isVisible() and not self._hide_on_fade_finished:
            seconds = max(1, int(self.settings.get("bubble_seconds", 7)))
            self._hide_timer.start(seconds * 1000)

    def on_chunk_spoken(self, text: str):
        """She just started saying `text` (a sentence of the reply): type it out as she says it."""
        if self._spoken_words is not None:
            # Everything before this sentence has been said by now, so it's all on screen.
            self._shown = max(self._shown, float(self._char_index_after_words(self._spoken_words)))
        self._awaiting_voice_since = None
        self._spoken_words = (self._spoken_words or 0) + len((text or "").split())
        if self._full_text:
            self._start_typing()

    def _render_current_page(self):
        self._render(force=True)
        try:
            import voice_dsp
            voice_dsp.play_typing_blip()
        except Exception:
            pass

    def copy_to_clipboard(self) -> str:
        """Copy the full bubble reply to the clipboard and briefly display `> sys: copied`."""
        if not self._full_text:
            return ""
        cb = QGuiApplication.clipboard()
        if cb is not None:
            copied = self._full_text
            if self._has_sources():
                copied += "\n\nSources:\n" + "\n".join(s["url"] for s in self._sources)
            cb.setText(copied)
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
        self._type_timer.stop()
        self._thinking = True
        self._thinking_dots = 0
        self._steps = []
        self._full_text = ""
        self._pages = []
        self._shown = 0.0
        self._spoken_words = None
        self._awaiting_voice_since = None

        t = _theme(self.settings)
        self.label.setStyleSheet(f"QLabel {{ background: transparent; color: {t['bubble_text']}; }}")
        self._fit_label_width("Thinking...")
        self._render(force=True)
        self._appear()

        # Dots tick until her words start; long safety net if upstream hangs.
        self._thinking_timer.start(450)
        seconds = max(1, int(self.settings.get("bubble_seconds", 7)))
        self._safety_timer.start(max(20, seconds * 6) * 1000)

    def _on_thinking_tick(self):
        self._thinking_dots = (self._thinking_dots + 1) % 4
        if self._thinking or (self._full_text and not self._revealed().strip()):
            self._render()
        elif not self._full_text or self._revealed().strip():
            self._thinking_timer.stop()

    def _appear(self):
        """Fade and slide in, unless she's already showing (then the text just changes)."""
        if self.isVisible() and not self._hide_on_fade_finished:
            return
        target_pos = self._compute_position()
        start_offset = -14 if self._tail_on_top else 14
        self._show_group.stop()
        self._opacity_anim.stop()
        # _opacity_anim is also used for fade-out; drop hide-on-finished before fade-in or this completion would hide immediately.
        self._clear_fade_hide_callback()
        start_opacity = self.windowOpacity() if self.isVisible() else 0.0
        if not self.isVisible():
            self.move(target_pos.x(), target_pos.y() + start_offset)
            self.setWindowOpacity(0.0)
            self.show()
        self.raise_()
        self._opacity_anim.setDuration(220)
        self._opacity_anim.setStartValue(start_opacity)
        self._opacity_anim.setEndValue(1.0)
        self._opacity_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._pos_anim.setDuration(220)
        self._pos_anim.setStartValue(self.pos())
        self._pos_anim.setEndValue(target_pos)
        self._pos_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._show_group.start()
        self._blip()

    @staticmethod
    def _blip():
        try:
            import voice_dsp
            voice_dsp.play_typing_blip()
        except Exception:
            pass

    def _fit_label_width(self, text: str):
        """Fix the width to the longest line of the text so far: while she types, the
        bubble only grows downward instead of wobbling sideways with every letter."""
        metrics = QFontMetrics(self.label.font())
        widest = max((metrics.horizontalAdvance(line) for line in (text or " ").splitlines()), default=0)
        self.label.setFixedWidth(max(60, min(260, widest + 4)))

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
        was_thinking = self._thinking
        fresh = not self.isVisible() or self._hide_on_fade_finished
        self._thinking = False
        self._hide_timer.stop()
        self._safety_timer.stop()

        if text != self._full_text:
            # A streamed reply grows: keep what's typed. A different message starts over.
            kept = len(os.path.commonprefix([self._revealed(), text]))
            if kept == 0 and self._full_text and not was_thinking:
                self._spoken_words = None
            self._shown = 0.0 if fresh else float(min(int(self._shown), kept))
            self._full_text = text
            self._pages = []
            self._page_index = 0
            if fresh:
                self._spoken_words = None
            if was_thinking or fresh or kept == 0:
                self._blip()

        t = _theme(self.settings)
        self.label.setStyleSheet(f"QLabel {{ background: transparent; color: {t['bubble_text']}; }}")
        self._fit_label_width(text)
        self._appear()
        self._render(force=True)
        if self._typing_done():
            self._finish_typing()
        else:
            self._start_typing()
            if not self._revealed().strip() and not self._thinking_timer.isActive():
                self._thinking_timer.start(450)

        # Hide by ~4x linger even if talk_end never fires.
        seconds = max(1, int(self.settings.get("bubble_seconds", 7)))
        self._safety_timer.start(max(8, seconds * 4) * 1000)

    def on_talk_start(self):
        """Keep the bubble up for the duration of speech; her words start now."""
        self._talking = True
        self._awaiting_voice_since = None
        self._hide_timer.stop()
        self._safety_timer.stop()
        if self._full_text and not self._typing_done():
            self._start_typing()

    def on_reply_pending(self):
        """A reply is coming and will be spoken: hold its text until her voice starts."""
        self._talking = True
        if getattr(config, "SPEAK_RESPONSES", True):
            self._awaiting_voice_since = time.monotonic()
        self._hide_timer.stop()
        self._safety_timer.stop()

    def on_talk_end(self):
        """Show the rest, then linger → fade, timed from actual talk_end."""
        self._talking = False
        self.finish_typing()
        seconds = max(1, int(self.settings.get("bubble_seconds", 7)))
        self._hide_timer.start(seconds * 1000)

    def _fade_out(self):
        if not self.isVisible():
            return
        self._type_timer.stop()
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
        if cw is None:
            return QPoint(100, 100)
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

        self.heart_label = QLabel(self)
        layout.addWidget(self.heart_label)
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

        self.resize(214, 30)
        self.refresh_state()
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

        self.resize(220, 36)
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
    """Return the active CompanionWindow, or None.

    Off the GUI thread (tools run on the assistant worker) this returns a
    GuiThreadProxy, so `companion.set_mood(...)` or `companion.bubble.show_message(...)`
    is safe even if the caller forgets run_on_gui_thread."""
    window = _ACTIVE_COMPANION_WINDOW
    if window is None or _on_gui_thread():
        return window
    return GuiThreadProxy(window)


get_active_companion_window = get_active_companion


def _on_gui_thread() -> bool:
    app = QCoreApplication.instance()
    return app is None or QThread.currentThread() is app.thread()


def run_on_gui_thread(fn) -> bool:
    """Run fn on the Qt GUI thread. Tools run on the assistant worker thread, where
    touching widgets crashes Qt and a context-less QTimer.singleShot never fires."""
    app = QCoreApplication.instance()
    if app is None:
        return False
    if QThread.currentThread() is app.thread():
        fn()
    else:
        QTimer.singleShot(0, app, fn)
    return True


GUI_CALL_TIMEOUT_SECONDS = 5.0


def call_on_gui_thread(fn, *args, timeout: float = GUI_CALL_TIMEOUT_SECONDS, **kwargs):
    """Run fn(*args, **kwargs) on the GUI thread and return its result.

    Waits at most `timeout` seconds (returns None after that) so a busy GUI can't
    hang the assistant forever. Exceptions from fn are re-raised in the caller."""
    if _on_gui_thread():
        return fn(*args, **kwargs)
    done = threading.Event()
    outcome = {}

    def _run():
        try:
            outcome["value"] = fn(*args, **kwargs)
        except BaseException as e:  # handed back to the waiting thread
            outcome["error"] = e
        finally:
            done.set()

    run_on_gui_thread(_run)
    if not done.wait(timeout):
        print(f"[gui] {getattr(fn, '__name__', fn)!r} didn't finish on the GUI thread within {timeout}s")
        return None
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]


class GuiThreadProxy:
    """Wraps a QObject so method calls from any thread run on the GUI thread.

    Sub-widgets reached through it (companion.bubble, window.game_hud) are wrapped
    too. Plain Python attribute reads and writes pass straight through."""

    __slots__ = ("_target",)

    def __init__(self, target):
        object.__setattr__(self, "_target", target)

    def __getattr__(self, name):
        value = getattr(self._target, name)
        if _on_gui_thread():
            return value
        if isinstance(value, QObject):
            return GuiThreadProxy(value)
        if callable(value):
            return lambda *args, **kwargs: _proxied(call_on_gui_thread(value, *args, **kwargs))
        return value

    def __setattr__(self, name, value):
        setattr(self._target, name, value)

    def __repr__(self):
        return f"GuiThreadProxy({self._target!r})"


def _proxied(value):
    if isinstance(value, QObject) and not _on_gui_thread():
        return GuiThreadProxy(value)
    return value


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

        # Code-driven poses (overlay/poses.py): tilt around her feet + squash.
        self._pose_state = "idle"
        self._pose_time = 0.0
        self._pose_tilt = 0.0
        self._pose_squash = 1.0
        self._land_started = None
        self._last_drag_x = None
        self._frame_index = 0

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

        # 3b. Poses: drag flail, landing squash, sleepy droop, music sway
        if self._update_pose(dt):
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

