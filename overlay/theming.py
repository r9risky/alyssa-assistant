import hashlib
import json
import os
import tempfile

from PySide6.QtCore import (
    Qt, QObject, QPropertyAnimation, QEasingCurve, QSize,
)
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QWidget, QPushButton, QGraphicsDropShadowEffect, QGraphicsOpacityEffect,
)


def _load_color_themes() -> dict:
    """Load repo-root color_themes.json; bubble_* lists → tuples. Hardcoded dark/light on error."""
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


_CODE_EDITOR_COLORS = {"bg": "#0D111A", "text": "#E2E8F0", "selection": "#334155"}


_DIALOG_RADIUS = 12


TYPE_SCALE = {
    "eyebrow": 10.5,  # QLabel#sectionHeader/-First letter-spaced labels
    "hint": 11.5,     # secondary/status/note labels
    "body": 13,       # QLabel, QCheckBox, default field text
}


def _rgba(hex_color: str, alpha: float) -> str:
    """'#RRGGBB' → 'rgba(r, g, b, alpha)'."""
    hex_color = hex_color.lstrip("#")
    r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    return f"rgba({r}, {g}, {b}, {alpha})"


def _is_light_color(hex_color: str) -> bool:
    """True when hex_color has high relative luminance (light theme background)."""
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return False
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (0.299 * r + 0.587 * g + 0.114 * b) > 165


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


_SVG_CACHE_DIR = os.path.join(tempfile.gettempdir(), "alyssa_ui_assets_v2")


def _write_cached_svg(name: str, svg_text: str) -> str:
    """Write SVG to a deterministic temp file and return forward-slash path for QSS url()."""
    try:
        os.makedirs(_SVG_CACHE_DIR, exist_ok=True)
        digest = hashlib.sha1(svg_text.encode("utf-8")).hexdigest()[:10]
        path = os.path.join(_SVG_CACHE_DIR, f"{name}_{digest}.svg")
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                f.write(svg_text)
        return path.replace("\\", "/")
    except OSError:
        return ""


def _theme_qss_assets(t: dict, is_light: bool) -> dict[str, str]:
    """Generate theme-colored SVG assets for toggle switches, combo arrows, and spin arrows."""
    accent = t["accent"]
    accent_hover = t["accent_hover"]
    subtext = t["subtext"]
    border = t["border"]
    track_off = "#CBD5E1" if is_light else "#263248"
    track_border = "#94A3B8" if is_light else border
    knob_off = "#FFFFFF" if is_light else "#CBD5E1"

    switch_off = _write_cached_svg(
        "switch_off",
        f"<svg xmlns='http://www.w3.org/2000/svg' width='36' height='20' viewBox='0 0 36 20'>"
        f"<rect x='1' y='1' width='34' height='18' rx='9' fill='{track_off}' stroke='{track_border}' stroke-width='1.2'/>"
        f"<circle cx='10' cy='10' r='6.2' fill='{knob_off}'/>"
        f"</svg>",
    )
    switch_off_hover = _write_cached_svg(
        "switch_off_hover",
        f"<svg xmlns='http://www.w3.org/2000/svg' width='36' height='20' viewBox='0 0 36 20'>"
        f"<rect x='1' y='1' width='34' height='18' rx='9' fill='{track_off}' stroke='{accent_hover}' stroke-width='1.5'/>"
        f"<circle cx='10' cy='10' r='6.5' fill='#FFFFFF'/>"
        f"</svg>",
    )
    switch_on = _write_cached_svg(
        "switch_on",
        f"<svg xmlns='http://www.w3.org/2000/svg' width='36' height='20' viewBox='0 0 36 20'>"
        f"<rect x='1' y='1' width='34' height='18' rx='9' fill='{accent}'/>"
        f"<circle cx='26' cy='10' r='6.5' fill='#FFFFFF'/>"
        f"</svg>",
    )
    switch_on_hover = _write_cached_svg(
        "switch_on_hover",
        f"<svg xmlns='http://www.w3.org/2000/svg' width='36' height='20' viewBox='0 0 36 20'>"
        f"<rect x='1' y='1' width='34' height='18' rx='9' fill='{accent_hover}'/>"
        f"<circle cx='26' cy='10' r='6.8' fill='#FFFFFF'/>"
        f"</svg>",
    )
    chevron_down = _write_cached_svg(
        "chevron_down",
        f"<svg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'>"
        f"<path d='M2.5 4.5 L6 8 L9.5 4.5' fill='none' stroke='{subtext}' stroke-width='1.7' stroke-linecap='round' stroke-linejoin='round'/>"
        f"</svg>",
    )
    chevron_down_accent = _write_cached_svg(
        "chevron_down_accent",
        f"<svg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'>"
        f"<path d='M2.5 4.5 L6 8 L9.5 4.5' fill='none' stroke='{accent}' stroke-width='1.9' stroke-linecap='round' stroke-linejoin='round'/>"
        f"</svg>",
    )
    spin_up = _write_cached_svg(
        "spin_up",
        f"<svg xmlns='http://www.w3.org/2000/svg' width='10' height='8' viewBox='0 0 10 8'>"
        f"<path d='M2 5.5 L5 2.5 L8 5.5' fill='none' stroke='{subtext}' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'/>"
        f"</svg>",
    )
    spin_down = _write_cached_svg(
        "spin_down",
        f"<svg xmlns='http://www.w3.org/2000/svg' width='10' height='8' viewBox='0 0 10 8'>"
        f"<path d='M2 2.5 L5 5.5 L8 2.5' fill='none' stroke='{subtext}' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'/>"
        f"</svg>",
    )
    return {
        "switch_off": switch_off,
        "switch_off_hover": switch_off_hover,
        "switch_on": switch_on,
        "switch_on_hover": switch_on_hover,
        "chevron_down": chevron_down,
        "chevron_down_accent": chevron_down_accent,
        "spin_up": spin_up,
        "spin_down": spin_down,
    }


_NAV_ICON_PATHS = {
    "assistant": (
        "<path d='M12 3a6 6 0 0 1 6 6c0 2.2-1.2 4.1-3 5.2V16a2 2 0 0 1-2 2h-2a2 2 0 0 1-2-2v-1.8C7.2 13.1 6 11.2 6 9a6 6 0 0 1 6-6z' fill='none' stroke='{c}' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'/>"
        "<path d='M9.5 21h5M10 9.5h4M12 7.5v4' fill='none' stroke='{c}' stroke-width='1.8' stroke-linecap='round'/>"
    ),
    "audio": (
        "<rect x='9' y='3' width='6' height='11' rx='3' fill='none' stroke='{c}' stroke-width='1.8'/>"
        "<path d='M5 10a7 7 0 0 0 14 0M12 17v4M8.5 21h7' fill='none' stroke='{c}' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'/>"
    ),
    "engine": (
        "<rect x='6' y='6' width='12' height='12' rx='2.5' fill='none' stroke='{c}' stroke-width='1.8'/>"
        "<rect x='9.5' y='9.5' width='5' height='5' rx='1' fill='none' stroke='{c}' stroke-width='1.6'/>"
        "<path d='M9 3v3M15 3v3M9 18v3M15 18v3M3 9h3M3 15h3M18 9h3M18 15h3' fill='none' stroke='{c}' stroke-width='1.8' stroke-linecap='round'/>"
    ),
    "companion": (
        "<circle cx='12' cy='8.5' r='4' fill='none' stroke='{c}' stroke-width='1.8'/>"
        "<path d='M5 20c0-3.6 3.1-6 7-6s7 2.4 7 6' fill='none' stroke='{c}' stroke-width='1.8' stroke-linecap='round'/>"
        "<path d='M18.5 4.5l.8 1.7 1.7.8-1.7.8-.8 1.7-.8-1.7-1.7-.8 1.7-.8z' fill='{c}'/>"
    ),
    "memory": (
        "<path d='M6 4h10a2 2 0 0 1 2 2v13a1 1 0 0 1-1.6.8L12 16.5 7.6 19.8A1 1 0 0 1 6 19V4z' fill='none' stroke='{c}' stroke-width='1.8' stroke-linejoin='round'/>"
        "<path d='M9.5 9h5M9.5 12.5h3.5' fill='none' stroke='{c}' stroke-width='1.8' stroke-linecap='round'/>"
    ),
    "plugins": (
        "<path d='M14.5 6.5a2 2 0 1 1 3 2.6L16 10.5l3 3a1.5 1.5 0 0 1 0 2.1l-3.4 3.4a1.5 1.5 0 0 1-2.1 0l-3-3-1.4 1.5a2 2 0 1 1-2.6-3L8 13 5 10a1.5 1.5 0 0 1 0-2.1L8.4 4.5a1.5 1.5 0 0 1 2.1 0l3 3z' fill='none' stroke='{c}' stroke-width='1.7' stroke-linejoin='round'/>"
    ),
    "updates": (
        "<path d='M20 11a8 8 0 1 0-2.1 5.4' fill='none' stroke='{c}' stroke-width='1.8' stroke-linecap='round'/>"
        "<polyline points='20 5 20 11 14 11' fill='none' stroke='{c}' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'/>"
    ),
    "custom": (
        "<rect x='4' y='4' width='7' height='7' rx='1.8' fill='none' stroke='{c}' stroke-width='1.8'/>"
        "<rect x='13' y='4' width='7' height='7' rx='1.8' fill='none' stroke='{c}' stroke-width='1.8'/>"
        "<rect x='4' y='13' width='7' height='7' rx='1.8' fill='none' stroke='{c}' stroke-width='1.8'/>"
        "<rect x='13' y='13' width='7' height='7' rx='1.8' fill='none' stroke='{c}' stroke-width='1.8'/>"
    ),
}


def build_nav_icon(kind: str, color_hex: str, size: int = 18) -> QIcon:
    """Render a crisp vector QIcon for the Settings sidebar navigation."""
    body = _NAV_ICON_PATHS.get(kind, _NAV_ICON_PATHS["custom"]).format(c=color_hex)
    svg = f"<svg xmlns='http://www.w3.org/2000/svg' width='{size}' height='{size}' viewBox='0 0 24 24'>{body}</svg>"
    renderer = QSvgRenderer(svg.encode("utf-8"))
    pix = QPixmap(QSize(size, size))
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    renderer.render(painter)
    painter.end()
    return QIcon(pix)


def _apply_elevation(widget: QWidget, blur: int = 28, y: int = 6, alpha: int = 110) -> QGraphicsDropShadowEffect:
    """Per-widget drop shadow (QSS has no box-shadow). One QGraphicsEffect per widget — do not call twice on the same widget."""
    shadow = QGraphicsDropShadowEffect(widget)
    shadow.setBlurRadius(blur)
    shadow.setOffset(0, y)
    shadow.setColor(QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(shadow)
    return shadow


class _HoverPress(QObject):
    """Press opacity dip on a QPushButton (QSS has no transitions). Event filter; keep a ref (`btn._press_fx = _HoverPress(btn)`) or GC stops it. Sets the widget's only QGraphicsEffect."""

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
            background: {_rgba(t['panel'], 0.98)};
            border: 1px solid {_rgba(t['border'], 0.85)};
            border-radius: 10px;
            padding: 6px;
            color: {t['text']};
            font-family: 'Segoe UI Variable Text', 'Segoe UI', -apple-system, BlinkMacSystemFont, 'Inter', sans-serif;
            font-size: 13px;
        }}
        QMenu::item {{
            padding: 8px 24px 8px 14px;
            border-radius: 6px;
            margin: 2px 3px;
            color: {t['text']};
            font-weight: 500;
        }}
        QMenu::item:selected {{
            background: {_rgba(t['accent'], 0.22)};
            color: {t['accent_hover'] if not _is_light_color(t['bg']) else t['accent']};
        }}
        QMenu::separator {{
            height: 1px;
            background: {_rgba(t['border'], 0.65)};
            margin: 6px 10px;
        }}
    """


def _build_messagebox_style(val) -> str:
    t = _theme(val)
    return (
        f"QMessageBox {{ background: {t['bg']}; font-family: 'Segoe UI Variable Text', 'Segoe UI', -apple-system, BlinkMacSystemFont, 'Inter', sans-serif; }}"
        f"QLabel {{ color: {t['text']}; font-size: 13px; }}"
        f"QPushButton {{"
        f"  background: {t['accent']}; color: #FFFFFF; border: none;"
        f"  border-radius: 7px; padding: 8px 20px; font-weight: 600; min-width: 72px;"
        f"}}"
        f"QPushButton:hover {{ background: {t['accent_hover']}; }}"
        f"QPushButton:pressed {{ background: {t['accent_press']}; }}"
    )


def build_voice_browser_style(dark) -> str:
    t = _theme(dark)
    is_light = _is_light_color(t["bg"])
    assets = _theme_qss_assets(t, is_light)
    r_xl, r_lg, r_md, r_sm = _DIALOG_RADIUS, 10, 7, 5
    f_eyebrow, f_hint, f_body = (
        TYPE_SCALE["eyebrow"], TYPE_SCALE["hint"], TYPE_SCALE["body"],
    )
    surface_canvas = t["bg"]
    surface_sidebar = _rgba(t["panel"], 0.98)
    surface_card = _rgba(t["panel"], 0.92) if not is_light else "#FFFFFF"
    surface_input = _rgba(t["card"], 0.85) if not is_light else "#F8FAFC"
    surface_input_focus = t["card"] if not is_light else "#FFFFFF"
    edge = _rgba(t["border"], 0.72) if not is_light else "rgba(15, 23, 42, 0.11)"
    edge_strong = _rgba(t["border"], 0.95) if not is_light else "rgba(15, 23, 42, 0.18)"
    edge_soft = "rgba(255, 255, 255, 0.05)" if not is_light else "rgba(15, 23, 42, 0.05)"
    accent_tint = _rgba(t["accent"], 0.14 if not is_light else 0.10)
    accent_border = _rgba(t["accent"], 0.42)

    switch_off_rule = f'image: url("{assets["switch_off"]}");' if assets["switch_off"] else ""
    switch_off_hover_rule = f'image: url("{assets["switch_off_hover"]}");' if assets["switch_off_hover"] else ""
    switch_on_rule = f'image: url("{assets["switch_on"]}");' if assets["switch_on"] else ""
    switch_on_hover_rule = f'image: url("{assets["switch_on_hover"]}");' if assets["switch_on_hover"] else ""
    chevron_rule = f'image: url("{assets["chevron_down"]}");' if assets["chevron_down"] else ""
    chevron_on_rule = f'image: url("{assets["chevron_down_accent"]}");' if assets["chevron_down_accent"] else ""
    spin_up_rule = f'image: url("{assets["spin_up"]}");' if assets["spin_up"] else ""
    spin_down_rule = f'image: url("{assets["spin_down"]}");' if assets["spin_down"] else ""

    return f"""
        * {{
            font-family: 'Segoe UI Variable Text', 'Segoe UI', -apple-system, BlinkMacSystemFont, 'Inter', sans-serif;
        }}
        QDialog {{
            background: {surface_canvas};
        }}
        QWidget#dialogContent {{
            background: {surface_canvas};
            border: none;
        }}
        QWidget#tabPage {{
            background: transparent;
        }}
        QWidget#sidebarTabs {{
            background: {surface_canvas};
            border: 1px solid {edge};
            border-radius: {r_xl}px;
        }}
        QFrame#sidebarContainer {{
            background: {surface_sidebar};
            border: none;
            border-right: 1px solid {edge};
            border-top-left-radius: {r_xl}px;
            border-bottom-left-radius: {r_xl}px;
        }}
        QLabel#sidebarBrandTitle {{
            color: {t['text']};
            font-size: 15px;
            font-weight: 700;
            background: transparent;
        }}
        QLabel#sidebarBrandSubtitle {{
            color: {t['subtext']};
            font-size: 11px;
            font-weight: 600;
            background: transparent;
        }}
        QLineEdit#sidebarSearch {{
            background: {surface_input};
            border: 1px solid {edge};
            border-radius: 7px;
            padding: 6px 10px;
            color: {t['text']};
            font-size: 11.5px;
        }}
        QLineEdit#sidebarSearch:focus {{
            border: 1.5px solid {t['accent']};
            background: {surface_input_focus};
        }}
        QListWidget#settingsNavigation {{
            background: transparent;
            border: none;
            padding: 4px 8px 10px 8px;
            outline: none;
        }}
        QListWidget#settingsNavigation::item {{
            color: {t['subtext']};
            border: 1px solid transparent;
            border-radius: 8px;
            padding: 0 12px;
            margin: 2px 0;
            font-size: 12.5px;
            font-weight: 600;
        }}
        QListWidget#settingsNavigation::item:selected {{
            color: {t['text']};
            background: {accent_tint};
            border: 1px solid {accent_border};
        }}
        QListWidget#settingsNavigation::item:hover:!selected {{
            color: {t['text']};
            background: {edge_soft};
        }}
        QStackedWidget#settingsPages {{
            background: {surface_canvas};
            border: none;
            border-top-right-radius: {r_xl}px;
            border-bottom-right-radius: {r_xl}px;
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
        QLabel#formFieldLabel {{
            color: {t['text']};
            font-size: 12.5px;
            font-weight: 600;
            padding-right: 6px;
        }}
        QFrame#pageHeader {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 {_rgba(t['panel'], 0.96)},
                stop:1 {_rgba(t['card'], 0.72)});
            border: 1px solid {edge};
            border-radius: {r_lg}px;
        }}
        QLabel#pageTitle {{
            color: {t['text']};
            font-size: 18px;
            font-weight: 700;
            background: transparent;
        }}
        QLabel#pageSubtitle {{
            color: {t['subtext']};
            font-size: 12px;
            background: transparent;
        }}
        QLabel#pageBadge {{
            background: {accent_tint};
            color: {t['accent_hover'] if not is_light else t['accent']};
            border: 1px solid {accent_border};
            border-radius: 11px;
            padding: 3px 10px;
            font-size: 11px;
            font-weight: 700;
        }}
        QFrame#settingsCard {{
            background: {surface_card};
            border: 1px solid {edge};
            border-radius: {r_lg}px;
        }}
        QFrame#previewShowcaseCard {{
            background: {surface_card};
            border: 1px solid {edge};
            border-radius: {r_lg}px;
        }}
        QFrame#cardDivider {{
            background: {edge};
            max-height: 1px;
            min-height: 1px;
            border: none;
        }}
        QLabel#cardIconBadge {{
            background: {accent_tint};
            color: {t['accent_hover'] if not is_light else t['accent']};
            border: 1px solid {accent_border};
            border-radius: 7px;
            font-size: 13px;
            font-weight: 700;
        }}
        QLabel#cardTitle {{
            color: {t['text']};
            font-size: 13.5px;
            font-weight: 700;
            background: transparent;
        }}
        QLabel#cardSubtitle {{
            color: {t['subtext']};
            font-size: 11.5px;
            background: transparent;
        }}
        QLabel#sectionHeader {{
            color: {t['accent_hover'] if not is_light else t['accent']};
            font-size: {f_eyebrow}px;
            font-weight: 700;
            letter-spacing: 1.1px;
            padding-top: 8px;
            padding-bottom: 4px;
        }}
        QLabel#sectionHeaderFirst {{
            color: {t['accent_hover'] if not is_light else t['accent']};
            font-size: {f_eyebrow}px;
            font-weight: 700;
            letter-spacing: 1.1px;
            padding-bottom: 4px;
        }}
        QLabel#sliderValueBadge {{
            background: {surface_input};
            color: {t['accent_hover'] if not is_light else t['accent']};
            border: 1px solid {edge};
            border-radius: 6px;
            padding: 3px 8px;
            font-size: 11.5px;
            font-weight: 700;
        }}
        QLabel#footerStatus {{
            color: {t['subtext']};
            font-size: 11.5px;
            background: transparent;
        }}
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
            background: {surface_input};
            border: 1px solid {edge_strong};
            border-radius: {r_md}px;
            padding: 7px 12px;
            min-height: 20px;
            color: {t['text']};
            font-size: 12.5px;
            selection-background-color: {t['accent']};
            selection-color: #FFFFFF;
        }}
        QPlainTextEdit {{
            background: {surface_input};
            border: 1px solid {edge_strong};
            border-radius: {r_md}px;
            padding: 8px 10px;
            color: {t['text']};
            font-size: 12px;
            selection-background-color: {t['accent']};
            selection-color: #FFFFFF;
        }}
        QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QPlainTextEdit:hover {{
            border: 1px solid {t['accent_hover']};
        }}
        QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus {{
            border: 1.5px solid {t['accent']};
            background: {surface_input_focus};
        }}
        QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QPlainTextEdit:disabled {{
            color: {t['subtext']};
            background: {edge_soft};
        }}
        QComboBox::drop-down {{
            border: none;
            width: 28px;
        }}
        QComboBox::down-arrow {{
            {chevron_rule}
            width: 12px;
            height: 12px;
            margin-right: 8px;
        }}
        QComboBox::down-arrow:on {{
            {chevron_on_rule}
        }}
        QComboBox QAbstractItemView {{
            background: {surface_sidebar};
            border: 1px solid {edge_strong};
            border-radius: {r_md}px;
            padding: 4px;
            color: {t['text']};
            selection-background-color: {accent_tint};
            selection-color: {t['accent_hover'] if not is_light else t['accent']};
            outline: none;
        }}
        QSpinBox::up-button, QDoubleSpinBox::up-button {{
            subcontrol-origin: border;
            subcontrol-position: top right;
            width: 20px;
            border: none;
            background: transparent;
            margin-top: 2px;
            margin-right: 3px;
        }}
        QSpinBox::down-button, QDoubleSpinBox::down-button {{
            subcontrol-origin: border;
            subcontrol-position: bottom right;
            width: 20px;
            border: none;
            background: transparent;
            margin-bottom: 2px;
            margin-right: 3px;
        }}
        QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
            {spin_up_rule}
            width: 10px;
            height: 8px;
        }}
        QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
            {spin_down_rule}
            width: 10px;
            height: 8px;
        }}
        QCheckBox {{
            color: {t['text']};
            font-size: 12.5px;
            font-weight: 500;
            spacing: 10px;
            padding: 3px 0;
        }}
        QCheckBox::indicator {{
            width: 36px;
            height: 20px;
            border: none;
            background: transparent;
            {switch_off_rule}
        }}
        QCheckBox::indicator:hover:!checked {{
            {switch_off_hover_rule}
        }}
        QCheckBox::indicator:checked {{
            {switch_on_rule}
        }}
        QCheckBox::indicator:checked:hover {{
            {switch_on_hover_rule}
        }}
        QSlider::groove:horizontal {{
            height: 6px;
            background: {t['slider_groove']};
            border-radius: 3px;
        }}
        QSlider::handle:horizontal {{
            width: 16px;
            height: 16px;
            margin: -5px 0;
            border-radius: 8px;
            background: #FFFFFF;
            border: 3px solid {t['accent']};
        }}
        QSlider::handle:horizontal:hover {{
            border: 3px solid {t['accent_hover']};
            background: #FFFFFF;
        }}
        QSlider::sub-page:horizontal {{
            background: {t['slider_fill']};
            border-radius: 3px;
        }}
        QPushButton {{
            background: {surface_input};
            color: {t['text']};
            border: 1px solid {edge_strong};
            border-radius: {r_md}px;
            padding: 7px 16px;
            font-size: 12px;
            font-weight: 600;
        }}
        QPushButton:hover {{
            border: 1px solid {t['accent_hover']};
            color: {t['accent_hover'] if not is_light else t['accent']};
            background: {surface_input_focus};
        }}
        QPushButton:pressed {{
            background: {accent_tint};
        }}
        QPushButton#primaryButton {{
            background: {t['accent']};
            color: #FFFFFF;
            border: 1px solid {_rgba('#FFFFFF', 0.14)};
            padding: 8px 22px;
            font-weight: 700;
        }}
        QPushButton#primaryButton:hover {{
            background: {t['accent_hover']};
            color: #FFFFFF;
        }}
        QPushButton#primaryButton:pressed {{
            background: {t['accent_press']};
        }}
        QPushButton#secondaryButton {{
            background: {surface_card};
            color: {t['text']};
            border: 1px solid {edge_strong};
            padding: 8px 20px;
            font-weight: 600;
        }}
        QPushButton#secondaryButton:hover {{
            border: 1px solid {t['accent_hover']};
            background: {surface_input_focus};
        }}
        QPushButton#compactButton {{
            padding: 5px 10px;
            border-radius: {r_sm}px;
            font-size: 11.5px;
        }}
        QScrollBar:vertical {{
            background: transparent;
            width: 8px;
            margin: 2px;
        }}
        QScrollBar::handle:vertical {{
            background: {edge_strong};
            border-radius: 4px;
            min-height: 28px;
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
        QScrollBar:horizontal {{
            background: transparent;
            height: 8px;
            margin: 2px;
        }}
        QScrollBar::handle:horizontal {{
            background: {edge_strong};
            border-radius: 4px;
            min-width: 28px;
        }}
        QScrollBar::handle:horizontal:hover {{
            background: {t['accent_hover']};
        }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
            width: 0px;
        }}
        QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
            background: transparent;
        }}
        QToolTip {{
            background: {surface_sidebar};
            color: {t['text']};
            border: 1px solid {edge_strong};
            border-radius: 6px;
            padding: 5px 9px;
            font-size: 11.5px;
        }}
        QListWidget#pluginList {{
            background: {surface_card};
            border: 1px solid {edge};
            border-radius: {r_lg}px;
            padding: 6px;
            outline: none;
            font-size: 12px;
        }}
        QListWidget#pluginList::item {{
            color: {t['text']};
            border-radius: 6px;
            padding: 7px 10px;
            margin: 1px 0;
        }}
        QListWidget#pluginList::item:selected {{
            background: {accent_tint};
            color: {t['text']};
            border: 1px solid {accent_border};
        }}
        QListWidget#pluginList::item:hover:!selected {{
            background: {edge_soft};
        }}
        QPlainTextEdit#pluginEditor {{
            background: {_CODE_EDITOR_COLORS['bg']};
            color: {_CODE_EDITOR_COLORS['text']};
            border: 1px solid {edge};
            border-radius: {r_lg}px;
            padding: 12px 14px;
            font-family: 'Cascadia Code', 'Consolas', 'Fira Code', monospace;
            font-size: 12px;
            selection-background-color: {_CODE_EDITOR_COLORS['selection']};
            selection-color: #FFFFFF;
        }}
        QLabel#pluginFileLabel {{
            color: {t['text']};
            font-size: 13.5px;
            font-weight: 700;
            background: transparent;
        }}
        QLabel#pluginEmptyState {{
            color: {t['subtext']};
            font-size: 13px;
            background: transparent;
        }}
        QLabel#pluginDot {{
            font-size: 12px;
            background: transparent;
        }}
        QPushButton#pluginDangerButton {{
            border: 1px solid {_rgba(t['danger'], 0.65)};
            color: {t['danger']};
            background: {_rgba(t['danger'], 0.08)};
        }}
        QPushButton#pluginDangerButton:hover {{
            background: {t['danger']};
            color: #FFFFFF;
            border: 1px solid {t['danger']};
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
