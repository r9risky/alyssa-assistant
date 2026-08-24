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

def build_voice_browser_style(dark: bool) -> str:
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
