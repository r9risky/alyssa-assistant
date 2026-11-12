import math
import queue
import random
import threading

from PySide6.QtCore import (
    Qt,
    QTimer,
    QPoint,
    QSize,
    Signal,
    QObject,
    QCoreApplication,
    QThread,
    QPropertyAnimation,
    QEasingCurve,
)
from PySide6.QtWidgets import QWidget


from . import motion
from .rendering import BASE_W, BASE_H, MIN_W, save_overlay_settings
from .companion.rendering_mixin import RenderingMixin
from .companion.interaction_mixin import InteractionMixin
from .companion.window_mixin import WindowMixin
from .companion.settings_mixin import SettingsMixin
from .companion.talk_state_mixin import TalkStateMixin

from .topmost import _TopmostWindowMixin  # noqa: F401
from .bubble import SpeechBubble  # noqa: F401  (re-exported: overlay.widgets.SpeechBubble)
from .companion_bars import ChatInputBar, HoverPill, MiniPlayerWidget  # noqa: F401


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

    # -- hover pill: quick actions + her fondness heart while the mouse is on her --
    def enterEvent(self, event):
        super().enterEvent(event)
        self.keep_hover_pill()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.release_hover_pill()

    def keep_hover_pill(self):
        pill = self.hover_pill
        if pill is None or not self.settings.get("hover_pill_enabled", True) or self._dragging:
            return
        timer = getattr(self, "_pill_hide_timer", None)
        if timer is not None:
            timer.stop()
        if not pill.isVisible() or getattr(pill, "_motion_hides", False):
            pill.refresh_state()
            pill.reposition()
        motion.fade_in(pill, motion.FAST_MS)

    def release_hover_pill(self):
        """Hide the pill shortly after the mouse leaves (long enough to reach the pill itself)."""
        if self.hover_pill is None:
            return
        timer = getattr(self, "_pill_hide_timer", None)
        if timer is None:
            timer = self._pill_hide_timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda: motion.fade_out(self.hover_pill, motion.FAST_MS))
        timer.start(600)

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

