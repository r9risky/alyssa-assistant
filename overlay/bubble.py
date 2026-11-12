"""The speech bubble: words type out as she says them, then it fades."""
import html
import os
import re
import time

from PySide6.QtCore import (
    Qt,
    QTimer,
    QPoint,
    QRect,
    QPropertyAnimation,
    QEasingCurve,
    QParallelAnimationGroup,
)
from PySide6.QtGui import (
    QPainter, QColor, QGuiApplication,
    QPainterPath, QLinearGradient, QFont, QFontMetrics,
)
from PySide6.QtWidgets import QWidget, QLabel

import config

from . import motion
from .theming import _theme

from .topmost import _TopmostWindowMixin

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
        self._opacity_anim.setDuration(motion.NORMAL_MS)
        self._opacity_anim.setStartValue(start_opacity)
        self._opacity_anim.setEndValue(1.0)
        self._opacity_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._pos_anim.setDuration(motion.NORMAL_MS)
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
        self._opacity_anim.setDuration(motion.SLOW_MS)
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
