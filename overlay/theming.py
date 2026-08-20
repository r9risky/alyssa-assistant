import json
import os

from PySide6.QtCore import (
    QObject, QPropertyAnimation, QEasingCurve,
)
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget, QPushButton, QGraphicsDropShadowEffect, QGraphicsOpacityEffect,
)

def _load_color_themes() -> dict:
    """Load themes from color_themes.json next to this file, converting
    bubble_top/bottom/border/shadow from JSON arrays back to tuples."""
    json_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "color_themes.json")
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


_DIALOG_RADIUS = 6


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


def _apply_elevation(widget: QWidget, blur: int = 28, y: int = 6, alpha: int = 110) -> QGraphicsDropShadowEffect:
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
