import json
import os
import sys

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QPixmap, QPainter
from PySide6.QtSvg import QSvgRenderer

if getattr(sys, "frozen", False):
    _BASE_DIR = os.path.dirname(sys.executable)
else:
    _BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

OVERLAY_CONFIG_FILE = os.path.join(_BASE_DIR, "overlay_config.json")


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
    """Image path for the current talk state (shared by render_character and GIF playback).

    mouth_open: talk_path → BUNDLED_TALK_IMAGE → idle_path → BUNDLED_IDLE_IMAGE.
    idle: idle_path → BUNDLED_IDLE_IMAGE.
    First existing path wins; else empty string.
    """
    idle_path = settings.get("character_image") or ""
    talk_path = settings.get("character_image_talking") or ""
    if mouth_open:
        candidates = [talk_path, BUNDLED_TALK_IMAGE, idle_path, BUNDLED_IDLE_IMAGE]
    else:
        candidates = [idle_path, BUNDLED_IDLE_IMAGE]
    return next((p for p in candidates if p and os.path.exists(p)), "")


def _scale_centered(src: QPixmap, size: QSize) -> QPixmap:
    """Scale `src` to fit `size` (KeepAspectRatio) and center on a transparent canvas of exactly `size`."""
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
    """Render the character at `size`, KeepAspectRatio + centered.

    - mouth_open: talking vs idle via _resolve_character_path.
    - Missing/unreadable file → transparent pixmap of `size`.
    - .gif: first frame only (Settings preview). Live GIF is CompanionWindow QMovie, not this.
    """
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
            out = QPixmap(size)  # unreadable file → transparent
            out.fill(Qt.transparent)
            return out

        return _cache_get_or_render(cache_key, _render_file)

    # No custom path and bundled assets missing — transparent placeholder.
    out = QPixmap(size)
    out.fill(Qt.transparent)
    return out
