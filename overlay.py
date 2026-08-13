"""
overlay.py -- Desktop companion overlay for Alyssa.

A small always-on-top character sits on your screen (like Desktop Mate /
Shimeji-style desktop pets):
  - Left-click + drag her body to move her anywhere on screen.
  - Left-click + drag the little grip in her bottom-right corner to resize her.
  - Right-click her for a menu, including "Settings..." which opens a GUI to
    change the assistant's name/voice/model AND her appearance.
  - When Alyssa speaks, her reply appears in a speech bubble above her head.

This is intentionally a separate, optional layer on top of the existing
console assistant (main.py / brain.py / actions.py) -- none of that logic
changes. If you'd rather not use it, set ENABLE_COMPANION_GUI = False in
config.py and Alyssa runs exactly like before, console-only.

Her default look is PNGTuber-style: a "not talking" (mouth closed) image
and a "talking" (mouth open) image (bundled in assets/) that swap back and
forth while she speaks, with an optional little bounce and an optional dim
while she's idle -- all configurable from Settings -> Companion. Swap in
your own images any time (.png, .jpg, .gif, or .svg all work; animated
.gif files will actually animate). If no bundled/custom art is found, she
falls back to a small original chibi-style character drawn as SVG right in
this file (nothing copyrighted).
"""

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
    qInstallMessageHandler, QtMsgType,
)


def _qt_message_filter(msg_type, context, message):
    """Filter out noisy DPI warning messages."""
    if "SetProcessDpiAwarenessContext" in message:
        return
    sys.stderr.write(message + "\n")
    if msg_type == QtMsgType.QtFatalMsg:
        sys.exit(1)


qInstallMessageHandler(_qt_message_filter)


def _print_uncaught_exception(exc_type, exc_value, exc_tb):
    """Print uncaught exceptions in Qt event loops to console."""
    import traceback
    traceback.print_exception(exc_type, exc_value, exc_tb)


sys.excepthook = _print_uncaught_exception

from PySide6.QtGui import (
    QPixmap,
    QPainter,
    QColor,
    QRegion,
    QGuiApplication,
    QMovie,
    QPainterPath,
    QLinearGradient,
    QFont,
    QFontDatabase,

    QIcon,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QMenu,
    QDialog,
    QFormLayout,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLineEdit,
    QComboBox,
    QSizePolicy,
    QCheckBox,
    QSlider,
    QSpinBox,
    QPushButton,
    QLabel,
    QFileDialog,
    QMessageBox,
    QInputDialog,
    QStackedWidget,
    QSystemTrayIcon,
    QScrollArea,
    QFrame,
    QSplitter,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
)
_QW = QWidget


import actions
import brain
import config
import plugin_loader

# --------------------------------------------------------------------------
# Persistent *companion* settings (position, size, appearance). Kept
# separate from config.py, which holds the assistant's brain/voice
# settings and is meant to be hand-edited -- this file is GUI-managed.
# --------------------------------------------------------------------------
if getattr(sys, "frozen", False):
    _BASE_DIR = os.path.dirname(sys.executable)
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))

OVERLAY_CONFIG_FILE = os.path.join(_BASE_DIR, "overlay_config.json")

# Bundled pngtuber-style art (mouth closed / mouth open). Default look when
# the user hasn't picked their own images.
ASSETS_DIR = os.path.join(_BASE_DIR, "assets")
BUNDLED_IDLE_IMAGE = os.path.join(ASSETS_DIR, "nottalk.png")
BUNDLED_TALK_IMAGE = os.path.join(ASSETS_DIR, "talkopen.png")

BASE_W, BASE_H = 180, 255  # native size at scale = 1.0
MIN_W = 90
RESIZE_GRIP = 22

DEFAULT_OVERLAY_SETTINGS = {
    "pos_x": None,
    "pos_y": None,
    "scale": 1.0,
    "opacity": 1.0,
    "always_on_top": True,
    "character_image": "",  # "" = bundled idle art if present, else built-in chibi
    "character_image_talking": "",  # "" = bundled talk art if present, else same as idle
    "bubble_seconds": 7,
    # -- PNGTuber-style talk animation, all configurable from Settings --
    "talk_mouth_flap_enabled": True,  # swap idle/talking image while she speaks
    "talk_bounce_enabled": True,  # little bounce while she speaks
    "talk_bounce_height": 0,  # px, at scale = 1.0
    "dim_when_idle_enabled": False,  # dim her out while she's not talking
    "dim_when_idle_opacity": 55,  # percent (of the base "Opacity" setting)
    "dark_mode": False,  # dark theme for the Settings window, menus, and speech bubble
    "color_theme": "dark",  # default preset key from COLOR_THEMES
    "chatbox_enabled": True,  # show the typed-command box below/beside her
    "chatbox_position": "bottom",  # "bottom" | "left" | "right"
}




def load_overlay_settings() -> dict:
    settings = dict(DEFAULT_OVERLAY_SETTINGS)
    if os.path.exists(OVERLAY_CONFIG_FILE):
        try:
            with open(OVERLAY_CONFIG_FILE, "r", encoding="utf-8") as f:
                saved_settings = json.load(f)
            if isinstance(saved_settings, dict):
                settings.update(saved_settings)
        except (json.JSONDecodeError, OSError):
            pass
    return settings


def save_overlay_settings(settings: dict):
    tmp_path = OVERLAY_CONFIG_FILE + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
        os.replace(tmp_path, OVERLAY_CONFIG_FILE)
    except OSError:
        pass  # non-fatal -- worst case, position/size just doesn't persist





# Cache of already-rendered character pixmaps. Without this, every
# animation tick (mouth flap runs on a 50ms QTimer while talking, ~6-7x/sec)
# would re-decode the image file from disk. Keyed on everything that
# affects pixel output, including the source file's mtime, so swapping in
# a new image is picked up immediately. Capped to bound memory use.
_render_cache: dict = {}
_RENDER_CACHE_MAX = 32


def _cache_get_or_render(key, render_fn):
    cached = _render_cache.get(key)
    if cached is not None:
        return cached
    pixmap = render_fn()
    if len(_render_cache) >= _RENDER_CACHE_MAX:
        _render_cache.pop(next(iter(_render_cache)))  # evict oldest
    _render_cache[key] = pixmap
    return pixmap


def render_svg(svg_text: str, size: QSize) -> QPixmap:
    pixmap = QPixmap(size)
    pixmap.fill(Qt.transparent)
    renderer = QSvgRenderer(bytearray(svg_text, "utf-8"))
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return pixmap


def _resolve_character_path(settings: dict, mouth_open: bool) -> str:
    """Picks which image file (if any) should be shown right now, given
    the talk state - shared by render_character() (static rendering /
    caching) and CompanionWindow's live GIF playback, so both agree on
    which file is "the current one" without duplicating the fallback
    order in two places."""
    idle_path = settings.get("character_image") or ""
    talk_path = settings.get("character_image_talking") or ""
    if mouth_open:
        candidates = [talk_path, BUNDLED_TALK_IMAGE, idle_path, BUNDLED_IDLE_IMAGE]
    else:
        candidates = [idle_path, BUNDLED_IDLE_IMAGE]
    return next((p for p in candidates if p and os.path.exists(p)), "")


def _scale_centered(src: QPixmap, size: QSize) -> QPixmap:
    """Scales src to fit within size (preserving aspect ratio) and
    centers it on a transparent canvas of exactly size - the same
    treatment every character image gets, whether it's a static file or
    one frame of a playing GIF."""
    scaled = src.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    out = QPixmap(size)
    out.fill(Qt.transparent)
    painter = QPainter(out)
    x = (size.width() - scaled.width()) // 2
    y = (size.height() - scaled.height()) // 2
    painter.drawPixmap(x, y, scaled)
    painter.end()
    return out


def render_character(settings: dict, size: QSize, mouth_open: bool = False) -> QPixmap:
    """Renders the current character (built-in or user-supplied image) at
    the given size, preserving aspect ratio and centering it.

    mouth_open picks between the "talking" image and the "idle" image, for
    the PNGTuber-style talk/not-talk swap. If the user hasn't picked custom
    images, falls back to the bundled assets/nottalk.png + assets/talkopen.png
    when present, and finally to the built-in blinking chibi SVG.

    For an animated .gif, this always returns just its first frame -
    used for one-off renders (the Settings preview thumbnail) where an
    unmoving image is fine. CompanionWindow itself does NOT call this for
    an active .gif; it drives real frame-by-frame playback via QMovie
    instead (see CompanionWindow._frame_from_movie) so she actually
    animates on screen rather than freezing on frame 1."""
    custom_path = _resolve_character_path(settings, mouth_open)

    if custom_path:
        try:
            mtime = os.path.getmtime(custom_path)
        except OSError:
            mtime = 0
        cache_key = ("file", custom_path, mtime, size.width(), size.height())

        def _render_file():
            ext = os.path.splitext(custom_path)[1].lower()
            if ext == ".svg":
                try:
                    with open(custom_path, "r", encoding="utf-8") as f:
                        return render_svg(f.read(), size)
                except OSError:
                    pass
            elif ext == ".gif":
                movie = QMovie(custom_path)
                movie.jumpToFrame(0)
                src = movie.currentPixmap()
                if src and not src.isNull():
                    return _scale_centered(src, size)
            else:
                src = QPixmap(custom_path)
                if src and not src.isNull():
                    return _scale_centered(src, size)
            # ponytail: transparent fallback if file can't load
            out = QPixmap(size)
            out.fill(Qt.transparent)
            return out

        return _cache_get_or_render(cache_key, _render_file)

    # No custom path and bundled assets missing — transparent placeholder.
    out = QPixmap(size)
    out.fill(Qt.transparent)
    return out


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


# --------------------------------------------------------------------------
# Light / dark theme palettes, shared by the Settings dialog, the
# right-click menus, the setup dialog, and the speech bubble.
# --------------------------------------------------------------------------
# Color Theme Presets
# --------------------------------------------------------------------------
def _load_color_themes() -> dict:
    """Load themes from color_themes.json next to this file, converting
    bubble_top/bottom/border/shadow from JSON arrays back to tuples."""
    json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "color_themes.json")
    _TUPLE_KEYS = ("bubble_top", "bubble_bottom", "bubble_border", "bubble_shadow")
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        for theme in raw.values():
            for k in _TUPLE_KEYS:
                if k in theme and isinstance(theme[k], list):
                    theme[k] = tuple(theme[k])
        return raw
    except (OSError, json.JSONDecodeError):
        # Hardcoded minimal fallback so the app still starts.
        return {
            "dark": {
                "name": "Dark Indigo",
                "bg": "#0B0F19", "panel": "#151C2C", "card": "#1E293B", "border": "#2E3B52",
                "text": "#F8FAFC", "subtext": "#94A3B8",
                "accent": "#6366F1", "accent_hover": "#818CF8", "accent_press": "#4F46E5", "accent2": "#38BDF8",
                "header_grad_start": "#0F172A", "header_grad_end": "#1E1B4B",
                "menu_hover": "#243047", "slider_groove": "#1E293B", "slider_fill": "#6366F1",
                "success": "#10B981", "warning": "#F59E0B", "danger": "#EF4444",
                "bubble_top": (21, 28, 44, 245), "bubble_bottom": (15, 20, 32, 245),
                "bubble_border": (99, 102, 241, 190), "bubble_text": "#F8FAFC", "bubble_shadow": (2, 6, 12),
            },
            "light": {
                "name": "Light Slate",
                "bg": "#F8FAFC", "panel": "#FFFFFF", "card": "#F1F5F9", "border": "#E2E8F0",
                "text": "#0F172A", "subtext": "#64748B",
                "accent": "#4F46E5", "accent_hover": "#6366F1", "accent_press": "#3730A3", "accent2": "#0EA5E9",
                "header_grad_start": "#0F172A", "header_grad_end": "#1E293B",
                "menu_hover": "#F1F5F9", "slider_groove": "#E2E8F0", "slider_fill": "#4F46E5",
                "success": "#10B981", "warning": "#F59E0B", "danger": "#EF4444",
                "bubble_top": (255, 255, 255, 250), "bubble_bottom": (248, 250, 252, 250),
                "bubble_border": (79, 70, 229, 180), "bubble_text": "#0F172A", "bubble_shadow": (15, 23, 42),
            },
        }


COLOR_THEMES = _load_color_themes()

_THEME_LIGHT = COLOR_THEMES["light"]
_CODE_EDITOR_COLORS = {"bg": "#1e1e2e", "text": "#cdd6f4", "selection": "#45475a"}

# Settings dialog outer corner radius.
_DIALOG_RADIUS = 6

# Single type scale for ConfigDialog._build_style. Was previously ad-hoc
# literals (11, 12, 12.5, 13px for text that reads at the same visual
# weight) scattered across ~30 QSS blocks, drifting a half-pixel at a time
# with no source of truth. Five steps, ~1.15 ratio, is all a settings
# dialog needs: eyebrow labels -> hints -> body -> emphasis -> title.
TYPE_SCALE = {
    "eyebrow": 10.5,  # QLabel#sectionHeader/-First letter-spaced labels
    "hint": 11.5,      # secondary/status/note labels
    "body": 13,        # QLabel, QCheckBox, default field text
}


def _rgba(hex_color: str, alpha: float) -> str:
    """'#RRGGBB' -> 'rgba(r, g, b, alpha)'. Lets the glass surfaces in
    ConfigDialog._build_style dial in translucency straight from the
    existing hex theme palette instead of hand-picking new colors."""
    hex_color = hex_color.lstrip("#")
    r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    return f"rgba({r}, {g}, {b}, {alpha})"


def _theme(val) -> dict:
    """Returns a theme palette dictionary matching a string key, boolean, or dict."""
    if isinstance(val, str):
        key = val.lower().strip()
        if key in COLOR_THEMES:
            return COLOR_THEMES[key]
    if isinstance(val, bool):
        return COLOR_THEMES["dark"] if val else COLOR_THEMES["light"]
    if isinstance(val, dict):
        theme_key = val.get("color_theme")
        if isinstance(theme_key, str) and theme_key.lower() in COLOR_THEMES:
            return COLOR_THEMES[theme_key.lower()]
        return COLOR_THEMES["dark"] if val.get("dark_mode", False) else COLOR_THEMES["light"]
    return COLOR_THEMES["dark"]


def _apply_elevation(widget: "_QW", blur: int = 28, y: int = 6, alpha: int = 110) -> QGraphicsDropShadowEffect:
    """Gives one widget its own soft drop shadow so it reads as a raised
    card instead of a flat glass panel. QSS has no box-shadow equivalent,
    so this has to happen in Python per-widget (Qt also only allows one
    QGraphicsEffect per widget - don't call this twice on the same one).
    The dialog itself already gets a bigger/softer version of this in
    ConfigDialog.__init__; this is for the cards *inside* it (tab pane,
    plugin list) that currently have a border but no depth of their own."""
    shadow = QGraphicsDropShadowEffect(widget)
    shadow.setBlurRadius(blur)
    shadow.setOffset(0, y)
    shadow.setColor(QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(shadow)
    return shadow


class _HoverPress(QObject):
    """Soft micro-interaction for a single QPushButton: a quick opacity
    dip on press, eased back on release. QSS :hover/:pressed selectors
    (already used throughout _build_style) snap instantly - there's no
    QSS transition property in Qt - so this is the lightweight way to add
    the "soft" feel that was asked for without hand-animating every
    widget. Installed as an event filter rather than subclassing
    QPushButton, so it can be dropped onto any existing button in one
    line: `_HoverPress(btn)` (keep a reference, e.g. `btn._press_fx =
    _HoverPress(btn)`, or it gets garbage-collected and stops working).
    """

    def __init__(self, button: QPushButton, floor: float = 0.82, ms: int = 90):
        super().__init__(button)
        self._button = button
        self._effect = QGraphicsOpacityEffect(button)
        self._effect.setOpacity(1.0)
        button.setGraphicsEffect(self._effect)
        self._anim = QPropertyAnimation(self._effect, b"opacity", self)
        self._anim.setDuration(ms)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._floor = floor
        button.installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj is self._button:
            if event.type() == event.Type.MouseButtonPress:
                self._to(self._floor)
            elif event.type() in (event.Type.MouseButtonRelease, event.Type.Leave):
                self._to(1.0)
        return False

    def _to(self, value: float):
        self._anim.stop()
        self._anim.setStartValue(self._effect.opacity())
        self._anim.setEndValue(value)
        self._anim.start()




def _build_menu_style(val) -> str:
    t = _theme(val)
    return f"""
        QMenu {{
            background: {_rgba(t['panel'], 0.97)};
            border: 1px solid {_rgba(t['border'], 0.8)};
            border-radius: 4px;
            padding: 6px;
            color: {t['text']};
            font-family: 'Segoe UI Variable Text', 'Segoe UI', -apple-system, BlinkMacSystemFont, 'Inter', sans-serif;
            font-size: 13px;
        }}
        QMenu::item {{
            padding: 8px 22px 8px 14px;
            border-radius: 3px;
            margin: 2px;
            color: {t['text']};
        }}
        QMenu::item:selected {{
            background: {_rgba(t['accent'], 0.28)};
            color: {t['accent']};
        }}
        QMenu::separator {{
            height: 1px;
            background: {t['border']};
            margin: 6px 8px;
        }}
    """


def _build_messagebox_style(val) -> str:
    t = _theme(val)
    return (
        f"QMessageBox {{ background: {t['bg']}; font-family: 'Segoe UI Variable Text', 'Segoe UI', -apple-system, BlinkMacSystemFont, 'Inter', sans-serif; }}"
        f"QLabel {{ color: {t['text']}; font-size: 13px; }}"
        f"QPushButton {{"
        f"  background: {t['accent']}; color: #FFFFFF; border: none;"
        f"  border-radius: 4px; padding: 8px 20px; font-weight: 600; min-width: 64px;"
        f"}}"
        f"QPushButton:hover {{ background: {t['accent_hover']}; }}"
        f"QPushButton:pressed {{ background: {t['accent_press']}; }}"
    )


# --------------------------------------------------------------------------
# Speech bubble
#
# A custom-painted card (rounded corners, soft shadow, gradient, tail
# pointing down at her) that fades + slides in when she starts talking and
# fades out shortly after she stops, synced to how long she actually took
# to say the line rather than a fixed timer.
# --------------------------------------------------------------------------
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


# --------------------------------------------------------------------------
# Chat input bar
#
# A small always-visible text box below the companion, for anyone who
# can't or would rather not speak commands out loud. Typed text goes
# through the same brain.handle_command() pipeline as spoken commands (see
# main.py's run_assistant_loop) via bridge.text_queue, and skips the "say
# typing directly into her chat box is already unambiguous.
# --------------------------------------------------------------------------
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


# --------------------------------------------------------------------------
# The character herself
# --------------------------------------------------------------------------
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
            self.tray.setIcon(_build_app_icon(theme=_theme(self.settings)))
        save_overlay_settings(self.settings)


# --------------------------------------------------------------------------
# Settings dialog (right-click -> Settings...)
# --------------------------------------------------------------------------
def _volume_str_to_percent(value: str, default: int = 0) -> int:
    """Parses config.EDGE_TTS_VOLUME's edge-tts format ('+0%', '-15%',
    '20%') into a plain int for the slider. Falls back to `default` for
    anything unexpected rather than raising, since this runs while just
    building the Settings window."""
    try:
        return int(str(value).strip().rstrip("%"))
    except (TypeError, ValueError):
        return default


def _percent_to_volume_str(percent: int) -> str:
    """Inverse of _volume_str_to_percent - always includes an explicit
    sign (edge-tts accepts '+0%' but this keeps it consistent with the
    +N%/-N% style already used throughout config.py's own comments)."""
    return f"{percent:+d}%"


def _is_http_url(value: str) -> bool:
    if not value:
        return True
    try:
        parsed = urlsplit(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    except ValueError:
        return False


def _verify_gemini_key(api_key: str) -> "tuple[bool, str, list]":
    """Checks whether `api_key` is accepted by Gemini, without spending any
    of the key's generation quota the way an actual chat/vision call would.
    Uses the free "list models" endpoint purely as a cheap auth probe - and,
    since that same response already lists every model the key can use,
    also returns that list so the Settings UI can offer it as a dropdown
    instead of a free-text guess.

    Returns (is_valid, message, models) - message is empty on success, or a
    short human-readable reason on failure; models is a sorted list of
    model names usable for chat (empty on failure or if none qualify).
    Runs a blocking network call, so the caller is expected to run this off
    the GUI thread."""
    try:
        resp = requests.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            params={"key": api_key},
            timeout=10,
        )
    except requests.exceptions.RequestException:
        return False, "Couldn't reach Gemini - check your internet connection.", []

    if resp.status_code == 200:
        models = []
        try:
            for m in resp.json().get("models", []):
                # Only ones that actually support a chat-style call - the
                # list also includes embedding/imagen/etc. models that
                # would just fail if picked here.
                if "generateContent" not in m.get("supportedGenerationMethods", []):
                    continue
                name = m.get("name", "")
                models.append(name.split("/", 1)[1] if "/" in name else name)
        except ValueError:
            pass
        return True, "", sorted(set(models), reverse=True)

    try:
        detail = resp.json().get("error", {}).get("message", "")
    except ValueError:
        detail = ""
    if resp.status_code in (400, 401, 403):
        return False, detail or "Gemini rejected this key.", []
    return False, detail or f"Gemini returned an error ({resp.status_code}).", []


def _verify_openai_key(api_key: str) -> "tuple[bool, str, list]":
    """Same idea as _verify_gemini_key above, but against OpenAI's free
    "list models" endpoint. See that function's docstring for the return
    contract."""
    try:
        resp = requests.get(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
    except requests.exceptions.RequestException:
        return False, "Couldn't reach OpenAI - check your internet connection.", []

    if resp.status_code == 200:
        models = []
        # OpenAI's /v1/models also lists non-chat models (embeddings,
        # Whisper, TTS, DALL-E, moderation) that would just fail here -
        # filter those out so the dropdown only shows usable chat models.
        _non_chat_markers = ("embedding", "whisper", "tts", "dall-e", "moderation", "davinci-", "babbage-")
        try:
            for m in resp.json().get("data", []):
                model_id = m.get("id", "")
                if model_id and not any(marker in model_id for marker in _non_chat_markers):
                    models.append(model_id)
        except ValueError:
            pass
        return True, "", sorted(set(models), reverse=True)

    try:
        detail = resp.json().get("error", {}).get("message", "")
    except ValueError:
        detail = ""
    if resp.status_code in (400, 401, 403):
        return False, detail or "OpenAI rejected this key.", []
    return False, detail or f"OpenAI returned an error ({resp.status_code}).", []


def _verify_anthropic_key(api_key: str) -> "tuple[bool, str, list]":
    """Same idea as _verify_gemini_key above, but against Anthropic's free
    "list models" endpoint. See that function's docstring for the return
    contract."""
    try:
        resp = requests.get(
            "https://api.anthropic.com/v1/models",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
            timeout=10,
        )
    except requests.exceptions.RequestException:
        return False, "Couldn't reach Anthropic - check your internet connection.", []

    if resp.status_code == 200:
        models = []
        try:
            # Anthropic returns these newest-first already - keep that
            # order rather than re-sorting alphabetically, since it's more
            # useful here (newest model shown first).
            models = [m.get("id", "") for m in resp.json().get("data", []) if m.get("id")]
        except ValueError:
            pass
        return True, "", models

    try:
        detail = resp.json().get("error", {}).get("message", "")
    except ValueError:
        detail = ""
    if resp.status_code in (400, 401, 403):
        return False, detail or "Anthropic rejected this key.", []
    return False, detail or f"Anthropic returned an error ({resp.status_code}).", []


def _verify_spotify_credentials(client_id: str, client_secret: str) -> "tuple[bool, str]":
    """Checks whether `client_id`/`client_secret` are accepted by Spotify's
    client-credentials token endpoint - the exact same request
    actions._get_spotify_token() makes, so a green check here means
    play_music's Spotify lookups will actually work."""
    try:
        resp = requests.post(
            "https://accounts.spotify.com/api/token",
            data={"grant_type": "client_credentials"},
            auth=(client_id, client_secret),
            timeout=10,
        )
    except requests.exceptions.RequestException:
        return False, "Couldn't reach Spotify - check your internet connection."

    if resp.status_code == 200:
        return True, ""
    try:
        detail = resp.json().get("error_description", "")
    except ValueError:
        detail = ""
    if resp.status_code in (400, 401, 403):
        return False, detail or "Spotify rejected this Client ID/Secret."
    return False, detail or f"Spotify returned an error ({resp.status_code})."


def _verify_youtube_key(api_key: str) -> "tuple[bool, str]":
    """Checks whether `api_key` is accepted by the YouTube Data API v3.
    Uses the videoCategories endpoint rather than search - it costs a
    fraction of the API quota (1 unit vs. search's 100), so a Verify click
    doesn't eat into the same daily quota play_music's actual lookups use."""
    try:
        resp = requests.get(
            "https://www.googleapis.com/youtube/v3/videoCategories",
            params={"part": "snippet", "regionCode": "US", "key": api_key},
            timeout=10,
        )
    except requests.exceptions.RequestException:
        return False, "Couldn't reach YouTube - check your internet connection."

    if resp.status_code == 200:
        return True, ""
    try:
        detail = resp.json().get("error", {}).get("message", "")
    except ValueError:
        detail = ""
    if resp.status_code in (400, 401, 403):
        return False, detail or "YouTube rejected this key."
    return False, detail or f"YouTube returned an error ({resp.status_code})."


def _fetch_custom_openai_models(base_url: str, api_key: str) -> "tuple[bool, str, list]":
    """Lists models from a custom OpenAI-compatible endpoint (Groq,
    OpenRouter, Together, a local LM Studio/vLLM server, etc.) by hitting
    its standard GET {base_url}/models route - the same endpoint OpenAI's
    own client uses, which most compatible providers implement too.

    Returns (ok, message, models) - same contract as _verify_gemini_key,
    except message is also used for a non-fatal note on success (e.g. a
    server that doesn't support this endpoint at all)."""
    base_url = (base_url or "").rstrip("/")
    if not base_url:
        return False, "Enter a base URL first.", []

    url = base_url if base_url.endswith("/models") else f"{base_url}/models"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
    except requests.exceptions.RequestException as e:
        return False, f"Couldn't reach that server - {e}", []

    if resp.status_code != 200:
        try:
            detail = resp.json().get("error", {}).get("message", "")
        except ValueError:
            detail = ""
        if resp.status_code in (400, 401, 403):
            return False, detail or "That server rejected the request/key.", []
        return False, detail or f"Server returned an error ({resp.status_code}).", []

    try:
        data = resp.json()
    except ValueError:
        return False, "Server responded, but not with valid JSON.", []

    # Most OpenAI-compatible servers use {"data": [{"id": ...}, ...]},
    # same shape as OpenAI itself - fall back to a bare list of strings/
    # dicts in case a server returns something slightly different.
    raw = data.get("data") if isinstance(data, dict) else data
    models = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get("id"):
                models.append(item["id"])
            elif isinstance(item, str):
                models.append(item)

    if not models:
        return False, "Connected, but no models were listed - this server may not support that.", []
    return True, "", sorted(set(models))


def _elevenlabs_voice_display(v: dict) -> str:
    """Combo-box text for one ElevenLabs voice - shows the human name but
    keeps the voice_id recoverable (see _extract_elevenlabs_voice_id)
    since the id, not the name, is what actually goes in config.py."""
    name = v.get("name") or v.get("voice_id", "")
    category = f" ({v['category']})" if v.get("category") else ""
    return f"{name}{category} — {v.get('voice_id', '')}"


def _extract_elevenlabs_voice_id(text: str) -> str:
    """Reverses _elevenlabs_voice_display - pulls the voice_id back out of
    a combo entry. A bare id (no ' — ' separator), e.g. one typed in by
    hand or loaded straight from config.py, passes through unchanged."""
    text = (text or "").strip()
    if " — " in text:
        return text.rsplit(" — ", 1)[-1].strip()
    return text


def _verify_elevenlabs_key(api_key: str) -> "tuple[bool, str, list]":
    """Fetches the account's ElevenLabs voice list as both the auth check
    and the data the Settings voice dropdown needs - there's no separate
    cheap auth-only probe, same as the custom OpenAI-compatible provider
    above. Returns (ok, message, display_strings) - display_strings are
    _elevenlabs_voice_display() text, ready to drop straight into the
    voice combo."""
    import voice as voice_module
    try:
        voices = voice_module.list_elevenlabs_voices(api_key)
    except requests.exceptions.RequestException as e:
        return False, f"Couldn't reach ElevenLabs - {e}", []
    except RuntimeError as e:
        return False, str(e), []
    if not voices:
        return False, "Connected, but no voices are available on this account.", []
    return True, "", [_elevenlabs_voice_display(v) for v in voices]


def _patch_config_line(text: str, key: str, value_literal: str) -> str:
    """Replaces the value of a top-level `KEY = ...` assignment in
    config.py's source text, preserving any trailing inline comment. Adds
    the line at the end if the key isn't already present."""
    import re

    pattern = re.compile(rf"(?m)^{re.escape(key)}\s*=\s*.*?(\s*#.*)?$")

    def _sub(m):
        comment = m.group(1) or ""
        return f"{key} = {value_literal}{comment}"

    new_text, n = pattern.subn(_sub, text, count=1)
    if n == 0:
        new_text = text.rstrip("\n") + f"\n{key} = {value_literal}\n"
    return new_text


def _atomic_write_text(path: str, text: str) -> None:
    """Replace a text file only after its complete new contents reach disk."""
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except OSError:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


class _StatusLabel(QLabel):
    """A QLabel that hides itself whenever its text is empty, so an
    empty status line doesn't leave a blank gap in the layout."""

    def setText(self, text):
        super().setText(text)
        self.setVisible(bool(text))


class _SidebarTabs(QWidget):
    """Horizontal tab text in a compact vertical navigation rail."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebarTabs")

        self.navigation = QListWidget()
        self.navigation.setObjectName("settingsNavigation")
        self.navigation.setMinimumWidth(120)
        self.navigation.setMaximumWidth(190)
        self.navigation.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self.navigation.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.navigation.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.pages = QStackedWidget()
        self.pages.setObjectName("settingsPages")
        self.pages.setMinimumWidth(0)
        self.pages.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.navigation)
        layout.addWidget(self.pages, 1)

    def addTab(self, page: QWidget, label: str):
        item = QListWidgetItem(label)
        item.setSizeHint(QSize(0, 50))
        self.navigation.addItem(item)
        self.pages.addWidget(page)
        if self.navigation.currentRow() < 0:
            self.navigation.setCurrentRow(0)

    def setCurrentIndex(self, index: int):
        self.navigation.setCurrentRow(index)

    def fit_navigation(self, available_width: int):
        """Keep the rail proportional without letting it crowd the form."""
        self.navigation.setFixedWidth(max(120, min(190, round(available_width * 0.22))))


class VoiceBrowserDialog(QDialog):
    """Search-and-pick dialog over the full Microsoft Edge neural voice
    catalog (400+ voices, ~140 locales) - opened from Settings ->
    Assistant -> Voice & Behavior -> "Browse all voices". The catalog is
    fetched from Microsoft on a background thread the first time this is
    opened (voice.list_edge_voices() caches it after that, so reopening
    the dialog later is instant). Typing in the search bar filters live
    across short name, locale, gender, and friendly name - e.g. "en-GB",
    "female", or "Aria" all narrow the list."""

    voice_picked = Signal(str)
    _voices_loaded_signal = Signal(bool, str, list)

    def _t(self) -> dict:
        """This dialog's theme dict, matching whatever light/dark mode
        Settings is currently in - kept as a helper so every status-label
        color pulls from the same palette instead of a stray hex code."""
        return _theme(self._dark)

    def __init__(self, parent, dark: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Browse Edge TTS voices")
        self.resize(560, 520)
        self._all_voices = []
        self._dark = dark
        self.setStyleSheet(ConfigDialog._build_style(dark))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search by name, locale, or gender… e.g. \"en-GB\", \"female\", \"Aria\"")
        self.search_edit.textChanged.connect(self._apply_filter)
        layout.addWidget(self.search_edit)

        self.status_label = QLabel("Loading voice catalog from Microsoft…")
        self.status_label.setStyleSheet(f"color: {self._t()['subtext']}; font-size: 11px;")
        layout.addWidget(self.status_label)

        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(self._on_double_click)
        layout.addWidget(self.list_widget, 1)

        btn_row = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setObjectName("compactButton")
        self.refresh_btn.clicked.connect(self._start_fetch_refresh)
        select_btn = QPushButton("Select")
        select_btn.clicked.connect(self._on_select_clicked)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self.refresh_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(select_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        self._voices_loaded_signal.connect(self._on_voices_loaded)
        self._start_fetch(force_refresh=False)

    def _start_fetch(self, force_refresh: bool):
        self.list_widget.clear()
        self.status_label.setText(
            "Refreshing voice catalog from Microsoft…" if force_refresh else "Loading voice catalog from Microsoft…"
        )
        threading.Thread(target=self._do_fetch, args=(force_refresh,), daemon=True).start()

    def _start_fetch_refresh(self):
        self._start_fetch(force_refresh=True)

    def _do_fetch(self, force_refresh: bool):
        # Runs off the GUI thread - list_edge_voices() is a blocking
        # network call (unless already cached from an earlier open).
        import voice as voice_module
        try:
            voices = voice_module.list_edge_voices(force_refresh=force_refresh)
            self._voices_loaded_signal.emit(True, "", voices)
        except Exception as e:
            self._voices_loaded_signal.emit(False, str(e), [])

    def _on_voices_loaded(self, ok: bool, message: str, voices: list):
        if not ok:
            self.status_label.setStyleSheet(f"font-size: 11px; color: {self._t()['danger']};")
            self.status_label.setText(f"✗ Couldn't load the voice catalog - {message}")
            return
        self._all_voices = voices
        self.status_label.setStyleSheet(f"color: {self._t()['subtext']}; font-size: 11px;")
        self._apply_filter()

    def _apply_filter(self, *args):
        query = self.search_edit.text().strip().lower()
        self.list_widget.clear()
        shown = 0
        for v in self._all_voices:
            short_name = v.get("ShortName", "")
            locale = v.get("Locale", "")
            gender = v.get("Gender", "")
            friendly = v.get("FriendlyName", "")
            haystack = f"{short_name} {locale} {gender} {friendly}".lower()
            if query and query not in haystack:
                continue
            label = f"{short_name}   ·   {locale}   ·   {gender}"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, short_name)
            self.list_widget.addItem(item)
            shown += 1
        if self._all_voices:
            total = len(self._all_voices)
            self.status_label.setText(
                f"Showing {shown} of {total} voices" if query else f"{total} voices loaded"
            )

    def _current_short_name(self):
        item = self.list_widget.currentItem()
        if item is None and self.list_widget.count() == 1:
            item = self.list_widget.item(0)
        return item.data(Qt.UserRole) if item is not None else None

    def _on_select_clicked(self):
        short_name = self._current_short_name()
        if not short_name:
            self.status_label.setStyleSheet(f"font-size: 11px; color: {self._t()['danger']};")
            self.status_label.setText("Pick a voice from the list first (or search until only one is left).")
            return
        self.voice_picked.emit(short_name)
        self.accept()

    def _on_double_click(self, item: QListWidgetItem):
        short_name = item.data(Qt.UserRole)
        if short_name:
            self.voice_picked.emit(short_name)
            self.accept()


class ConfigDialog(QDialog):
    # Background checks emit here so Qt queues the result onto the GUI thread.
    _key_verified_signal = Signal(str, bool, str, list)
    # Custom OpenAI-compatible server - fetching its model list doubles as
    # its "verify" step, since there's no separate cheap auth-only probe.
    _custom_models_fetched_signal = Signal(bool, str, list)
    # Spotify's pair is verified together as one signal, since the token
    # request needs both at once.
    _spotify_verified_signal = Signal(bool, str)

    # A cohesive theme matching the companion/speech-bubble palette,
    # built from the shared color themes so the dialog, speech bubble, and
    # menus use one source of truth.
    @staticmethod
    def _build_style(dark: bool) -> str:
        t = _theme(dark)
        # Reused radii/opacity so every surface reads as one material
        # instead of each block picking its own numbers. Kept modest
        # (not pill-shaped) and near-opaque (solid cards, not see-through
        # glass) per the flat/cute redesign.
        r_xl, r_lg, r_md, r_sm = _DIALOG_RADIUS, 6, 4, 3
        f_eyebrow, f_hint, f_body = (
            TYPE_SCALE["eyebrow"], TYPE_SCALE["hint"], TYPE_SCALE["body"],
        )
        glass_1 = _rgba(t['panel'], 0.97)   # dialog body, tab pane
        glass_2 = _rgba(t['card'], 0.94)    # inputs, buttons, list rows
        glass_3 = _rgba(t['card'], 1.0)     # focused/selected surfaces
        edge = "rgba(255, 255, 255, 0.10)" if dark else "rgba(15, 23, 42, 0.10)"
        edge_soft = "rgba(255, 255, 255, 0.06)" if dark else "rgba(15, 23, 42, 0.06)"
        return f"""
        * {{
            font-family: 'Segoe UI Variable Text', 'Segoe UI', -apple-system, BlinkMacSystemFont, 'Inter', sans-serif;
        }}
        QDialog {{
            background: {t['bg']};
        }}
        QWidget#tabPage {{
            background: transparent;
        }}
        QWidget#sidebarTabs {{
            background: {t['panel']};
            border: 1px solid {edge};
            border-radius: {r_lg}px;
        }}
        QListWidget#settingsNavigation {{
            background: {t['bg']};
            border: none;
            border-right: 1px solid {edge};
            border-top-left-radius: {r_lg}px;
            border-bottom-left-radius: {r_lg}px;
            padding: 12px 0;
            outline: none;
        }}
        QListWidget#settingsNavigation::item {{
            color: {t['subtext']};
            border: none;
            border-left: 3px solid transparent;
            padding: 0 18px;
            font-size: {f_body}px;
            font-weight: 600;
        }}
        QListWidget#settingsNavigation::item:selected {{
            color: {t['text']};
            background: {t['card']};
            border-left: 3px solid {t['accent']};
        }}
        QListWidget#settingsNavigation::item:hover:!selected {{
            color: {t['text']};
            background: {edge_soft};
        }}
        QStackedWidget#settingsPages {{
            background: {t['panel']};
            border: none;
            border-top-right-radius: {r_lg}px;
            border-bottom-right-radius: {r_lg}px;
        }}
        QScrollArea {{
            background: transparent;
            border: none;
        }}
        QScrollArea > QWidget > QWidget {{
            background: transparent;
        }}
        QLabel {{
            color: {t['text']};
            font-size: {f_body}px;
            background: transparent;
        }}
        QWidget#dialogContent {{
            background: {glass_1};
            border-radius: {r_xl}px;
            border: 1px solid {edge};
        }}
        QLabel#sectionHeader {{
            color: {t['accent']};
            font-size: {f_eyebrow}px;
            font-weight: 700;
            letter-spacing: 1.2px;
            padding-top: 12px;
            padding-bottom: 5px;
            border-top: 1px solid {edge};
            margin-top: 8px;
        }}
        QLabel#sectionHeaderFirst {{
            color: {t['accent']};
            font-size: {f_eyebrow}px;
            font-weight: 700;
            letter-spacing: 1.2px;
            padding-bottom: 5px;
        }}
        QLineEdit, QComboBox, QSpinBox {{
            background: {glass_2};
            border: 1px solid {edge};
            border-radius: {r_md}px;
            padding: 8px 13px;
            color: {t['text']};
            font-size: {f_hint}px;
            selection-background-color: {t['accent']};
            selection-color: #FFFFFF;
        }}
        QLineEdit:hover, QComboBox:hover, QSpinBox:hover {{
            border: 1px solid {t['accent_hover']};
        }}
        QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
            border: 1.5px solid {t['accent']};
            background: {glass_3};
        }}
        QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{
            color: {t['subtext']};
            background: {edge_soft};
            opacity: 0.6;
        }}
        QComboBox::drop-down {{
            border: none;
            width: 24px;
        }}
        QComboBox::down-arrow {{
            image: none;
            width: 0px;
            height: 0px;
            border-left: 4px solid transparent;
            border-right: 4px solid transparent;
            border-top: 5px solid {t['subtext']};
            margin-right: 9px;
        }}
        QComboBox::down-arrow:on {{
            border-top: 5px solid {t['accent']};
        }}
        QComboBox QAbstractItemView {{
            background: {glass_3};
            border: 1px solid {edge};
            border-radius: {r_md}px;
            padding: 4px;
            color: {t['text']};
            selection-background-color: {edge_soft};
            selection-color: {t['accent']};
            outline: none;
        }}
        QCheckBox {{
            color: {t['text']};
            font-size: {f_body}px;
            spacing: 9px;
            padding: 3px 0;
        }}
        QCheckBox::indicator {{
            width: 17px;
            height: 17px;
            border-radius: 3px;
            border: 1.5px solid {edge};
            background: {glass_2};
        }}
        QCheckBox::indicator:hover {{
            border: 1.5px solid {t['accent_hover']};
        }}
        QCheckBox::indicator:checked {{
            background: {t['accent']};
            border: 1.5px solid {t['accent']};
            image: url("data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 12 12'><polyline points='1.5,6 5,9.5 10.5,2.5' fill='none' stroke='white' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'/></svg>");
        }}
        QSpinBox::up-button, QSpinBox::down-button {{
            width: 16px;
            border: none;
            background: transparent;
        }}
        QSlider::groove:horizontal {{
            height: 5px;
            background: {t['slider_groove']};
            border-radius: 2.5px;
        }}
        QSlider::handle:horizontal {{
            width: 16px;
            height: 16px;
            margin: -5.5px 0;
            border-radius: 8px;
            background: {t['accent']};
            border: 2px solid {t['panel']};
        }}
        QSlider::handle:horizontal:hover {{
            background: {t['accent_hover']};
        }}
        QSlider::sub-page:horizontal {{
            background: {t['slider_fill']};
            border-radius: 2.5px;
        }}
        QPushButton {{
            background: {glass_2};
            color: {t['text']};
            border: 1px solid {edge};
            border-radius: {r_md}px;
            padding: 8px 20px;
            font-size: {f_hint}px;
            font-weight: 600;
        }}
        QPushButton:hover {{
            border: 1px solid {t['accent_hover']};
            color: {t['accent']};
            background: {glass_3};
        }}
        QPushButton:pressed {{
            background: {edge};
        }}
        QPushButton#primaryButton {{
            background: {t['accent']};
            color: #FFFFFF;
            border: none;
            padding: 8px 26px;
        }}
        QPushButton#primaryButton:hover {{
            background: {t['accent_hover']};
        }}
        QPushButton#primaryButton:pressed {{
            background: {t['accent_press']};
        }}
        QPushButton#compactButton {{
            padding: 5px 8px;
            border-radius: {r_sm}px;
        }}
        QScrollBar:vertical {{
            background: transparent;
            width: 8px;
            margin: 2px;
        }}
        QScrollBar::handle:vertical {{
            background: {edge};
            border-radius: 3px;
            min-height: 24px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: {t['accent_hover']};
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0px;
        }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
            background: transparent;
        }}
        QToolTip {{
            background: {glass_3};
            color: {t['text']};
            border: none;
            border-radius: 2px;
            padding: 2px 6px;
        }}
        QListWidget#pluginList {{
            background: {glass_2};
            border: 1px solid {edge};
            border-radius: {r_lg}px;
            padding: 4px;
            outline: none;
            font-size: {f_hint}px;
        }}
        QListWidget#pluginList::item {{
            color: {t['text']};
            border-radius: {r_sm}px;
            padding: 8px 9px;
            margin: 1px 0;
        }}
        QListWidget#pluginList::item:selected {{
            background: {t['accent']};
            color: #FFFFFF;
        }}
        QListWidget#pluginList::item:hover:!selected {{
            background: {edge_soft};
        }}
        QPlainTextEdit#pluginEditor {{
            background: {_CODE_EDITOR_COLORS['bg']};
            color: {_CODE_EDITOR_COLORS['text']};
            border: 1px solid {edge};
            border-radius: {r_lg}px;
            padding: 10px 12px;
            font-family: 'Cascadia Code', 'Consolas', 'Fira Code', monospace;
            font-size: 12px;
            selection-background-color: {_CODE_EDITOR_COLORS['selection']};
            selection-color: #FFFFFF;
        }}
        QLabel#pluginFileLabel {{
            color: {t['text']};
            font-size: 13px;
            font-weight: 600;
            background: transparent;
        }}
        QLabel#pluginEmptyState {{
            color: {t['subtext']};
            font-size: 13px;
            background: transparent;
        }}
        QLabel#pluginDot {{
            font-size: 13px;
            background: transparent;
        }}
        QPushButton#pluginDangerButton {{
            border: 1px solid {t['danger']};
            color: {t['danger']};
        }}
        QPushButton#pluginDangerButton:hover {{
            background: {t['danger']};
            color: #FFFFFF;
        }}
        QSplitter::handle {{
            background: transparent;
            border: none;
            border-right: 1px solid {edge};
            margin: 0 4px;
        }}
        QSplitter::handle:hover {{
            border-right: 1px solid {t['accent_hover']};
        }}
    """

    def _t(self) -> dict:
        """This dialog's theme dict, matching the companion's current color_theme
        or dark_mode setting - a single place every status-label/border color
        reads from."""
        return _theme(self.companion.settings)

    def __init__(self, companion: CompanionWindow, focus_gemini: bool = False):
        super().__init__(None, Qt.Window)
        # Explicitly non-modal: this dialog has no parent, so exec()'s
        # default modality would be Qt.ApplicationModal and block input to
        # every other window - including Alyssa herself, making her
        # un-right-clickable and undraggable while Settings is open. Shown
        # with .show() (see CompanionWindow.open_settings) rather than
        # .exec() so she stays fully interactive alongside it.
        self.setWindowModality(Qt.NonModal)
        # Destroy the C++ widget on close instead of just hiding it, so a
        # closed-and-reopened Settings window is always built fresh from
        # current settings/config.py rather than showing stale state.
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.companion = companion
        self.setWindowTitle(f"{config.ASSISTANT_NAME} - Settings")
        # Wide enough that every row (API key field + Show/Verify buttons,
        # etc.) fits without horizontal scrolling.
        # Cap the dialog's height to the actual screen it's opening on, and
        # give it a sane default - without this, adding rows to a tab could
        # push the natural minimum height past the available screen space
        # and trigger a Windows geometry warning. Each tab scrolls
        # internally instead (see _wrap_in_scroll_area).
        screen = self.screen() or QGuiApplication.primaryScreen()
        available = screen.availableGeometry().size()
        self.resize(
            min(max(640, round(available.width() * 0.72)), available.width() - 32),
            min(max(520, round(available.height() * 0.82)), available.height() - 32),
        )
        self.setMinimumSize(
            min(560, max(420, available.width() - 32)),
            min(420, max(360, available.height() - 32)),
        )
        self.setStyleSheet(self._build_style(companion.settings))
        self._responsive_forms = []
        self._compact_layout = None

        # Every field applies as you change it - no Save button. Each tab
        # has its own debounce timer so a burst of changes (a slider drag,
        # typing character by character) collapses into one apply/persist
        # instead of one per event.
        self._companion_apply_timer = QTimer(self)
        self._companion_apply_timer.setSingleShot(True)
        self._companion_apply_timer.setInterval(150)
        self._companion_apply_timer.timeout.connect(self._apply_companion_live)

        self._assistant_apply_timer = QTimer(self)
        self._assistant_apply_timer.setSingleShot(True)
        self._assistant_apply_timer.setInterval(400)
        self._assistant_apply_timer.timeout.connect(self._apply_assistant_live)

        self._key_verified_signal.connect(self._on_key_verification_result)
        self._custom_models_fetched_signal.connect(self._on_custom_models_fetch_result)
        self._spotify_verified_signal.connect(self._on_spotify_verify_result)

        self.tabs = _SidebarTabs(self)
        self.tabs.addTab(self._wrap_in_scroll_area(self._build_assistant_tab()), "Assistant")
        self.tabs.addTab(self._wrap_in_scroll_area(self._build_engine_tab()), "Engine")
        self.tabs.addTab(self._wrap_in_scroll_area(self._build_companion_tab()), "Companion")
        # Not wrapped in _wrap_in_scroll_area like the tabs above - this
        # tab's own splitter (plugin list + code editor) needs to claim
        # the full available height itself rather than being able to
        # shrink to content size inside a QScrollArea.
        self.tabs.addTab(self._build_plugins_tab(), "Plugins")

        # Root-cause fix for clipped combo text (provider/model dropdowns
        # etc.): QFormLayout's ExpandingFieldsGrow stretches QLineEdit
        # fields to the column width but leaves QComboBox at its
        # content-derived sizeHint, since its default horizontal size
        # policy doesn't include the Grow flag. Rather than patching every
        # `QComboBox()` call site across the tabs, normalize them all here
        # in one pass so every dropdown matches the text fields beside it.
        for combo in self.findChildren(QComboBox):
            combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        close_btn = QPushButton("Close")
        close_btn.setObjectName("primaryButton")
        close_btn.clicked.connect(self._on_close)
        # Soft micro-interaction: opacity dips on press, eases back on
        # release/leave. Kept to the one button the user clicks most on
        # their way out, rather than wired onto every button in the
        # dialog - see _HoverPress for wiring it onto others.
        close_btn._press_fx = _HoverPress(close_btn)
        # Covers every way the dialog can close (Close button, window X,
        # Esc) - see _flush_pending_applies.
        self.finished.connect(self._flush_pending_applies)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(close_btn)
        btn_row.setContentsMargins(0, 6, 0, 0)

        content = _QW()
        content.setObjectName("dialogContent")
        content.setAttribute(Qt.WA_StyledBackground, True)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(12, 12, 12, 12)
        content_layout.setSpacing(10)
        content_layout.addWidget(self.tabs)
        content_layout.addLayout(btn_row)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(content)

        self._update_responsive_layout()

        if focus_gemini:
            self.tabs.setCurrentIndex(0)
            self.provider_combo.setCurrentText("gemini")
            self.gemini_setup_hint.setVisible(True)
            self.gemini_key_edit.setStyleSheet(f"border: 2px solid {self._t()['warning']};")
            self.gemini_key_edit.setFocus()

        # Track what the form itself changed. Comparing against the live
        # companion is wrong when the user drags/resizes her while Settings
        # remains open: an unrelated toggle would otherwise re-submit the
        # stale size slider and snap her back.
        self._last_companion_form_values = self._gather_companion_settings()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_responsive_layout()

    def _update_responsive_layout(self):
        """Reflow the few rows that cannot shrink cleanly on their own."""
        if not hasattr(self, "tabs"):
            return
        self.tabs.fit_navigation(self.width())
        page_width = max(0, self.width() - self.tabs.navigation.width() - 48)
        compact = page_width < 560

        for form in self._responsive_forms:
            form.setRowWrapPolicy(
                QFormLayout.WrapAllRows if compact else QFormLayout.WrapLongRows
            )
            form.setLabelAlignment(
                Qt.AlignLeft | Qt.AlignVCenter
                if compact else Qt.AlignRight | Qt.AlignVCenter
            )

        if compact != self._compact_layout:
            if hasattr(self, "_companion_grid"):
                if compact:
                    self._companion_grid.addWidget(
                        self._preview_panel, 0, 0, 1, 2, Qt.AlignHCenter | Qt.AlignTop
                    )
                    self._companion_grid.addWidget(self._companion_form_panel, 1, 0, 1, 2)
                else:
                    self._companion_grid.addWidget(self._preview_panel, 0, 0, Qt.AlignTop)
                    self._companion_grid.addWidget(self._companion_form_panel, 0, 1)
                self._companion_grid.setColumnStretch(0, 0 if not compact else 1)
                self._companion_grid.setColumnStretch(1, 1)

            if hasattr(self, "_plugin_splitter"):
                self._plugin_splitter.setOrientation(
                    Qt.Vertical if compact else Qt.Horizontal
                )
                self._plugin_splitter.setSizes(
                    [190, 360] if compact else [190, 500]
                )

            if hasattr(self, "_plugin_header_grid"):
                grid = self._plugin_header_grid
                if compact:
                    grid.addWidget(self.plugin_file_label, 0, 0, 1, 3)
                    grid.addWidget(self.plugin_enable_btn, 1, 0)
                    grid.addWidget(self.plugin_save_btn, 1, 1)
                    grid.addWidget(self.plugin_delete_btn, 1, 2)
                else:
                    grid.addWidget(self.plugin_file_label, 0, 0)
                    grid.addWidget(self.plugin_enable_btn, 0, 1)
                    grid.addWidget(self.plugin_save_btn, 0, 2)
                    grid.addWidget(self.plugin_delete_btn, 0, 3)
                grid.setColumnStretch(0, 1)
            self._compact_layout = compact

    def finalize_initial_layout(self):
        """Resolve every nested layout after the native window is visible."""
        for widget in [self] + self.findChildren(QWidget):
            layout = widget.layout()
            if layout is not None:
                layout.invalidate()
                layout.activate()
            widget.updateGeometry()
        self.update()

    def _section_header(self, text: str, icon: str = "", first: bool = False) -> QLabel:
        """A small bold, accent-colored label with a hairline divider
        above it, used to break a long tab's fields into named groups
        (e.g. "Voice & Behavior", "Music") without changing how any of
        the actual fields/rows work underneath - purely a visual label
        dropped into the existing QFormLayout via addRow("", header).
        Qt doesn't support text-transform:uppercase so we uppercase in Python."""
        label = QLabel(f"{icon}  {text.upper()}" if icon else text.upper())
        label.setObjectName("sectionHeaderFirst" if first else "sectionHeader")
        return label

    # -- Assistant tab helpers ----------------------------------------------
    def _wrap_in_scroll_area(self, content: _QW) -> QScrollArea:
        """Wraps a tab's content in a scroll area so a long tab (Assistant,
        Engine) doesn't force the whole Settings dialog taller than the
        screen."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setMinimumWidth(0)
        scroll.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content.setMinimumWidth(0)
        content.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        scroll.setWidget(content)
        return scroll

    def _add_row(self, form, label, field):
        """Adds a row to `form` and returns (label_widget, field_widget) so
        the row can be shown/hidden as a unit later (e.g. only the
        currently-selected LLM provider's fields)."""
        label_widget = QLabel(label) if isinstance(label, str) else label
        form.addRow(label_widget, field)
        return (label_widget, field)

    def _stack_under(self, primary, *extras) -> _QW:
        """Stacks `extras` (status text, "don't have one?" links, etc.)
        directly beneath `primary` in a single column, so they line up
        under their field instead of spanning the full row width the way
        addRow("", ...) would."""
        wrapper = _QW()
        col = QVBoxLayout(wrapper)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)
        if isinstance(primary, QHBoxLayout):
            col.addLayout(primary)
        else:
            col.addWidget(primary)
        for extra in extras:
            col.addWidget(extra)
        return wrapper

    def _help_label(self, text: str) -> QLabel:
        """Small gray status/help text used under a field (verify results,
        "don't have one?" links, etc.). Starts hidden and only takes up
        space once it actually has something to say - otherwise an empty
        status label left a blank line between the field and the link
        below it."""
        label = _StatusLabel(text)
        label.setStyleSheet("font-size: 11px;")
        label.setWordWrap(True)
        label.setVisible(bool(text))
        return label

    def _link_label(self, url: str, prefix: str, link_text: str) -> QLabel:
        accent = _theme(self.companion.settings)["accent"]
        label = QLabel(
            f'{prefix} <a href="{url}" style="color:{accent};">{link_text}</a>'
        )
        label.setOpenExternalLinks(True)
        label.setStyleSheet("font-size: 11px;")
        return label

    def _set_rows_visible(self, form, rows, visible):
        """Hides/shows each (label, field) row as a unit via
        QFormLayout.setRowVisible, which - unlike widget.setVisible()
        directly - also collapses the row's spacing when hidden. Without
        this, every hidden provider's rows still ate their share of
        form.setSpacing(), leaving a big dead gap wherever 2-3 unused
        providers were hiding."""
        for label_widget, field_widget in rows:
            form.setRowVisible(field_widget, visible)

    def _build_api_key_row(self, form, label_text, initial_value, toggle_slot, verify_slot, placeholder, extras=()):
        """Builds the common "[key field] [Show] [Verify]" row shared by
        every cloud provider's API key setting, with any status/help-link
        labels stacked directly under the field. Returns (key_edit,
        row_label, rows) where `rows` is a single-item list of (label,
        field) suitable for _set_rows_visible."""
        key_edit = QLineEdit(initial_value)
        key_edit.setEchoMode(QLineEdit.Password)
        key_edit.setPlaceholderText(placeholder)
        key_edit.textChanged.connect(self._queue_assistant_apply)

        toggle_btn = QPushButton("Show")
        toggle_btn.setObjectName("compactButton")
        toggle_btn.setFixedWidth(58)
        toggle_btn.setCheckable(True)
        toggle_btn.toggled.connect(toggle_slot)

        verify_btn = QPushButton("Verify")
        verify_btn.setObjectName("compactButton")
        verify_btn.setFixedWidth(58)
        verify_btn.clicked.connect(verify_slot)

        # Stashed on key_edit so the toggle/verify slots (which only get
        # the changed value, e.g. the checked bool) can reach these back.
        key_edit._toggle_btn = toggle_btn
        key_edit._verify_btn = verify_btn

        row_layout = QHBoxLayout()
        row_layout.addWidget(key_edit)
        row_layout.addWidget(toggle_btn)
        row_layout.addWidget(verify_btn)

        field = self._stack_under(row_layout, *extras) if extras else row_layout
        row_label = QLabel(label_text)
        row = self._add_row(form, row_label, field)
        return key_edit, row_label, [row]

    def _build_model_combo(self, current_value: str) -> QComboBox:
        """An editable dropdown for a provider's model name - starts with
        just the current/default value typed in, and gets repopulated with
        the real list once Verify/Fetch models succeeds. Editable so a
        model that isn't in the fetched list (brand new, preview-only,
        etc.) can still be typed in by hand."""
        combo = QComboBox()
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.NoInsert)
        if current_value:
            combo.addItem(current_value)
        combo.setCurrentText(current_value)
        combo.currentTextChanged.connect(self._queue_assistant_apply)
        return combo

    def _populate_model_combo(self, combo: QComboBox, models: list):
        """Refills *combo* with a freshly-fetched model list, keeping
        whatever the user currently has typed/selected as the current text
        even if it's not in the new list (e.g. they'd already picked a
        preview model the list endpoint doesn't surface)."""
        current = combo.currentText().strip()
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(models)
        if current and combo.findText(current) < 0:
            combo.insertItem(0, current)
        combo.setCurrentText(current or (models[0] if models else ""))
        combo.blockSignals(False)

    def _update_provider_rows_visibility(self, *args):
        provider = self.provider_combo.currentText()
        form = self._assistant_form
        self._set_rows_visible(form, [(self._ollama_model_label, self.ollama_model_edit)], provider == "ollama")
        self._set_rows_visible(form, self._gemini_key_rows, provider == "gemini")
        self._set_rows_visible(form, self._openai_key_rows, provider == "openai")
        self._set_rows_visible(form, self._anthropic_key_rows, provider == "anthropic")
        self._set_rows_visible(form, self._custom_rows, provider == "custom_openai")

    def _update_tts_provider_rows_visibility(self, *args):
        provider = self.tts_provider_combo.currentText()
        form = self._assistant_form
        self._set_rows_visible(form, self._edge_voice_rows, provider == "edge")
        self._set_rows_visible(form, self._elevenlabs_rows, provider == "elevenlabs")

    # -- Assistant tab -----------------------------------------------------
    def _build_assistant_tab(self) -> _QW:
        w = _QW()
        w.setObjectName("tabPage")
        form = QFormLayout(w)
        form.setContentsMargins(16, 16, 16, 16)
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self._responsive_forms.append(form)
        self._assistant_form = form  # so _update_provider_rows_visibility can setRowVisible()

        form.addRow("", self._section_header("Identity & Brain", "🧠", first=True))
        self.name_edit = QLineEdit(config.ASSISTANT_NAME)
        self.name_edit.textChanged.connect(self._queue_assistant_apply)
        form.addRow("Name:", self.name_edit)

        self.provider_combo = QComboBox()
        self.provider_combo.addItems(["ollama", "gemini", "openai", "anthropic", "custom_openai"])
        self.provider_combo.setCurrentText(config.LLM_PROVIDER)
        self.provider_combo.currentTextChanged.connect(self._queue_assistant_apply)
        self.provider_combo.currentTextChanged.connect(self._update_provider_rows_visibility)
        form.addRow("LLM provider:", self.provider_combo)

        # -- Ollama (local) --
        self.ollama_model_edit = QLineEdit(config.OLLAMA_MODEL)
        self.ollama_model_edit.textChanged.connect(self._queue_assistant_apply)
        self._ollama_model_label = QLabel("Ollama model:")
        form.addRow(self._ollama_model_label, self.ollama_model_edit)

        # -- Gemini --
        # Pre-filled from config.py (masked) so the field reflects what's
        # already set. Keep the original value to only rewrite config.py's
        # *_API_KEY lines if they actually changed - same pattern for
        # OpenAI/Anthropic/custom below.
        self._original_gemini_key = config.GEMINI_API_KEY
        self.gemini_key_status_label = self._help_label("")
        get_key_link = self._link_label(
            "https://aistudio.google.com/apikey", "Don't have one?", "Get a free Gemini API key"
        )
        self.gemini_setup_hint = QLabel("⬆ Paste your key above; it applies immediately.")
        self.gemini_setup_hint.setStyleSheet(f"color: {self._t()['warning']}; font-weight: 600; font-size: 11px;")
        self.gemini_setup_hint.setVisible(False)
        self.gemini_key_edit, self.gemini_key_row_label, self._gemini_key_rows = self._build_api_key_row(
            form, "Gemini API key:", config.GEMINI_API_KEY,
            self._toggle_gemini_key_visibility, self._start_gemini_key_verify,
            "Paste your Gemini API key here",
            extras=(self.gemini_key_status_label, get_key_link, self.gemini_setup_hint),
        )
        self.gemini_key_edit.textChanged.connect(self._reset_gemini_key_verify_status)
        self.gemini_model_edit = self._build_model_combo(config.GEMINI_MODEL)
        self._gemini_key_rows.append(self._add_row(form, "Gemini model:", self.gemini_model_edit))

        # -- OpenAI --
        self._original_openai_key = getattr(config, "OPENAI_API_KEY", "")
        self.openai_key_status_label = self._help_label("")
        openai_key_link = self._link_label(
            "https://platform.openai.com/api-keys", "Don't have one?", "Get an OpenAI API key"
        )
        self.openai_key_edit, self.openai_key_row_label, self._openai_key_rows = self._build_api_key_row(
            form, "OpenAI API key:", self._original_openai_key,
            self._toggle_openai_key_visibility, self._start_openai_key_verify,
            "Paste your OpenAI API key here",
            extras=(self.openai_key_status_label, openai_key_link),
        )
        self.openai_key_edit.textChanged.connect(self._reset_openai_key_verify_status)
        self.openai_model_edit = self._build_model_combo(getattr(config, "OPENAI_MODEL", "gpt-5-mini"))
        self._openai_key_rows.append(self._add_row(form, "OpenAI model:", self.openai_model_edit))

        # -- Anthropic (Claude) --
        self._original_anthropic_key = getattr(config, "ANTHROPIC_API_KEY", "")
        self.anthropic_key_status_label = self._help_label("")
        anthropic_key_link = self._link_label(
            "https://console.anthropic.com", "Don't have one?", "Get an Anthropic API key"
        )
        self.anthropic_key_edit, self.anthropic_key_row_label, self._anthropic_key_rows = self._build_api_key_row(
            form, "Anthropic API key:", self._original_anthropic_key,
            self._toggle_anthropic_key_visibility, self._start_anthropic_key_verify,
            "Paste your Anthropic API key here",
            extras=(self.anthropic_key_status_label, anthropic_key_link),
        )
        self.anthropic_key_edit.textChanged.connect(self._reset_anthropic_key_verify_status)
        self.anthropic_model_edit = self._build_model_combo(getattr(config, "ANTHROPIC_MODEL", "claude-sonnet-5"))
        self._anthropic_key_rows.append(self._add_row(form, "Claude model:", self.anthropic_model_edit))

        # -- Custom OpenAI-compatible provider (Groq, OpenRouter, etc.) --
        self._custom_rows = []
        self.custom_base_url_edit = QLineEdit(getattr(config, "CUSTOM_BASE_URL", ""))
        self.custom_base_url_edit.textChanged.connect(self._queue_assistant_apply)
        self._custom_rows.append(self._add_row(form, "Custom base URL:", self.custom_base_url_edit))
        self._original_custom_key = getattr(config, "CUSTOM_API_KEY", "")
        self.custom_key_edit = QLineEdit(self._original_custom_key)
        self.custom_key_edit.setEchoMode(QLineEdit.Password)
        self.custom_key_edit.setPlaceholderText("API key (leave blank if the server doesn't need one)")
        self.custom_key_edit.textChanged.connect(self._queue_assistant_apply)
        self._custom_key_toggle = QPushButton("Show")
        self._custom_key_toggle.setObjectName("compactButton")
        self._custom_key_toggle.setFixedWidth(58)
        self._custom_key_toggle.setCheckable(True)
        self._custom_key_toggle.toggled.connect(
            lambda show: self.custom_key_edit.setEchoMode(QLineEdit.Normal if show else QLineEdit.Password)
        )
        custom_key_row = QHBoxLayout()
        custom_key_row.addWidget(self.custom_key_edit)
        custom_key_row.addWidget(self._custom_key_toggle)
        self._custom_rows.append(self._add_row(form, "Custom API key:", custom_key_row))

        self.custom_model_edit = self._build_model_combo(getattr(config, "CUSTOM_MODEL", ""))
        self.custom_models_status_label = self._help_label("")
        self._custom_fetch_models_btn = QPushButton("Fetch models")
        self._custom_fetch_models_btn.setObjectName("compactButton")
        self._custom_fetch_models_btn.clicked.connect(self._start_custom_models_fetch)
        custom_model_row = QHBoxLayout()
        custom_model_row.addWidget(self.custom_model_edit)
        custom_model_row.addWidget(self._custom_fetch_models_btn)
        custom_model_field = self._stack_under(custom_model_row, self.custom_models_status_label)
        self._custom_rows.append(self._add_row(form, "Custom model:", custom_model_field))

        custom_note = self._help_label(
            "Works with OpenAI-compatible providers. Fetch or enter a model name."
        )
        custom_note.setStyleSheet(f"color: {self._t()['subtext']}; font-size: 11px;")
        self._custom_rows.append(self._add_row(form, "", custom_note))

        note = self._help_label(
            "Keys apply immediately and are stored as plain text in config.py."
        )
        note.setStyleSheet(f"color: {self._t()['subtext']}; font-size: 11px;")
        form.addRow("", note)

        # -- Music (Spotify / YouTube Music) -- optional: without these,
        # play_music can only open a search page (see actions.py).
        form.addRow("", self._section_header("Music (Spotify / YouTube Music)", "🎵"))
        music_intro = self._help_label(
            "Optional: plays results directly instead of opening search."
        )
        music_intro.setStyleSheet(f"color: {self._t()['subtext']}; font-size: 11px;")
        form.addRow("", music_intro)

        self._original_spotify_client_id = getattr(config, "SPOTIFY_CLIENT_ID", "")
        self._original_spotify_client_secret = getattr(config, "SPOTIFY_CLIENT_SECRET", "")

        self.spotify_client_id_edit = QLineEdit(self._original_spotify_client_id)
        self.spotify_client_id_edit.setPlaceholderText("Paste your Spotify Client ID here")
        self.spotify_client_id_edit.textChanged.connect(self._queue_assistant_apply)
        self.spotify_client_id_edit.textChanged.connect(self._reset_spotify_verify_status)
        # Right-padded to match the width of the Secret/YouTube fields
        # below (which have Show/Verify buttons eating into their width),
        # so this field doesn't run wider and look ragged.
        id_row = QHBoxLayout()
        id_row.addWidget(self.spotify_client_id_edit)
        id_spacer = QLabel("")
        id_spacer.setFixedWidth(58 * 2 + 6)
        id_row.addWidget(id_spacer)
        form.addRow("Spotify Client ID:", id_row)

        self.spotify_status_label = self._help_label("")
        spotify_link = self._link_label(
            "https://developer.spotify.com/dashboard",
            "Don't have these?", "Create a free Spotify app",
        )
        self.spotify_client_secret_edit, _spotify_secret_label, _spotify_secret_rows = self._build_api_key_row(
            form, "Spotify Client Secret:", self._original_spotify_client_secret,
            self._toggle_spotify_secret_visibility, self._start_spotify_verify,
            "Paste your Spotify Client Secret here",
            extras=(self.spotify_status_label, spotify_link),
        )
        self.spotify_client_secret_edit.textChanged.connect(self._reset_spotify_verify_status)

        self._original_youtube_key = getattr(config, "YOUTUBE_API_KEY", "")
        self.youtube_status_label = self._help_label("")
        youtube_link = self._link_label(
            "https://console.cloud.google.com/apis/library/youtube.googleapis.com",
            "Don't have one?", "Enable the YouTube Data API v3",
        )
        self.youtube_key_edit, _youtube_key_label, _youtube_key_rows = self._build_api_key_row(
            form, "YouTube API key:", self._original_youtube_key,
            self._toggle_youtube_key_visibility, self._start_youtube_key_verify,
            "Paste your YouTube Data API key here",
            extras=(self.youtube_status_label, youtube_link),
        )
        self.youtube_key_edit.textChanged.connect(self._reset_youtube_key_verify_status)

        form.addRow("", self._section_header("Voice & Behavior", "🔊"))

        self.tts_provider_combo = QComboBox()
        self.tts_provider_combo.addItems(["edge", "elevenlabs"])
        self.tts_provider_combo.setCurrentText(getattr(config, "TTS_PROVIDER", "edge"))
        self.tts_provider_combo.currentTextChanged.connect(self._queue_assistant_apply)
        self.tts_provider_combo.currentTextChanged.connect(self._update_tts_provider_rows_visibility)
        form.addRow("TTS provider:", self.tts_provider_combo)

        # -- Edge TTS (free, default) --
        self._edge_voice_rows = []
        self.voice_combo = QComboBox()
        self.voice_combo.setEditable(True)
        # ponytail: dropped 63-entry COMMON_VOICES list; combo is editable
        # and "Browse all voices" fetches the full catalog from Edge TTS.
        self.voice_combo.addItem(config.EDGE_TTS_VOICE)
        self.voice_combo.setCurrentText(config.EDGE_TTS_VOICE)
        self.voice_combo.currentTextChanged.connect(self._queue_assistant_apply)
        self.browse_edge_voices_btn = QPushButton("Browse all voices…")
        self.browse_edge_voices_btn.setObjectName("compactButton")
        self.browse_edge_voices_btn.clicked.connect(self._open_edge_voice_browser)
        edge_voice_row = QHBoxLayout()
        edge_voice_row.addWidget(self.voice_combo)
        edge_voice_row.addWidget(self.browse_edge_voices_btn)
        self._edge_voice_rows.append(self._add_row(form, "Voice:", edge_voice_row))

        edge_note = self._help_label(
            "Browse Microsoft's full voice catalog."
        )
        edge_note.setStyleSheet(f"color: {self._t()['subtext']}; font-size: 11px;")
        self._edge_voice_rows.append(self._add_row(form, "", edge_note))

        # -- ElevenLabs (premium/cloned voices) --
        self._elevenlabs_rows = []
        self._original_elevenlabs_key = getattr(config, "ELEVENLABS_API_KEY", "")
        self.elevenlabs_key_status_label = self._help_label("")
        elevenlabs_key_link = self._link_label(
            "https://elevenlabs.io/app/settings/api-keys", "Don't have one?", "Get an ElevenLabs API key"
        )
        self.elevenlabs_key_edit, self.elevenlabs_key_row_label, self._elevenlabs_key_rows = self._build_api_key_row(
            form, "ElevenLabs API key:", self._original_elevenlabs_key,
            self._toggle_elevenlabs_key_visibility, self._start_elevenlabs_key_verify,
            "Paste your ElevenLabs API key here",
            extras=(self.elevenlabs_key_status_label, elevenlabs_key_link),
        )
        self.elevenlabs_key_edit.textChanged.connect(self._reset_elevenlabs_key_verify_status)
        self._elevenlabs_rows.extend(self._elevenlabs_key_rows)

        self.elevenlabs_voice_combo = QComboBox()
        self.elevenlabs_voice_combo.setEditable(True)
        current_voice_id = getattr(config, "ELEVENLABS_VOICE_ID", "")
        if current_voice_id:
            self.elevenlabs_voice_combo.addItem(current_voice_id)
        self.elevenlabs_voice_combo.setCurrentText(current_voice_id)
        self.elevenlabs_voice_combo.currentTextChanged.connect(self._queue_assistant_apply)
        self._elevenlabs_rows.append(self._add_row(form, "ElevenLabs voice:", self.elevenlabs_voice_combo))

        elevenlabs_note = self._help_label(
            "Verify the key to load voices, or enter a voice ID."
        )
        elevenlabs_note.setStyleSheet(f"color: {self._t()['subtext']}; font-size: 11px;")
        self._elevenlabs_rows.append(self._add_row(form, "", elevenlabs_note))

        self.elevenlabs_model_combo = QComboBox()
        self.elevenlabs_model_combo.setEditable(True)
        self.elevenlabs_model_combo.setInsertPolicy(QComboBox.NoInsert)
        _elevenlabs_models = ["eleven_multilingual_v2", "eleven_flash_v2_5", "eleven_turbo_v2_5", "eleven_v3"]
        current_model = getattr(config, "ELEVENLABS_MODEL", "eleven_multilingual_v2")
        if current_model and current_model not in _elevenlabs_models:
            _elevenlabs_models.insert(0, current_model)
        self.elevenlabs_model_combo.addItems(_elevenlabs_models)
        self.elevenlabs_model_combo.setCurrentText(current_model)
        self.elevenlabs_model_combo.currentTextChanged.connect(self._queue_assistant_apply)
        self._elevenlabs_rows.append(self._add_row(form, "ElevenLabs model:", self.elevenlabs_model_combo))

        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(-50, 50)
        self.volume_slider.setValue(
            _volume_str_to_percent(getattr(config, "EDGE_TTS_VOLUME", "+0%"))
        )
        self.volume_value_label = QLabel()
        self.volume_value_label.setFixedWidth(40)
        self.volume_slider.valueChanged.connect(
            lambda v: self.volume_value_label.setText(_percent_to_volume_str(v))
        )
        self.volume_value_label.setText(_percent_to_volume_str(self.volume_slider.value()))
        self.volume_slider.valueChanged.connect(self._apply_edge_volume_live)
        volume_row = QHBoxLayout()
        volume_row.addWidget(self.volume_slider)
        volume_row.addWidget(self.volume_value_label)
        # Edge TTS only - ElevenLabs' API has no equivalent %-volume knob,
        # so this row is hidden rather than shown-but-inert when that
        # provider is selected (see _update_tts_provider_rows_visibility).
        self._edge_voice_rows.append(self._add_row(form, "Volume:", volume_row))

        self._update_tts_provider_rows_visibility()

        self.speak_check = QCheckBox("Speak replies out loud")
        self.speak_check.setChecked(bool(config.SPEAK_RESPONSES))
        self.speak_check.toggled.connect(self._queue_assistant_apply)
        form.addRow("", self.speak_check)

        # Caveman mode - mirrors plugins/caveman_mode.py's set_caveman_mode()
        # tool (which the user can also trigger by voice); both write the
        # same config.CAVEMAN_MODE, so whichever was used last wins.
        self.caveman_combo = QComboBox()
        self.caveman_combo.addItem("Off", None)
        self.caveman_combo.addItem("Lite", "lite")
        self.caveman_combo.addItem("Full", "full")
        self.caveman_combo.addItem("Ultra", "ultra")
        current_caveman = getattr(config, "CAVEMAN_MODE", None)
        idx = self.caveman_combo.findData(current_caveman)
        self.caveman_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.caveman_combo.currentIndexChanged.connect(self._queue_assistant_apply)
        form.addRow("Caveman mode:", self.caveman_combo)

        self.confirm_check = QCheckBox("Ask for confirmation before every action")
        self.confirm_check.setChecked(bool(config.CONFIRM_BEFORE_ACTIONS))
        self.confirm_check.toggled.connect(self._queue_assistant_apply)
        form.addRow("", self.confirm_check)

        self.show_console_check = QCheckBox("Show command prompt")
        self.show_console_check.setChecked(not bool(getattr(config, "HIDE_CONSOLE_WINDOW", True)))
        self.show_console_check.toggled.connect(self._on_show_console_toggled)
        form.addRow("", self.show_console_check)

        # -- Memory & Location --
        form.addRow("", self._section_header("Memory & Location", "🧾"))

        memory_intro = self._help_label(
            "Short-term memory expires; saved facts persist in memory.json."
        )
        memory_intro.setStyleSheet(f"color: {self._t()['subtext']}; font-size: 11px;")
        form.addRow("", memory_intro)

        self.conversation_turns_spin = QSpinBox()
        self.conversation_turns_spin.setRange(1, 50)
        self.conversation_turns_spin.setValue(int(getattr(config, "CONVERSATION_MEMORY_TURNS", 10)))
        self.conversation_turns_spin.setToolTip(
            "Conversation turns kept in short-term memory."
        )
        self.conversation_turns_spin.valueChanged.connect(self._queue_assistant_apply)
        form.addRow("Short-term memory (turns):", self.conversation_turns_spin)

        self.conversation_timeout_spin = QSpinBox()
        self.conversation_timeout_spin.setRange(0, 3600)
        self.conversation_timeout_spin.setSuffix(" sec")
        self.conversation_timeout_spin.setValue(int(getattr(config, "CONVERSATION_TIMEOUT_SECONDS", 300)))
        self.conversation_timeout_spin.setToolTip(
            "Idle seconds before memory clears. 0 disables auto-clear."
        )
        self.conversation_timeout_spin.valueChanged.connect(self._queue_assistant_apply)
        form.addRow("Auto-clear after idle:", self.conversation_timeout_spin)

        self.max_saved_memories_spin = QSpinBox()
        self.max_saved_memories_spin.setRange(1, 1000)
        self.max_saved_memories_spin.setValue(int(getattr(config, "MAX_SAVED_MEMORIES", 75)))
        self.max_saved_memories_spin.setToolTip(
            "Maximum saved facts; oldest are removed first."
        )
        self.max_saved_memories_spin.valueChanged.connect(self._queue_assistant_apply)
        form.addRow("Saved memories (max facts):", self.max_saved_memories_spin)

        self.max_memory_chars_spin = QSpinBox()
        self.max_memory_chars_spin.setRange(20, 2000)
        self.max_memory_chars_spin.setSuffix(" chars")
        self.max_memory_chars_spin.setValue(int(getattr(config, "MAX_MEMORY_FACT_CHARACTERS", 400)))
        self.max_memory_chars_spin.setToolTip("Maximum characters per saved fact.")
        self.max_memory_chars_spin.valueChanged.connect(self._queue_assistant_apply)
        form.addRow("Max length per fact:", self.max_memory_chars_spin)

        self.max_memories_in_prompt_spin = QSpinBox()
        self.max_memories_in_prompt_spin.setRange(0, 200)
        self.max_memories_in_prompt_spin.setValue(int(getattr(config, "MAX_MEMORIES_IN_PROMPT", 20)))
        self.max_memories_in_prompt_spin.setToolTip(
            "Relevant saved facts included per request."
        )
        self.max_memories_in_prompt_spin.valueChanged.connect(self._queue_assistant_apply)
        form.addRow("Facts recalled per request:", self.max_memories_in_prompt_spin)

        location_note = self._help_label(
            "Used for weather and location requests; no key needed."
        )
        location_note.setStyleSheet(f"color: {self._t()['subtext']}; font-size: 11px;")
        form.addRow("", location_note)

        self.auto_detect_location_check = QCheckBox("Auto-detect my location from my IP address")
        self.auto_detect_location_check.setChecked(bool(getattr(config, "AUTO_DETECT_LOCATION", True)))
        self.auto_detect_location_check.setToolTip(
            "Approximate city detection; VPNs may affect accuracy."
        )
        self.auto_detect_location_check.toggled.connect(self._queue_assistant_apply)
        form.addRow("", self.auto_detect_location_check)

        self.weather_default_location_edit = QLineEdit(getattr(config, "WEATHER_DEFAULT_LOCATION", ""))
        self.weather_default_location_edit.setPlaceholderText("e.g. Chicago, IL - leave blank to auto-detect")
        self.weather_default_location_edit.textChanged.connect(self._queue_assistant_apply)
        form.addRow("Default location:", self.weather_default_location_edit)

        self.weather_units_combo = QComboBox()
        self.weather_units_combo.addItem("Imperial (°F, mph)", "imperial")
        self.weather_units_combo.addItem("Metric (°C, km/h)", "metric")
        current_units = getattr(config, "WEATHER_UNITS", "imperial")
        idx = self.weather_units_combo.findData(current_units)
        self.weather_units_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.weather_units_combo.currentIndexChanged.connect(self._queue_assistant_apply)
        form.addRow("Weather units:", self.weather_units_combo)

        self._update_provider_rows_visibility()
        return w

    # -- Engine tab (Whisper speech-to-text device/model) ------------------
    def _build_engine_tab(self) -> _QW:
        w = _QW()
        w.setObjectName("tabPage")
        form = QFormLayout(w)
        form.setContentsMargins(16, 16, 16, 16)
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self._responsive_forms.append(form)

        form.addRow("", self._section_header("Speech Recognition", "🎙️", first=True))
        intro = QLabel(
            "Local Whisper converts speech to text before LLM processing."
        )
        intro.setStyleSheet(f"color: {self._t()['subtext']}; font-size: 11px;")
        form.addRow("", intro)

        self.whisper_model_combo = QComboBox()
        self.whisper_model_combo.setEditable(True)
        whisper_models = ["tiny.en", "base.en", "small.en", "medium.en", "large-v3", "large-v3-turbo"]
        self.whisper_model_combo.addItems(whisper_models)
        current_model = getattr(config, "WHISPER_MODEL_SIZE", "base.en")
        if current_model not in whisper_models:
            self.whisper_model_combo.addItem(current_model)
        self.whisper_model_combo.setCurrentText(current_model)
        self.whisper_model_combo.currentTextChanged.connect(self._queue_assistant_apply)
        form.addRow("Whisper model:", self.whisper_model_combo)
        model_hint = QLabel(
            "Larger models are more accurate but slower; GPUs help."
        )
        model_hint.setStyleSheet(f"color: {self._t()['subtext']}; font-size: 11px;")
        form.addRow("", model_hint)

        self.whisper_device_combo = QComboBox()
        self._whisper_device_display_to_value = {
            "Auto-detect (use GPU if found, else CPU)": "auto",
            "Force NVIDIA GPU (CUDA)": "cuda",
            "Force CPU": "cpu",
        }
        self._whisper_device_value_to_display = {v: k for k, v in self._whisper_device_display_to_value.items()}
        self.whisper_device_combo.addItems(list(self._whisper_device_display_to_value.keys()))
        current_device = str(getattr(config, "WHISPER_DEVICE", "auto") or "auto").lower()
        self.whisper_device_combo.setCurrentText(
            self._whisper_device_value_to_display.get(current_device, "Auto-detect (use GPU if found, else CPU)")
        )
        self.whisper_device_combo.currentTextChanged.connect(self._queue_assistant_apply)
        form.addRow("Device:", self.whisper_device_combo)
        device_hint = QLabel(
            "GPU mode requires NVIDIA CUDA; otherwise use Auto or CPU."
        )
        device_hint.setWordWrap(True)
        device_hint.setStyleSheet(f"color: {self._t()['subtext']}; font-size: 11px;")
        form.addRow("", device_hint)

        self.whisper_compute_combo = QComboBox()
        compute_types = ["auto", "float16", "float32", "int8", "int8_float16"]
        self.whisper_compute_combo.addItems(compute_types)
        current_compute = str(getattr(config, "WHISPER_COMPUTE_TYPE", "auto") or "auto").lower()
        if current_compute not in compute_types:
            self.whisper_compute_combo.addItem(current_compute)
        self.whisper_compute_combo.setCurrentText(current_compute)
        self.whisper_compute_combo.currentTextChanged.connect(self._queue_assistant_apply)
        form.addRow("Compute type:", self.whisper_compute_combo)
        compute_hint = QLabel(
            "Auto uses float16 on GPU and int8 on CPU."
        )
        compute_hint.setStyleSheet(f"color: {self._t()['subtext']}; font-size: 11px;")
        form.addRow("", compute_hint)

        restart_note = QLabel(
            "Changes reload automatically; no restart needed."
        )
        restart_note.setWordWrap(True)
        restart_note.setStyleSheet(f"color: {self._t()['subtext']}; font-size: 11px;")
        form.addRow("", restart_note)

        # -- Live status: what's actually running right now -----------------
        # Picking "Force NVIDIA GPU (CUDA)" patches config.py and kicks off
        # a background reload (see _apply_assistant_live()), but a forced
        # GPU load that fails to find CUDA quietly falls back to CPU. This
        # label shows transcribe.py's live status, which distinguishes
        # "configured for CPU" from "GPU was tried and failed".
        self.engine_status_label = QLabel("Checking current status…")
        self.engine_status_label.setWordWrap(True)
        self.engine_status_label.setMinimumHeight(44)
        self.engine_status_label.setStyleSheet("font-size: 11px;")
        form.addRow("Currently running:", self.engine_status_label)

        refresh_status_btn = QPushButton("Refresh status")
        refresh_status_btn.clicked.connect(self._refresh_engine_status)
        form.addRow("", refresh_status_btn)
        self._refresh_engine_status()
        # A reload triggered by changing a combo above happens on a
        # background thread and isn't instant - poll for a few seconds
        # afterward so the label updates itself once it's done, instead of
        # only updating when you happen to click Refresh.
        self._engine_status_poll_timer = QTimer(self)
        self._engine_status_poll_timer.setInterval(1000)
        self._engine_status_poll_timer.timeout.connect(self._on_engine_status_poll_tick)
        self._engine_status_poll_ticks = 0

        return w

    def _refresh_engine_status(self):
        try:
            import transcribe
            self.engine_status_label.setText(transcribe.get_engine_status())
        except Exception as e:
            self.engine_status_label.setText(f"Couldn't check status: {e}")

    def _poll_engine_status_after_change(self):
        # Called right after a Whisper setting change kicks off a
        # background reload (see _apply_assistant_live()) - checks the
        # status label every second for a few seconds to catch the reload
        # finishing (or failing) without a manual refresh.
        self._engine_status_poll_ticks = 8
        self._engine_status_poll_timer.start()

    def _on_engine_status_poll_tick(self):
        self._refresh_engine_status()
        self._engine_status_poll_ticks -= 1
        if self._engine_status_poll_ticks <= 0:
            self._engine_status_poll_timer.stop()

    def _toggle_key_visibility(self, edit, show: bool):
        edit.setEchoMode(QLineEdit.Normal if show else QLineEdit.Password)
        edit._toggle_btn.setText("Hide" if show else "Show")

    def _reset_key_status(self, edit, status):
        edit.setStyleSheet("")
        status.setText("")

    def _start_key_verification(self, provider, edit, status, verify, checking):
        key = edit.text().strip()
        if not key:
            edit.setStyleSheet(f"border: 2px solid {self._t()['danger']};")
            status.setStyleSheet(f"font-size: 11px; color: {self._t()['danger']};")
            status.setText("Paste a key first.")
            return
        edit._verify_btn.setEnabled(False)
        edit._verify_btn.setText("…")
        edit.setStyleSheet("")
        status.setStyleSheet(f"font-size: 11px; color: {self._t()['subtext']};")
        status.setText(checking)
        edit._verified_value = key
        threading.Thread(
            target=self._verify_key, args=(provider, verify, key), daemon=True
        ).start()

    def _verify_key(self, provider, verify, key):
        result = verify(key)
        valid, message = result[:2]
        self._key_verified_signal.emit(
            provider, valid, message, result[2] if len(result) > 2 else []
        )

    def _on_key_verification_result(self, provider, valid, message, items):
        edit, status, target, item_name = {
            "gemini": (self.gemini_key_edit, self.gemini_key_status_label, self.gemini_model_edit, "models"),
            "openai": (self.openai_key_edit, self.openai_key_status_label, self.openai_model_edit, "models"),
            "anthropic": (self.anthropic_key_edit, self.anthropic_key_status_label, self.anthropic_model_edit, "models"),
            "elevenlabs": (self.elevenlabs_key_edit, self.elevenlabs_key_status_label, self.elevenlabs_voice_combo, "voices"),
            "youtube": (self.youtube_key_edit, self.youtube_status_label, None, "items"),
        }[provider]
        edit._verify_btn.setEnabled(True)
        edit._verify_btn.setText("Verify")
        if edit.text().strip() != edit._verified_value:
            return
        color = self._t()["success" if valid else "danger"]
        edit.setStyleSheet(f"border: 2px solid {color};")
        status.setStyleSheet(f"font-size: 11px; color: {color};")
        if not valid:
            status.setText(f"✗ {message}")
        elif items:
            self._populate_model_combo(target, items)
            status.setText(f"✓ Valid key - {len(items)} {item_name} loaded.")
        else:
            status.setText("✓ Valid key.")

    def _toggle_gemini_key_visibility(self, show):
        self._toggle_key_visibility(self.gemini_key_edit, show)

    def _reset_gemini_key_verify_status(self, *args):
        self._reset_key_status(self.gemini_key_edit, self.gemini_key_status_label)

    def _start_gemini_key_verify(self):
        self._start_key_verification("gemini", self.gemini_key_edit, self.gemini_key_status_label, _verify_gemini_key, "Checking with Gemini…")

    def _toggle_openai_key_visibility(self, show):
        self._toggle_key_visibility(self.openai_key_edit, show)

    def _reset_openai_key_verify_status(self, *args):
        self._reset_key_status(self.openai_key_edit, self.openai_key_status_label)

    def _start_openai_key_verify(self):
        self._start_key_verification("openai", self.openai_key_edit, self.openai_key_status_label, _verify_openai_key, "Checking with OpenAI…")

    def _toggle_anthropic_key_visibility(self, show):
        self._toggle_key_visibility(self.anthropic_key_edit, show)

    def _reset_anthropic_key_verify_status(self, *args):
        self._reset_key_status(self.anthropic_key_edit, self.anthropic_key_status_label)

    def _start_anthropic_key_verify(self):
        self._start_key_verification("anthropic", self.anthropic_key_edit, self.anthropic_key_status_label, _verify_anthropic_key, "Checking with Anthropic…")

    # -- Custom OpenAI-compatible provider: "Fetch models" doubles as its
    # verify step, since there's no separate cheap auth-only probe. --------
    def _start_custom_models_fetch(self):
        base_url = self.custom_base_url_edit.text().strip()
        key = self.custom_key_edit.text().strip()
        if not base_url:
            self.custom_models_status_label.setStyleSheet(f"font-size: 11px; color: {self._t()['danger']};")
            self.custom_models_status_label.setText("Enter a base URL first.")
            return
        if not _is_http_url(base_url):
            self.custom_base_url_edit.setStyleSheet(f"border: 2px solid {self._t()['danger']};")
            self.custom_models_status_label.setStyleSheet(f"font-size: 11px; color: {self._t()['danger']};")
            self.custom_models_status_label.setText("Enter a valid http:// or https:// base URL.")
            return

        self._custom_fetch_models_btn.setEnabled(False)
        self._custom_fetch_models_btn.setText("…")
        self.custom_models_status_label.setStyleSheet(f"font-size: 11px; color: {self._t()['subtext']};")
        self.custom_models_status_label.setText("Fetching model list…")
        self._last_custom_fetch_checked = (base_url, key)
        threading.Thread(
            target=self._do_fetch_custom_models, args=(base_url, key), daemon=True
        ).start()

    def _do_fetch_custom_models(self, base_url: str, key: str):
        ok, message, models = _fetch_custom_openai_models(base_url, key)
        self._custom_models_fetched_signal.emit(ok, message, models)

    def _on_custom_models_fetch_result(self, ok: bool, message: str, models: list):
        self._custom_fetch_models_btn.setEnabled(True)
        self._custom_fetch_models_btn.setText("Fetch models")
        current = (self.custom_base_url_edit.text().strip(), self.custom_key_edit.text().strip())
        if current != self._last_custom_fetch_checked:
            return  # user changed the URL/key again while this was in flight

        if ok and models:
            self._populate_model_combo(self.custom_model_edit, models)
            self.custom_models_status_label.setStyleSheet(f"font-size: 11px; color: {self._t()['success']};")
            self.custom_models_status_label.setText(f"✓ {len(models)} models loaded.")
        else:
            self.custom_models_status_label.setStyleSheet(f"font-size: 11px; color: {self._t()['danger']};")
            self.custom_models_status_label.setText(f"✗ {message}")

    def _toggle_elevenlabs_key_visibility(self, show):
        self._toggle_key_visibility(self.elevenlabs_key_edit, show)

    def _reset_elevenlabs_key_verify_status(self, *args):
        self._reset_key_status(self.elevenlabs_key_edit, self.elevenlabs_key_status_label)

    def _start_elevenlabs_key_verify(self):
        self._start_key_verification(
            "elevenlabs", self.elevenlabs_key_edit,
            self.elevenlabs_key_status_label, _verify_elevenlabs_key,
            "Loading voices from ElevenLabs…",
        )

    # -- Edge TTS "Browse all voices" - opens VoiceBrowserDialog, which
    # fetches the full catalog on its own background thread. -------------
    def _open_edge_voice_browser(self):
        dialog = VoiceBrowserDialog(self, dark=self.companion.settings)
        dialog.voice_picked.connect(self._on_edge_voice_picked)
        dialog.exec()

    def _on_edge_voice_picked(self, short_name: str):
        if self.voice_combo.findText(short_name) < 0:
            self.voice_combo.addItem(short_name)
        self.voice_combo.setCurrentText(short_name)

    # -- Spotify credential verification (mirrors the Gemini block above,
    # except it checks a Client ID/Secret pair together instead of a
    # single key) -----------------------------------------------------------
    def _toggle_spotify_secret_visibility(self, show: bool):
        self._toggle_key_visibility(self.spotify_client_secret_edit, show)

    def _reset_spotify_verify_status(self, *args):
        self._reset_key_status(self.spotify_client_secret_edit, self.spotify_status_label)

    def _start_spotify_verify(self):
        client_id = self.spotify_client_id_edit.text().strip()
        client_secret = self.spotify_client_secret_edit.text().strip()
        if not client_id or not client_secret:
            self.spotify_client_secret_edit.setStyleSheet(f"border: 2px solid {self._t()['danger']};")
            self.spotify_status_label.setStyleSheet(f"font-size: 11px; color: {self._t()['danger']};")
            self.spotify_status_label.setText("Fill in both the Client ID and Client Secret first.")
            return

        self.spotify_client_secret_edit._verify_btn.setEnabled(False)
        self.spotify_client_secret_edit._verify_btn.setText("…")
        self.spotify_client_secret_edit.setStyleSheet("")
        self.spotify_status_label.setStyleSheet(f"font-size: 11px; color: {self._t()['subtext']};")
        self.spotify_status_label.setText("Checking with Spotify…")
        self._last_verified_spotify_checked = (client_id, client_secret)
        threading.Thread(
            target=self._do_verify_spotify, args=(client_id, client_secret), daemon=True
        ).start()

    def _do_verify_spotify(self, client_id: str, client_secret: str):
        valid, message = _verify_spotify_credentials(client_id, client_secret)
        self._spotify_verified_signal.emit(valid, message)

    def _on_spotify_verify_result(self, valid: bool, message: str):
        self.spotify_client_secret_edit._verify_btn.setEnabled(True)
        self.spotify_client_secret_edit._verify_btn.setText("Verify")
        current = (self.spotify_client_id_edit.text().strip(), self.spotify_client_secret_edit.text().strip())
        if current != self._last_verified_spotify_checked:
            return
        if valid:
            self.spotify_client_secret_edit.setStyleSheet(f"border: 2px solid {self._t()['success']};")
            self.spotify_status_label.setStyleSheet(f"font-size: 11px; color: {self._t()['success']};")
            self.spotify_status_label.setText("✓ Valid credentials.")
        else:
            self.spotify_client_secret_edit.setStyleSheet(f"border: 2px solid {self._t()['danger']};")
            self.spotify_status_label.setStyleSheet(f"font-size: 11px; color: {self._t()['danger']};")
            self.spotify_status_label.setText(f"✗ {message}")

    def _toggle_youtube_key_visibility(self, show):
        self._toggle_key_visibility(self.youtube_key_edit, show)

    def _reset_youtube_key_verify_status(self, *args):
        self._reset_key_status(self.youtube_key_edit, self.youtube_status_label)

    def _start_youtube_key_verify(self):
        self._start_key_verification(
            "youtube", self.youtube_key_edit, self.youtube_status_label,
            _verify_youtube_key, "Checking with YouTube…",
        )

    def _apply_theme_stylesheet(self):
        self.setStyleSheet("")
        self.setStyleSheet(self._build_style(self.companion.settings))

    def _repolish(self):
        """Force existing widgets to pick up live theme changes."""
        for w in [self] + self.findChildren(QWidget):
            w.style().unpolish(w)
            w.style().polish(w)
            w.update()

    def _on_theme_changed(self, index: int):
        theme_key = self.theme_combo.itemData(index)
        if not theme_key:
            return
        is_dark = (theme_key not in ["light", "lavender"])
        self.dark_mode_check.blockSignals(True)
        self.dark_mode_check.setChecked(is_dark)
        self.dark_mode_check.blockSignals(False)

        self.companion.apply_companion_settings({
            "color_theme": theme_key,
            "dark_mode": is_dark,
        })
        if hasattr(self, "_last_companion_form_values"):
            self._last_companion_form_values.update({
                "color_theme": theme_key,
                "dark_mode": is_dark,
            })
        self._apply_theme_stylesheet()
        self._repolish()

    def _on_dark_mode_toggled(self, dark: bool):
        new_key = "dark" if dark else "light"
        idx = self.theme_combo.findData(new_key)
        if idx >= 0 and self.theme_combo.currentIndex() != idx:
            self.theme_combo.blockSignals(True)
            self.theme_combo.setCurrentIndex(idx)
            self.theme_combo.blockSignals(False)
        self.companion.apply_companion_settings({
            "color_theme": new_key,
            "dark_mode": dark,
        })
        if hasattr(self, "_last_companion_form_values"):
            self._last_companion_form_values.update({
                "color_theme": new_key,
                "dark_mode": dark,
            })
        self._apply_theme_stylesheet()
        self._repolish()

    def _on_show_console_toggled(self, show: bool):
        hide = not show
        try:
            config_path = os.path.join(_BASE_DIR, "config.py")
            with open(config_path, "r", encoding="utf-8") as f:
                text = _patch_config_line(f.read(), "HIDE_CONSOLE_WINDOW", str(hide))
            _atomic_write_text(config_path, text)
        except OSError as e:
            self.show_console_check.blockSignals(True)
            self.show_console_check.setChecked(not config.HIDE_CONSOLE_WINDOW)
            self.show_console_check.blockSignals(False)
            QMessageBox.warning(self, "Settings", f"Couldn't save command prompt setting: {e}")
            return

        config.HIDE_CONSOLE_WINDOW = hide
        _set_console_visible(show)

    # -- Startup (Task Scheduler, Windows only) ------------------------------
    def _refresh_startup_status(self):
        """Queries whether the AlyssaAssistant scheduled task currently
        exists - this only reads, so unlike installing/removing it, no
        admin prompt is needed just to check."""
        try:
            # CREATE_NO_WINDOW keeps this invisible instead of flashing a
            # console window every time Settings opens.
            result = subprocess.run(
                ["schtasks", "/Query", "/TN", "AlyssaAssistant"],
                capture_output=True, timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            registered = result.returncode == 0
        except Exception:
            registered = False
        self.startup_status_label.setText(
            "✓ Currently starts automatically at login, with admin rights."
            if registered else
            "Not currently set to start at login."
        )

    def _startup_script_path(self, filename: str) -> str:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)

    def _on_enable_startup(self):
        path = self._startup_script_path("install_startup.bat")
        if not os.path.exists(path):
            self.startup_status_label.setText("⚠ install_startup.bat wasn't found next to main.py.")
            return
        try:
            os.startfile(path)
        except OSError as e:
            self.startup_status_label.setText(f"⚠ Couldn't launch install_startup.bat: {e}")
            return
        self.startup_status_label.setText(
            "A window opened asking for admin permission - approve it, "
            "then reopen Settings to confirm."
        )

    def _on_disable_startup(self):
        path = self._startup_script_path("uninstall_startup.bat")
        if not os.path.exists(path):
            self.startup_status_label.setText("⚠ uninstall_startup.bat wasn't found next to main.py.")
            return
        try:
            os.startfile(path)
        except OSError as e:
            self.startup_status_label.setText(f"⚠ Couldn't launch uninstall_startup.bat: {e}")
            return
        self.startup_status_label.setText(
            "A window opened asking for admin permission - approve it, "
            "then reopen Settings to confirm."
        )

    # -- Companion tab -----------------------------------------------------
    def _build_companion_tab(self) -> _QW:
        w = _QW()
        w.setObjectName("tabPage")
        outer = QVBoxLayout(w)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(10)

        top_row = QGridLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setHorizontalSpacing(16)
        top_row.setVerticalSpacing(12)
        self._companion_grid = top_row

        self._preview_panel = _QW()
        preview_col = QVBoxLayout(self._preview_panel)
        preview_col.setContentsMargins(0, 0, 0, 0)
        preview_col.setSpacing(2)
        preview_col.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        self._preview_layout = preview_col
        self.preview_label = QLabel()
        self.preview_label.setFixedSize(140, 198)
        self.preview_label.setScaledContents(True)
        preview_col.addWidget(self.preview_label, 0, Qt.AlignHCenter)
        self.preview_talking_check = QCheckBox("Preview talking")
        self.preview_talking_check.toggled.connect(self._refresh_preview)
        preview_col.addWidget(self.preview_talking_check, 0, Qt.AlignHCenter)
        top_row.addWidget(self._preview_panel, 0, 0, Qt.AlignTop)

        self._companion_form_panel = _QW()
        form_col = QVBoxLayout(self._companion_form_panel)
        form_col.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self._responsive_forms.append(form)

        form.addRow("", self._section_header("Character Appearance", "🎨", first=True))
        self.image_edit = QLineEdit(self.companion.settings.get("character_image", ""))
        self.image_edit.setPlaceholderText("(default: assets/nottalk.png, mouth closed)")
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_image)
        default_btn = QPushButton("Use Default")
        default_btn.clicked.connect(self._use_default_image)
        img_row = QHBoxLayout()
        img_row.addWidget(self.image_edit)
        img_row.addWidget(browse_btn)
        img_row.addWidget(default_btn)
        form.addRow("Not-talking image:", img_row)

        self.talk_image_edit = QLineEdit(self.companion.settings.get("character_image_talking", ""))
        self.talk_image_edit.setPlaceholderText("(default: assets/talkopen.png, mouth open)")
        talk_browse_btn = QPushButton("Browse…")
        talk_browse_btn.clicked.connect(self._browse_talk_image)
        talk_default_btn = QPushButton("Use Default")
        talk_default_btn.clicked.connect(self._use_default_talk_image)
        talk_img_row = QHBoxLayout()
        talk_img_row.addWidget(self.talk_image_edit)
        talk_img_row.addWidget(talk_browse_btn)
        talk_img_row.addWidget(talk_default_btn)
        form.addRow("Talking image:", talk_img_row)

        self.scale_slider = QSlider(Qt.Horizontal)
        self.scale_slider.setRange(50, 250)
        self.scale_slider.setValue(int(self.companion.settings.get("scale", 1.0) * 100))
        form.addRow("Size:", self.scale_slider)

        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(30, 100)
        self.opacity_slider.setValue(int(self.companion.settings.get("opacity", 1.0) * 100))
        form.addRow("Opacity:", self.opacity_slider)

        form.addRow("", self._section_header("Window Behavior", "🪟"))
        self.always_on_top_check = QCheckBox("Always on top")
        self.always_on_top_check.setChecked(bool(self.companion.settings.get("always_on_top", True)))
        form.addRow("", self.always_on_top_check)

        self.theme_combo = QComboBox()
        current_theme = self.companion.settings.get("color_theme")
        if not current_theme:
            current_theme = "dark" if self.companion.settings.get("dark_mode", False) else "light"
        for key, info in COLOR_THEMES.items():
            self.theme_combo.addItem(info.get("name", key.title()), key)
        idx = self.theme_combo.findData(current_theme)
        if idx >= 0:
            self.theme_combo.setCurrentIndex(idx)
        else:
            self.theme_combo.setCurrentIndex(0)
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        form.addRow("Color theme:", self.theme_combo)

        self.dark_mode_check = QCheckBox("Dark mode (Settings window + speech bubble)")
        self.dark_mode_check.setChecked(bool(self.companion.settings.get("dark_mode", False)))
        self.dark_mode_check.toggled.connect(self._on_dark_mode_toggled)
        form.addRow("", self.dark_mode_check)

        if sys.platform.startswith("win"):
            form.addRow("", self._section_header("Startup", "🚀"))
            self.startup_status_label = self._help_label("")
            self.startup_status_label.setStyleSheet(f"color: {self._t()['subtext']}; font-size: 11px;")
            form.addRow("", self.startup_status_label)

            startup_row = QHBoxLayout()
            self.startup_enable_btn = QPushButton("Enable admin startup")
            self.startup_enable_btn.setToolTip(
                "Start Alyssa as administrator at Windows login."
            )
            self.startup_enable_btn.clicked.connect(self._on_enable_startup)
            startup_row.addWidget(self.startup_enable_btn)

            self.startup_disable_btn = QPushButton("Disable")
            self.startup_disable_btn.setToolTip("Disable startup at Windows login.")
            self.startup_disable_btn.clicked.connect(self._on_disable_startup)
            startup_row.addWidget(self.startup_disable_btn)
            form.addRow("Start at login (admin):", startup_row)

            self._refresh_startup_status()

        form.addRow("", self._section_header("Speech Bubble & Chat Box", "💬"))
        self.bubble_seconds_spin = QSpinBox()
        self.bubble_seconds_spin.setRange(2, 30)
        self.bubble_seconds_spin.setValue(int(self.companion.settings.get("bubble_seconds", 7)))
        form.addRow("Bubble lingers after she's done (sec):", self.bubble_seconds_spin)

        self.chatbox_enabled_check = QCheckBox("Show typed-command chat box")
        self.chatbox_enabled_check.setChecked(bool(self.companion.settings.get("chatbox_enabled", True)))
        self.chatbox_enabled_check.setToolTip(
            "Type commands instead of speaking them."
        )
        form.addRow("", self.chatbox_enabled_check)

        self.chatbox_position_combo = QComboBox()
        self.chatbox_position_combo.addItem("Bottom", "bottom")
        self.chatbox_position_combo.addItem("Left", "left")
        self.chatbox_position_combo.addItem("Right", "right")
        current_pos = self.companion.settings.get("chatbox_position", "bottom")
        idx = self.chatbox_position_combo.findData(current_pos)
        self.chatbox_position_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.chatbox_position_combo.setEnabled(self.chatbox_enabled_check.isChecked())
        self.chatbox_enabled_check.toggled.connect(self.chatbox_position_combo.setEnabled)
        form.addRow("Chat box position:", self.chatbox_position_combo)

        # -- PNGTuber talk animation --
        form.addRow("", self._section_header("Talk Animation", "🗣️"))
        self.mouth_flap_check = QCheckBox("Swap talk/not-talk image while speaking")
        self.mouth_flap_check.setChecked(bool(self.companion.settings.get("talk_mouth_flap_enabled", True)))
        self.mouth_flap_check.toggled.connect(self._refresh_preview)
        form.addRow("", self.mouth_flap_check)

        self.bounce_check = QCheckBox("Bounce while talking")
        self.bounce_check.setChecked(bool(self.companion.settings.get("talk_bounce_enabled", True)))
        form.addRow("", self.bounce_check)

        self.bounce_height_spin = QSpinBox()
        self.bounce_height_spin.setRange(0, 60)
        self.bounce_height_spin.setSuffix(" px")
        self.bounce_height_spin.setValue(int(self.companion.settings.get("talk_bounce_height", 0)))
        form.addRow("Bounce height:", self.bounce_height_spin)

        self.dim_idle_check = QCheckBox("Dim her while she's not talking")
        self.dim_idle_check.setChecked(bool(self.companion.settings.get("dim_when_idle_enabled", False)))
        form.addRow("", self.dim_idle_check)

        self.dim_idle_spin = QSpinBox()
        self.dim_idle_spin.setRange(10, 100)
        self.dim_idle_spin.setSuffix("% opacity")
        self.dim_idle_spin.setValue(int(self.companion.settings.get("dim_when_idle_opacity", 55)))
        form.addRow("Dimmed level:", self.dim_idle_spin)

        form_col.addLayout(form)
        top_row.addWidget(self._companion_form_panel, 0, 1)
        top_row.setColumnStretch(1, 1)
        outer.addLayout(top_row)

        for widget, signal in [
            (self.scale_slider, self.scale_slider.valueChanged),
            (self.opacity_slider, self.opacity_slider.valueChanged),
        ]:
            signal.connect(self._refresh_preview)
        self.image_edit.textChanged.connect(self._refresh_preview)
        self.talk_image_edit.textChanged.connect(self._refresh_preview)

        # Every control on this tab feeds the same live-apply debounce
        # timer, so the companion window (and overlay_config.json) picks
        # up each change without a Save click.
        for widget, signal in [
            (self.image_edit, self.image_edit.editingFinished),
            (self.talk_image_edit, self.talk_image_edit.editingFinished),
            (self.scale_slider, self.scale_slider.valueChanged),
            (self.opacity_slider, self.opacity_slider.valueChanged),
            (self.always_on_top_check, self.always_on_top_check.toggled),
            (self.bubble_seconds_spin, self.bubble_seconds_spin.valueChanged),
            (self.chatbox_enabled_check, self.chatbox_enabled_check.toggled),
            (self.chatbox_position_combo, self.chatbox_position_combo.currentIndexChanged),
            (self.mouth_flap_check, self.mouth_flap_check.toggled),
            (self.bounce_check, self.bounce_check.toggled),
            (self.bounce_height_spin, self.bounce_height_spin.valueChanged),
            (self.dim_idle_check, self.dim_idle_check.toggled),
            (self.dim_idle_spin, self.dim_idle_spin.valueChanged),
        ]:
            signal.connect(self._queue_companion_apply)

        self._refresh_preview()
        return w

    def _browse_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose a not-talking (mouth closed) character image",
            "",
            "Images (*.png *.jpg *.jpeg *.gif *.svg)",
        )
        if path:
            self.image_edit.setText(path)
            self._queue_companion_apply()

    def _use_default_image(self):
        self.image_edit.setText("")
        self._queue_companion_apply()

    def _browse_talk_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose a talking (mouth open) character image",
            "",
            "Images (*.png *.jpg *.jpeg *.gif *.svg)",
        )
        if path:
            self.talk_image_edit.setText(path)
            self._queue_companion_apply()

    def _use_default_talk_image(self):
        self.talk_image_edit.setText("")
        self._queue_companion_apply()

    def _refresh_preview(self, *args):
        preview_settings = dict(self.companion.settings)
        preview_settings["character_image"] = self.image_edit.text().strip()
        preview_settings["character_image_talking"] = self.talk_image_edit.text().strip()
        talking_preview = self.preview_talking_check.isChecked() and self.mouth_flap_check.isChecked()
        pixmap = render_character(preview_settings, QSize(280, 396), mouth_open=talking_preview)
        self.preview_label.setPixmap(
            pixmap.scaled(140, 198, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )

    # -- live apply ----------------------------------------------------------
    # Every field on both tabs applies itself as you change it (debounced -
    # see the two QTimers set up in __init__) instead of waiting for a Save
    # click. Close just dismisses the window; there's nothing left to commit.

    def _gather_companion_settings(self) -> dict:
        return {
            "character_image": self._validated_image_path(
                self.image_edit, "character_image"
            ),
            "character_image_talking": self._validated_image_path(
                self.talk_image_edit, "character_image_talking"
            ),
            "scale": self.scale_slider.value() / 100.0,
            "opacity": self.opacity_slider.value() / 100.0,
            "always_on_top": self.always_on_top_check.isChecked(),
            "bubble_seconds": self.bubble_seconds_spin.value(),
            "chatbox_enabled": self.chatbox_enabled_check.isChecked(),
            "chatbox_position": self.chatbox_position_combo.currentData(),
            "talk_mouth_flap_enabled": self.mouth_flap_check.isChecked(),
            "talk_bounce_enabled": self.bounce_check.isChecked(),
            "talk_bounce_height": self.bounce_height_spin.value(),
            "dim_when_idle_enabled": self.dim_idle_check.isChecked(),
            "dim_when_idle_opacity": self.dim_idle_spin.value(),
            "color_theme": self.theme_combo.currentData(),
            "dark_mode": self.dark_mode_check.isChecked(),
        }

    def _validated_image_path(self, edit: QLineEdit, setting_key: str) -> str:
        value = edit.text().strip()
        valid = not value or (
            os.path.isfile(value)
            and os.path.splitext(value)[1].lower() in {".png", ".jpg", ".jpeg", ".gif", ".svg"}
        )
        edit.setStyleSheet("" if valid else f"border: 2px solid {self._t()['danger']};")
        edit.setToolTip("" if valid else "Choose an existing PNG, JPG, GIF, or SVG file.")
        return value if valid else self.companion.settings.get(setting_key, "")

    def _queue_companion_apply(self, *args):
        # Restarting a single-shot timer on every call is what turns a
        # burst of signals (dragging a slider, typing a path) into one
        # apply shortly after things settle, instead of one per event.
        self._companion_apply_timer.start()

    def sync_companion_scale(self, scale: float):
        """Keep the open form in sync after drag-resizing the companion."""
        value = round(float(scale) * 100)
        self.scale_slider.blockSignals(True)
        self.scale_slider.setValue(value)
        self.scale_slider.blockSignals(False)
        if hasattr(self, "_last_companion_form_values"):
            self._last_companion_form_values["scale"] = self.scale_slider.value() / 100.0
        self._refresh_preview()

    # -- Plugins tab ----------------------------------------------------
    def _build_plugins_tab(self) -> _QW:
        """A little in-app IDE for plugins/*.py: a file list on the left
        (green dot = enabled, gray dot = disabled - see plugin_loader.py's
        underscore-prefix convention) and a syntax-highlighted code editor
        on the right. Unlike the other tabs, nothing here applies live as
        you type - Python is too easy to leave momentarily broken
        mid-edit, so changes only take effect on an explicit Save, and
        Save is also the moment reload_plugins()/reload_plugin_tools() run
        to pick the change up in this same running session."""
        w = _QW()
        w.setObjectName("tabPage")
        outer = QVBoxLayout(w)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(10)

        intro = self._help_label(
            "Edit plugins or create one from the template."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {self._t()['subtext']}; font-size: 11px;")
        outer.addWidget(intro)

        splitter = QSplitter(Qt.Horizontal)
        self._plugin_splitter = splitter
        splitter.setChildrenCollapsible(False)
        # Default handle renders as a stark, unstyled bar in this theme -
        # given a little width and a themed color via QSplitter::handle
        # in _build_style instead, so it reads as a subtle divider.
        splitter.setHandleWidth(10)

        # -- Left: file list + list-level actions --
        left = _QW()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)

        self.plugin_list = QListWidget()
        self.plugin_list.setObjectName("pluginList")
        _apply_elevation(self.plugin_list, blur=20, y=3, alpha=80)
        self.plugin_list.currentItemChanged.connect(self._on_plugin_selected)
        left_layout.addWidget(self.plugin_list, 1)

        new_plugin_btn = QPushButton("+ New Plugin")
        new_plugin_btn.clicked.connect(self._on_new_plugin)
        left_layout.addWidget(new_plugin_btn)

        reload_btn = QPushButton("⟳ Reload Plugins")
        reload_btn.setToolTip("Reload plugin changes now.")
        reload_btn.clicked.connect(self._on_reload_plugins)
        left_layout.addWidget(reload_btn)

        splitter.addWidget(left)

        # -- Right: header row (filename, enable toggle, save/delete) + editor --
        right = _QW()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(10, 0, 0, 0)
        right_layout.setSpacing(8)

        header_widget = _QW()
        header_row = QGridLayout(header_widget)
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setHorizontalSpacing(8)
        header_row.setVerticalSpacing(8)
        self._plugin_header_grid = header_row
        self.plugin_file_label = QLabel("Select a plugin to edit")
        self.plugin_file_label.setObjectName("pluginFileLabel")
        header_row.addWidget(self.plugin_file_label, 0, 0)
        header_row.setColumnStretch(0, 1)

        self.plugin_enable_btn = QPushButton("Enable")
        self.plugin_enable_btn.setObjectName("compactButton")
        self.plugin_enable_btn.setCheckable(True)
        self.plugin_enable_btn.clicked.connect(self._on_toggle_plugin_enabled)
        header_row.addWidget(self.plugin_enable_btn, 0, 1)

        self.plugin_save_btn = QPushButton("💾 Save")
        self.plugin_save_btn.setObjectName("primaryButton")
        self.plugin_save_btn.clicked.connect(self._on_save_plugin)
        header_row.addWidget(self.plugin_save_btn, 0, 2)

        self.plugin_delete_btn = QPushButton("Delete")
        self.plugin_delete_btn.setObjectName("pluginDangerButton")
        self.plugin_delete_btn.clicked.connect(self._on_delete_plugin)
        header_row.addWidget(self.plugin_delete_btn, 0, 3)
        right_layout.addWidget(header_widget)

        self.plugin_status_label = self._help_label("")
        self.plugin_status_label.setStyleSheet(f"color: {self._t()['subtext']}; font-size: 11px;")
        right_layout.addWidget(self.plugin_status_label)

        self.plugin_editor = QPlainTextEdit()
        self.plugin_editor.setObjectName("pluginEditor")
        self.plugin_editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        mono = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        mono.setPointSize(11)
        self.plugin_editor.setFont(mono)
        self.plugin_editor.textChanged.connect(self._on_plugin_text_changed)
        # ponytail: syntax highlighter removed (yagni for a settings-dialog editor)
        right_layout.addWidget(self.plugin_editor, 1)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([190, 500])
        outer.addWidget(splitter, 1)

        # Selected filename on disk (e.g. "weather.py" or "_weather.py" if
        # disabled) and whether the editor has unsaved changes - both used
        # by the handlers below.
        self._current_plugin_file = None
        self._plugin_dirty = False
        self._refresh_plugin_list()
        self._set_plugin_editor_enabled(False)
        return w

    def _refresh_plugin_list(self, select: str = None):
        """Repopulates self.plugin_list from plugins/*.py on disk. `select`
        (a filename) re-selects that item afterward if given - used after
        creating/renaming a plugin so the list doesn't lose the user's
        place."""
        self.plugin_list.blockSignals(True)
        self.plugin_list.clear()
        plugins_dir = plugin_loader.PLUGINS_DIR
        if os.path.isdir(plugins_dir):
            for filename in sorted(os.listdir(plugins_dir)):
                if not filename.endswith(".py"):
                    continue
                enabled = not filename.startswith("_")
                display_name = filename[1:] if not enabled else filename
                dot = "🟢" if enabled else "⚪"
                item = QListWidgetItem(f"{dot}  {display_name}")
                item.setData(Qt.UserRole, filename)
                item.setToolTip("Loaded at startup" if enabled else "Not loaded")
                self.plugin_list.addItem(item)
                if select and filename == select:
                    self.plugin_list.setCurrentItem(item)
        self.plugin_list.blockSignals(False)
        if select is None and self.plugin_list.count() and self.plugin_list.currentRow() < 0:
            self.plugin_list.setCurrentRow(0)
        elif self.plugin_list.count() == 0:
            self._current_plugin_file = None
            self._set_plugin_editor_enabled(False)

    def _set_plugin_editor_enabled(self, enabled: bool):
        self.plugin_editor.setEnabled(enabled)
        self.plugin_enable_btn.setEnabled(enabled)
        self.plugin_save_btn.setEnabled(enabled)
        self.plugin_delete_btn.setEnabled(enabled)
        if not enabled:
            self.plugin_editor.blockSignals(True)
            self.plugin_editor.setPlainText("")
            self.plugin_editor.blockSignals(False)
            self.plugin_file_label.setText(
                "No plugins yet - click \"+ New Plugin\" to create one"
                if self.plugin_list.count() == 0 else "Select a plugin to edit"
            )
            self.plugin_status_label.setText("")

    def _on_plugin_selected(self, current: QListWidgetItem, previous: QListWidgetItem):
        if previous is not None and self._plugin_dirty:
            self._save_plugin_file(previous.data(Qt.UserRole), reload_after=False, quiet=True)
        if current is None:
            self._current_plugin_file = None
            self._set_plugin_editor_enabled(False)
            return

        filename = current.data(Qt.UserRole)
        self._current_plugin_file = filename
        path = os.path.join(plugin_loader.PLUGINS_DIR, filename)
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError as e:
            content = f"# Couldn't read this file: {e}"

        self._set_plugin_editor_enabled(True)
        self.plugin_editor.blockSignals(True)
        self.plugin_editor.setPlainText(content)
        self.plugin_editor.blockSignals(False)
        self._plugin_dirty = False

        enabled = not filename.startswith("_")
        self.plugin_enable_btn.setChecked(enabled)
        self.plugin_enable_btn.setText("Enabled ✓" if enabled else "Disabled")
        self.plugin_file_label.setText(filename[1:] if not enabled else filename)
        self.plugin_status_label.setText("")

    def _on_plugin_text_changed(self):
        self._plugin_dirty = True
        if self._current_plugin_file:
            name = self._current_plugin_file
            name = name[1:] if name.startswith("_") else name
            self.plugin_file_label.setText(f"{name} •")

    def _on_toggle_plugin_enabled(self, checked: bool):
        if not self._current_plugin_file:
            return
        old_name = self._current_plugin_file
        bare_name = old_name[1:] if old_name.startswith("_") else old_name
        new_name = bare_name if checked else f"_{bare_name}"
        if new_name == old_name:
            return

        plugins_dir = plugin_loader.PLUGINS_DIR
        old_path = os.path.join(plugins_dir, old_name)
        new_path = os.path.join(plugins_dir, new_name)
        try:
            # Flush any unsaved edits into the file before the rename, so
            # toggling enable/disable never silently discards them.
            if self._plugin_dirty:
                with open(old_path, "w", encoding="utf-8") as f:
                    f.write(self.plugin_editor.toPlainText())
                self._plugin_dirty = False
            os.rename(old_path, new_path)
        except OSError as e:
            self.plugin_status_label.setText(f"⚠ Couldn't rename file: {e}")
            self.plugin_enable_btn.setChecked(not checked)
            return

        self._current_plugin_file = new_name
        self.plugin_enable_btn.setText("Enabled ✓" if checked else "Disabled")
        self._reload_plugins_backend()
        self._refresh_plugin_list(select=new_name)
        self.plugin_status_label.setText(
            f"✓ {bare_name} enabled and reloaded" if checked else f"{bare_name} disabled"
        )

    def _save_plugin_file(self, filename: str, reload_after: bool = True, quiet: bool = False) -> bool:
        if not filename:
            return False
        path = os.path.join(plugin_loader.PLUGINS_DIR, filename)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.plugin_editor.toPlainText())
        except OSError as e:
            if not quiet:
                self.plugin_status_label.setText(f"⚠ Couldn't save: {e}")
            return False
        self._plugin_dirty = False
        bare_name = filename[1:] if filename.startswith("_") else filename
        self.plugin_file_label.setText(bare_name)
        if reload_after:
            self._reload_plugins_backend()
            if not quiet:
                self.plugin_status_label.setText(f"✓ Saved and reloaded {bare_name}")
        return True

    def _on_save_plugin(self):
        if self._current_plugin_file:
            self._save_plugin_file(self._current_plugin_file)

    def _reload_plugins_backend(self):
        """Re-scans plugins/ and pushes the result into the already-running
        session - actions.FUNCTIONS/PLUGIN_TOOLS and brain.TOOLS - so a
        save in this tab is usable immediately without restarting Alyssa."""
        try:
            actions.reload_plugins()
            brain.reload_plugin_tools()
        except Exception as e:
            self.plugin_status_label.setText(f"⚠ Saved, but reload hit an error: {e}")

    def _on_reload_plugins(self):
        if self._current_plugin_file and self._plugin_dirty:
            self._save_plugin_file(self._current_plugin_file, reload_after=False, quiet=True)
        self._reload_plugins_backend()
        selected = self._current_plugin_file
        self._refresh_plugin_list(select=selected)
        errors = plugin_loader.get_load_errors()
        count = len(actions.PLUGIN_FUNCTIONS)
        if errors:
            self.plugin_status_label.setText(f"⚠ Reloaded - {count} loaded, but: {'; '.join(errors)}")
        else:
            self.plugin_status_label.setText(f"✓ Reloaded - {count} plugin function(s) active")

    def _on_new_plugin(self):
        name, ok = QInputDialog.getText(
            self, "New Plugin", "Plugin name (letters, numbers, underscores):"
        )
        if not ok or not name.strip():
            return
        safe = re.sub(r"[^a-zA-Z0-9_]", "_", name.strip().lower())
        if not safe:
            return
        filename = f"{safe}.py"
        path = os.path.join(plugin_loader.PLUGINS_DIR, filename)
        if os.path.exists(path):
            QMessageBox.warning(self, "Already exists", f"'{filename}' already exists in plugins/.")
            return

        template = f'''"""
{safe} plugin - describe what this adds here.
"""

def {safe}_example(message: str) -> str:
    """Replace this with your own function. Whatever it returns is what
    gets spoken/shown back to the user."""
    return f"{{message}}, from the {safe} plugin!"


FUNCTIONS = {{
    "{safe}_example": {safe}_example,
}}

TOOLS = [
    {{
        "type": "function",
        "function": {{
            "name": "{safe}_example",
            "description": "Explain here when the assistant should call this.",
            "parameters": {{
                "type": "object",
                "properties": {{
                    "message": {{"type": "string", "description": "What to say."}}
                }},
                "required": ["message"],
            }},
        }},
    }},
]
'''
        try:
            os.makedirs(plugin_loader.PLUGINS_DIR, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(template)
        except OSError as e:
            QMessageBox.critical(self, "Couldn't create plugin", str(e))
            return

        self._reload_plugins_backend()
        self._refresh_plugin_list(select=filename)
        self.plugin_status_label.setText(f"✓ Created {filename} - edit it, then Save")

    def _on_delete_plugin(self):
        if not self._current_plugin_file:
            return
        filename = self._current_plugin_file
        bare_name = filename[1:] if filename.startswith("_") else filename
        confirm = QMessageBox.question(
            self, "Delete plugin",
            f"Permanently delete '{bare_name}'? This can't be undone.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        path = os.path.join(plugin_loader.PLUGINS_DIR, filename)
        try:
            os.remove(path)
        except OSError as e:
            QMessageBox.critical(self, "Couldn't delete", str(e))
            return
        self._current_plugin_file = None
        self._plugin_dirty = False
        self._reload_plugins_backend()
        self._refresh_plugin_list()
        self.plugin_status_label.setText(f"Deleted {bare_name}")

    def _apply_companion_live(self):
        current = self._gather_companion_settings()
        changed = {
            key: value for key, value in current.items()
            if self._last_companion_form_values.get(key) != value
        }
        self._last_companion_form_values = current
        if changed:
            self.companion.apply_companion_settings(changed)

    def _gather_assistant_values(self) -> dict:
        custom_base_url = self.custom_base_url_edit.text().strip()
        if not _is_http_url(custom_base_url):
            self.custom_base_url_edit.setStyleSheet(f"border: 2px solid {self._t()['danger']};")
            self.custom_models_status_label.setStyleSheet(f"font-size: 11px; color: {self._t()['danger']};")
            self.custom_models_status_label.setText("Enter a valid http:// or https:// base URL.")
            custom_base_url = config.CUSTOM_BASE_URL
        else:
            self.custom_base_url_edit.setStyleSheet("")
        return {
            "ASSISTANT_NAME": self.name_edit.text().strip() or config.ASSISTANT_NAME,
            "LLM_PROVIDER": self.provider_combo.currentText(),
            "OLLAMA_MODEL": self.ollama_model_edit.text().strip() or config.OLLAMA_MODEL,
            "GEMINI_MODEL": self.gemini_model_edit.currentText().strip() or config.GEMINI_MODEL,
            "OPENAI_MODEL": self.openai_model_edit.currentText().strip() or getattr(config, "OPENAI_MODEL", ""),
            "ANTHROPIC_MODEL": self.anthropic_model_edit.currentText().strip() or getattr(config, "ANTHROPIC_MODEL", ""),
            "CUSTOM_BASE_URL": custom_base_url,
            "CUSTOM_MODEL": self.custom_model_edit.currentText().strip(),
            "TTS_PROVIDER": self.tts_provider_combo.currentText(),
            "EDGE_TTS_VOICE": self.voice_combo.currentText().strip() or config.EDGE_TTS_VOICE,
            "EDGE_TTS_VOLUME": _percent_to_volume_str(self.volume_slider.value()),
            "ELEVENLABS_VOICE_ID": _extract_elevenlabs_voice_id(self.elevenlabs_voice_combo.currentText()),
            "ELEVENLABS_MODEL": (
                self.elevenlabs_model_combo.currentText().strip() or getattr(config, "ELEVENLABS_MODEL", "eleven_multilingual_v2")
            ),
            "SPEAK_RESPONSES": self.speak_check.isChecked(),
            "CAVEMAN_MODE": self.caveman_combo.currentData(),
            "CONFIRM_BEFORE_ACTIONS": self.confirm_check.isChecked(),
            "HIDE_CONSOLE_WINDOW": not self.show_console_check.isChecked(),
            "WHISPER_MODEL_SIZE": self.whisper_model_combo.currentText().strip() or config.WHISPER_MODEL_SIZE,
            "WHISPER_DEVICE": self._whisper_device_display_to_value.get(self.whisper_device_combo.currentText(), "auto"),
            "WHISPER_COMPUTE_TYPE": self.whisper_compute_combo.currentText().strip() or "auto",
            "CONVERSATION_MEMORY_TURNS": self.conversation_turns_spin.value(),
            "CONVERSATION_TIMEOUT_SECONDS": self.conversation_timeout_spin.value(),
            "MAX_SAVED_MEMORIES": self.max_saved_memories_spin.value(),
            "MAX_MEMORY_FACT_CHARACTERS": self.max_memory_chars_spin.value(),
            "MAX_MEMORIES_IN_PROMPT": self.max_memories_in_prompt_spin.value(),
            "AUTO_DETECT_LOCATION": self.auto_detect_location_check.isChecked(),
            "WEATHER_DEFAULT_LOCATION": self.weather_default_location_edit.text().strip(),
            "WEATHER_UNITS": self.weather_units_combo.currentData() or "imperial",
            "GEMINI_API_KEY": self.gemini_key_edit.text().strip(),
            "OPENAI_API_KEY": self.openai_key_edit.text().strip(),
            "ANTHROPIC_API_KEY": self.anthropic_key_edit.text().strip(),
            "CUSTOM_API_KEY": self.custom_key_edit.text().strip(),
            "ELEVENLABS_API_KEY": self.elevenlabs_key_edit.text().strip(),
            "SPOTIFY_CLIENT_ID": self.spotify_client_id_edit.text().strip(),
            "SPOTIFY_CLIENT_SECRET": self.spotify_client_secret_edit.text().strip(),
            "YOUTUBE_API_KEY": self.youtube_key_edit.text().strip(),
        }

    def _gather_assistant_updates(self, values=None) -> dict:
        values = values or self._gather_assistant_values()
        originals = {
            "GEMINI_API_KEY": self._original_gemini_key,
            "OPENAI_API_KEY": self._original_openai_key,
            "ANTHROPIC_API_KEY": self._original_anthropic_key,
            "CUSTOM_API_KEY": self._original_custom_key,
            "ELEVENLABS_API_KEY": self._original_elevenlabs_key,
            "SPOTIFY_CLIENT_ID": self._original_spotify_client_id,
            "SPOTIFY_CLIENT_SECRET": self._original_spotify_client_secret,
            "YOUTUBE_API_KEY": self._original_youtube_key,
        }
        return {
            key: repr(value) for key, value in values.items()
            if key not in originals or value != originals[key]
        }

    def _queue_assistant_apply(self, *args):
        self._assistant_apply_timer.start()

    def _apply_edge_volume_live(self, value: int):
        # Synthesis and active playback both read this value directly; only
        # the config.py write remains debounced while the slider is dragged.
        config.EDGE_TTS_VOLUME = _percent_to_volume_str(value)
        self._queue_assistant_apply()

    def _apply_assistant_live(self):
        # Patch config.py on disk + this session's already-imported config
        # module, so changes take effect immediately without a restart.
        values = self._gather_assistant_values()
        updates = self._gather_assistant_updates(values)
        try:
            config_path = os.path.join(_BASE_DIR, "config.py")
            with open(config_path, "r", encoding="utf-8") as f:
                text = f.read()
            for key, literal in updates.items():
                text = _patch_config_line(text, key, literal)
            _atomic_write_text(config_path, text)
        except OSError as e:
            QMessageBox.warning(self, "Settings", f"Couldn't write changes to config.py: {e}")
            return

        old_hide = config.HIDE_CONSOLE_WINDOW
        old_whisper = (config.WHISPER_MODEL_SIZE, config.WHISPER_DEVICE, config.WHISPER_COMPUTE_TYPE)
        old_spotify = (config.SPOTIFY_CLIENT_ID, config.SPOTIFY_CLIENT_SECRET)
        for key, value in values.items():
            setattr(config, key, value)

        if old_hide != config.HIDE_CONSOLE_WINDOW:
            _set_console_visible(not config.HIDE_CONSOLE_WINDOW)
        whisper_settings_changed = old_whisper != (
            config.WHISPER_MODEL_SIZE, config.WHISPER_DEVICE, config.WHISPER_COMPUTE_TYPE
        )
        if whisper_settings_changed:
            # transcribe.py caches the loaded Whisper model for the process
            # lifetime - without this, changing model/device/compute type
            # here would patch config.py but the already-loaded model
            # would keep being reused, silently ignoring the change until
            # a full restart. reload_model_async() drops the cached model.
            try:
                import transcribe
                transcribe.reload_model_async()
                # Refresh the Engine tab's live status label a few times
                # over the next several seconds, so it picks up the reload
                # finishing (or a forced GPU choice quietly failing back to
                # CPU) without you having to click Refresh yourself.
                if hasattr(self, "_poll_engine_status_after_change"):
                    self._poll_engine_status_after_change()
            except ImportError:
                pass
        if old_spotify != (config.SPOTIFY_CLIENT_ID, config.SPOTIFY_CLIENT_SECRET):
            # actions.py caches a Spotify access token in-process (valid
            # ~1hr) - without this, changing Client ID/Secret wouldn't
            # take effect until that cached token expired on its own.
            try:
                import actions
                actions._spotify_token_cache["access_token"] = None
                actions._spotify_token_cache["expires_at"] = 0
            except ImportError:
                pass

        # Actually start listening now if it wasn't already (e.g. this is
        # what just supplied a previously-missing Gemini key) - a no-op if
        # the assistant loop is already running.
        starter = getattr(self.companion, "start_assistant_worker", None)
        if starter is not None:
            starter()

        self.setWindowTitle(f"{config.ASSISTANT_NAME} - Settings")

    def _flush_pending_applies(self, *args):
        # Flush any pending debounced writes immediately instead of losing
        # up to 400ms of unapplied change if the window closes right after
        # the last edit. Hooked to `finished`, so this runs no matter how
        # the dialog closes - Close button, window X, or Esc.
        if self._companion_apply_timer.isActive():
            self._companion_apply_timer.stop()
            self._apply_companion_live()
        if self._assistant_apply_timer.isActive():
            self._assistant_apply_timer.stop()
            self._apply_assistant_live()
        if getattr(self, "_plugin_dirty", False) and getattr(self, "_current_plugin_file", None):
            self._save_plugin_file(self._current_plugin_file, reload_after=True, quiet=True)

    def _on_close(self):
        self.accept()


def _build_app_icon(size: int = 64, theme: dict = None) -> "QIcon":
    """A small on-brand app/tray icon, painted to match the current color
    theme's header gradient (Settings header, primary buttons) instead of
    a generic system icon - a soft circular badge with a simple sparkle,
    so Alyssa is recognizable at a glance in the taskbar/tray without
    needing a bundled image asset. Defaults to the light preset when no
    theme is given, same as before this became theme-aware."""
    t = theme or _THEME_LIGHT
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing)

    margin = size * 0.04
    circle_rect = QRect(int(margin), int(margin), int(size - 2 * margin), int(size - 2 * margin))
    gradient = QLinearGradient(0, 0, size, size)
    gradient.setColorAt(0.0, QColor(t["header_grad_start"]))
    gradient.setColorAt(1.0, QColor(t["header_grad_end"]))
    painter.setPen(Qt.NoPen)
    painter.setBrush(gradient)
    painter.drawEllipse(circle_rect)

    # A small four-point sparkle/star, off-white, centered slightly high -
    # echoes the little heart/sparkle accents on the bundled chibi art
    # without reproducing it.
    cx, cy = size / 2, size * 0.47
    r_out, r_in = size * 0.24, size * 0.09
    star = QPainterPath()
    for i in range(8):
        angle = math.pi / 4 * i - math.pi / 2
        r = r_out if i % 2 == 0 else r_in
        x, y = cx + r * math.cos(angle), cy + r * math.sin(angle)
        if i == 0:
            star.moveTo(x, y)
        else:
            star.lineTo(x, y)
    star.closeSubpath()
    painter.setBrush(QColor("#FFFFFF"))
    painter.drawPath(star)

    # A tiny pink dot "gem" beneath the sparkle for a touch of the
    # accent2 pink used throughout the rest of the palette.
    dot_r = size * 0.055
    painter.setBrush(QColor(t["accent2"]))
    painter.drawEllipse(QPoint(int(cx), int(size * 0.74)), int(dot_r), int(dot_r))

    painter.end()
    return QIcon(pm)


# --------------------------------------------------------------------------
# Tray icon -- lets you bring her back after "Hide", or quit, without
# needing to find the invisible, frameless window first.
# --------------------------------------------------------------------------
def _build_tray_icon(window: CompanionWindow, app: QApplication) -> QSystemTrayIcon:
    icon = _build_app_icon(theme=_theme(window.settings))
    tray = QSystemTrayIcon(icon)
    tray.setToolTip(config.ASSISTANT_NAME)

    menu = QMenu()
    menu.setAttribute(Qt.WA_TranslucentBackground)
    menu.setStyleSheet(_build_menu_style(window.settings))
    menu.aboutToShow.connect(lambda: menu.setStyleSheet(_build_menu_style(window.settings)))
    show_action = menu.addAction("Show / Hide")
    settings_action = menu.addAction("Settings…")
    menu.addSeparator()
    quit_action = menu.addAction("Quit")

    def _toggle():
        window.setVisible(not window.isVisible())

    show_action.triggered.connect(_toggle)
    settings_action.triggered.connect(window.open_settings)
    quit_action.triggered.connect(app.quit)

    tray.setContextMenu(menu)
    tray.activated.connect(lambda reason: _toggle() if reason == QSystemTrayIcon.Trigger else None)
    tray.show()
    return tray


# --------------------------------------------------------------------------
# Entry point used by main.py
# --------------------------------------------------------------------------
_console_window_handle = None


def _set_console_visible(visible: bool):
    """On Windows, shows or hides the console window this process is
    attached to - the cmd.exe window opened by start_alyssa.bat (or
    whatever terminal launched it) - controlled by HIDE_CONSOLE_WINDOW in
    config.py (or the matching Settings -> Assistant checkbox).

    Unlike _close_parent_console() below, this doesn't close that window
    or end the .bat script waiting on it - it only toggles whether it's
    drawn on screen, so it can be shown again later (e.g. by unchecking
    "Show command prompt" in Settings) to see startup errors,
    tracebacks, or DEBUG_PRINT_TRANSCRIPTS output. No-op (silently) on
    any platform other than Windows, or if there's no console attached at
    all (e.g. launched via pythonw.exe or a frozen windowed .exe with no
    console) - both cases where there's nothing to show or hide."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        global _console_window_handle
        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        kernel32.GetConsoleWindow.restype = wintypes.HWND
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.IsWindow.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.ShowWindowAsync.argtypes = [wintypes.HWND, ctypes.c_int]

        console_hwnd = kernel32.GetConsoleWindow()
        if not console_hwnd:
            return

        if _console_window_handle and not user32.IsWindow(_console_window_handle):
            _console_window_handle = None
        if _console_window_handle is None:
            if user32.IsWindowVisible(console_hwnd):
                _console_window_handle = console_hwnd
            else:
                foreground = user32.GetForegroundWindow()
                class_name = ctypes.create_unicode_buffer(64)
                user32.GetClassNameW(foreground, class_name, len(class_name))
                if class_name.value in {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS"}:
                    _console_window_handle = foreground

        hwnd = _console_window_handle or console_hwnd
        if hwnd:
            user32.ShowWindowAsync(hwnd, 9 if visible else 0)  # SW_RESTORE / SW_HIDE
    except Exception:
        pass  # best-effort - never let this stop Alyssa from starting


def _close_parent_console():
    """On Windows, asks the console window this process is attached to -
    the cmd.exe window opened by start_alyssa.bat (or whatever terminal
    launched it) - to close itself too.

    Quitting Alyssa only ends the Python process; the console window she
    was launched from stays open behind her (that's just how a console
    process works - the shell that ran the script doesn't close itself
    just because the script exited). GetConsoleWindow() finds that
    console's window handle, and posting it WM_CLOSE asks it to close the
    same way clicking its own [x] button would - including terminating
    the .bat script/cmd.exe that was waiting on this process, since they
    share one console. No-op (silently) on any platform other than
    Windows, or if there's no console attached at all (e.g. launched via
    pythonw.exe or a frozen windowed .exe with no console)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            WM_CLOSE = 0x0010
            ctypes.windll.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    except Exception:
        pass  # best-effort - never let this stop Alyssa from quitting


def run_with_assistant(assistant_loop_fn):
    """Starts the Qt GUI on the main thread and runs assistant_loop_fn(bridge)
    on a background thread. assistant_loop_fn should behave like main.py's
    run_assistant_loop(bridge): do its own listening/thinking/speaking loop,
    and call bridge.speak_signal.emit(text) (instead of / in addition to
    printing) whenever it wants something shown in the speech bubble."""
    # Kick off the Whisper model load now, before window/QApplication
    # setup - it's a plain CPU/GPU-bound background thread with no
    # dependency on Qt. This is intentionally redundant with the load
    # main.run_assistant_loop() itself kicks off shortly after; whichever
    # gets there first does the real loading and the other is a no-op.
    import transcribe
    threading.Thread(target=transcribe.preload, daemon=True).start()

    # Hide the cmd.exe console window behind her by default (see
    # HIDE_CONSOLE_WINDOW in config.py).
    _set_console_visible(not getattr(config, "HIDE_CONSOLE_WINDOW", True))

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(_build_app_icon())

    settings = load_overlay_settings()
    bridge = Bridge()
    window = CompanionWindow(settings, bridge)
    bubble = SpeechBubble(window, settings)
    window.bubble = bubble
    chatbar = ChatInputBar(window, settings, bridge)
    window.chatbar = chatbar

    bridge.speak_signal.connect(bubble.show_message)
    bridge.thinking_signal.connect(bubble.show_thinking)
    # NOTE: connect to bound methods here, not bare lambdas.
    # speak_signal/talk_*_signal/error_signal are emitted from the
    # background assistant thread - PySide6 only auto-queues a signal to
    # the GUI thread when the slot is a bound method of a QObject (it
    # reads the receiver's thread affinity). A bare lambda has no such
    # receiver, so it falls back to a direct same-thread call, meaning
    # set_talking()/QMessageBox.critical() would run on the background
    # thread, touching Qt widgets/timers unsafely - causing mouth-
    # animation glitches and occasional crashes.
    bridge.error_signal.connect(window.show_error)
    bridge.gemini_key_needed.connect(window.prompt_gemini_key_setup)
    bridge.reply_pending_signal.connect(bubble.on_reply_pending)
    bridge.talk_start_signal.connect(window.start_talking)
    bridge.talk_end_signal.connect(window.stop_talking)
    # Keeps the speech bubble's fade-out timed to when she actually
    # finishes speaking, rather than a fixed guess made when the text
    # first appeared (see SpeechBubble.on_talk_start/on_talk_end).
    bridge.talk_start_signal.connect(bubble.on_talk_start)
    bridge.talk_end_signal.connect(bubble.on_talk_end)
    if QSystemTrayIcon.isSystemTrayAvailable():
        window.tray = _build_tray_icon(window, app)

    window.show()
    chatbar.apply_enabled()

    # Qt can reinitialize the Windows console during application/window setup.
    # Re-apply the configured visibility after the GUI is fully initialized so
    # the Settings checkbox and config.py value reliably control the console.
    _set_console_visible(not getattr(config, "HIDE_CONSOLE_WINDOW", True))
    # The startup greeting is spoken by run_assistant_loop() itself (via
    # speak(), once preflight checks pass) - not shown here directly,
    # since showing it here too would flash a silent, unvoiced duplicate
    # before the real greeting replaces it a moment later.

    window._worker_thread = None

    def _start_worker():
        """(Re)starts the background listening loop if it isn't already
        running. Safe to call any number of times - a no-op while the loop
        is already alive. This is what makes "paste API key -> Save" in
        Settings actually start listening immediately: the loop exits
        right away on first launch if run_preflight_checks() fails (e.g.
        no key yet), so without this, nothing would ever restart it short
        of fully relaunching the app."""
        if window._worker_thread is not None and window._worker_thread.is_alive():
            return
        window._worker_thread = threading.Thread(
            target=assistant_loop_fn, args=(bridge,), daemon=True
        )
        window._worker_thread.start()

    window.start_assistant_worker = _start_worker
    _start_worker()

    # app.exec() only returns once app.quit() is called - and since
    # setQuitOnLastWindowClosed(False) is set above, only the "Quit"
    # actions in the right-click/tray menus do that (see
    # _show_context_menu and _build_tray_icon). So this is the reliable
    # spot to also close the console window she was launched from.
    exit_code = app.exec()
    _close_parent_console()
    sys.exit(exit_code)
