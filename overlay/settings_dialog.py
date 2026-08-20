import os
import re
import subprocess
import sys
import threading

from PySide6.QtCore import (
    Qt, QTimer, QSize, Signal,
)
from PySide6.QtGui import QGuiApplication, QFontDatabase
from PySide6.QtWidgets import (
    QWidget, QDialog, QFormLayout, QVBoxLayout,
    QHBoxLayout, QGridLayout, QLineEdit, QComboBox, QSizePolicy,
    QCheckBox, QSlider, QSpinBox, QPushButton, QLabel, QFileDialog,
    QMessageBox, QInputDialog, QStackedWidget,
    QScrollArea, QFrame, QSplitter, QListWidget, QListWidgetItem,
    QPlainTextEdit,
)

_QW = QWidget

import actions
import brain
import config
import credential_store
import plugin_loader
import updater

try:
    import sounddevice as sd
except ImportError:  # pragma: no cover - sounddevice is a core dependency
    sd = None

from .app_shell import _set_console_visible
from .credential_checks import (
    _atomic_write_text, _extract_elevenlabs_voice_id, _fetch_custom_openai_models,
    _is_http_url, _patch_config_line, _percent_to_volume_str,
    _verify_anthropic_key, _verify_elevenlabs_key, _verify_gemini_key,
    _verify_openai_key, _verify_spotify_credentials, _verify_youtube_key,
    _volume_str_to_percent,
)
from .rendering import _BASE_DIR, render_character
from .theming import (
    COLOR_THEMES, TYPE_SCALE, _CODE_EDITOR_COLORS, _DIALOG_RADIUS,
    _HoverPress, _apply_elevation, _rgba, _theme,
)
from .widgets import CompanionWindow

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
    _update_checked_signal = Signal(bool, object)
    _update_installed_signal = Signal(bool, str)

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
        self._update_checked_signal.connect(self._on_update_checked)
        self._update_installed_signal.connect(self._on_update_installed)

        self.tabs = _SidebarTabs(self)
        self.tabs.addTab(self._wrap_in_scroll_area(self._build_assistant_tab()), "Assistant")
        self.tabs.addTab(self._wrap_in_scroll_area(self._build_engine_tab()), "Engine")
        self.tabs.addTab(self._wrap_in_scroll_area(self._build_audio_tab()), "Audio")
        self.tabs.addTab(self._wrap_in_scroll_area(self._build_companion_tab()), "Companion")
        # Not wrapped in _wrap_in_scroll_area like the tabs above - this
        # tab's own splitter (plugin list + code editor) needs to claim
        # the full available height itself rather than being able to
        # shrink to content size inside a QScrollArea.
        self.tabs.addTab(self._build_plugins_tab(), "Plugins")
        self.tabs.addTab(self._wrap_in_scroll_area(self._build_updates_tab()), "Updates")

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
        self.show_console_check.setChecked(not bool(getattr(config, "HIDE_CONSOLE_WINDOW", False)))
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

    # -- Audio tab (input/output device picker) -----------------------------
    def _populate_device_combo(self, combo: QComboBox, channel_key: str, configured_value: str):
        """Fills combo with "Default" plus every device exposing
        channel_key (max_input_channels/max_output_channels), each item's
        data holding the exact string that goes in config.py. Selects
        "Default" or whichever device name contains configured_value."""
        combo.addItem("Default", "default")
        configured = str(configured_value or "").strip()
        matched = False
        try:
            devices = sd.query_devices() if sd is not None else []
        except Exception:
            devices = []
        seen_names = set()
        for device in devices:
            if device.get(channel_key, 0) <= 0:
                continue
            name = device["name"]
            if name in seen_names:
                # Same physical device often shows up once per Windows
                # audio driver (MME, DirectSound, WASAPI, WDM-KS) - the
                # matching in recorder.py/voice.py is name-based anyway,
                # so listing every duplicate just adds clutter without
                # adding a real choice.
                continue
            seen_names.add(name)
            combo.addItem(name, name)
            if configured and configured.lower() != "default" and configured.lower() in name.lower():
                combo.setCurrentText(name)
                matched = True
        if not matched and configured and configured.lower() != "default":
            # Configured device isn't currently plugged in/detected - keep
            # its name selectable so saving other fields doesn't silently
            # drop it.
            combo.addItem(f"{configured} (not detected)", configured)
            combo.setCurrentText(f"{configured} (not detected)")

    def _build_audio_tab(self) -> _QW:
        w = _QW()
        w.setObjectName("tabPage")
        form = QFormLayout(w)
        form.setContentsMargins(16, 16, 16, 16)
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self._responsive_forms.append(form)

        form.addRow("", self._section_header("Audio", "🔊", first=True))

        self.audio_input_combo = QComboBox()
        self._populate_device_combo(
            self.audio_input_combo, "max_input_channels", getattr(config, "MICROPHONE_DEVICE", "")
        )
        self.audio_input_combo.setToolTip("Microphone Alyssa listens on.")
        self.audio_input_combo.currentIndexChanged.connect(self._queue_assistant_apply)
        form.addRow("Input:", self.audio_input_combo)

        self.audio_output_combo = QComboBox()
        self._populate_device_combo(
            self.audio_output_combo, "max_output_channels", getattr(config, "AUDIO_OUTPUT_DEVICE", "")
        )
        self.audio_output_combo.setToolTip("Speaker/headset replies play through.")
        self.audio_output_combo.currentIndexChanged.connect(self._queue_assistant_apply)
        form.addRow("Output:", self.audio_output_combo)

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
        return os.path.join(_BASE_DIR, "scripts", filename)

    def _on_enable_startup(self):
        path = self._startup_script_path("install_startup.bat")
        if not os.path.exists(path):
            self.startup_status_label.setText("⚠ scripts\\install_startup.bat wasn't found.")
            return
        try:
            os.startfile(path)
        except OSError as e:
            self.startup_status_label.setText(f"⚠ Couldn't launch scripts\\install_startup.bat: {e}")
            return
        self.startup_status_label.setText(
            "A window opened asking for admin permission - approve it, "
            "then reopen Settings to confirm."
        )

    def _on_disable_startup(self):
        path = self._startup_script_path("uninstall_startup.bat")
        if not os.path.exists(path):
            self.startup_status_label.setText("⚠ scripts\\uninstall_startup.bat wasn't found.")
            return
        try:
            os.startfile(path)
        except OSError as e:
            self.startup_status_label.setText(f"⚠ Couldn't launch scripts\\uninstall_startup.bat: {e}")
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

    # -- Updates tab ----------------------------------------------------
    def _build_updates_tab(self) -> _QW:
        w = _QW()
        w.setObjectName("tabPage")
        outer = QVBoxLayout(w)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(12)

        outer.addWidget(self._section_header("Application Updates", "↻", first=True))
        intro = self._help_label(
            "Install the latest Alyssa release. Your settings and personal data stay "
            "unchanged while application files are replaced with the latest versions."
        )
        intro.setVisible(True)
        outer.addWidget(intro)

        self.current_version_label = self._help_label(
            f"Current Version: {updater.current_version(_BASE_DIR)}"
        )
        self.current_version_label.setVisible(True)
        outer.addWidget(self.current_version_label)

        self.check_update_btn = QPushButton("Check Update")
        self.check_update_btn.setObjectName("primaryButton")
        self.check_update_btn.setToolTip("Check GitHub for a newer release.")
        self.check_update_btn.clicked.connect(self._on_check_update)

        self.restart_alyssa_btn = QPushButton("Restart Alyssa")
        self.restart_alyssa_btn.setToolTip("Close and reopen Alyssa.")
        self.restart_alyssa_btn.clicked.connect(self._on_restart_alyssa)

        button_row = QHBoxLayout()
        button_row.addWidget(self.check_update_btn)
        button_row.addWidget(self.restart_alyssa_btn)
        button_row.addStretch(1)
        outer.addLayout(button_row)

        self.update_status_label = self._help_label("")
        outer.addWidget(self.update_status_label)
        outer.addStretch(1)
        return w

    def _on_check_update(self):
        self._flush_pending_applies()
        self.check_update_btn.setEnabled(False)
        self.update_status_label.setStyleSheet(
            f"color: {self._t()['subtext']}; font-size: 11px;"
        )
        self.update_status_label.setText("Checking GitHub for the latest release…")

        def _worker():
            try:
                self._update_checked_signal.emit(True, updater.check_latest(_BASE_DIR))
            except Exception as error:
                self._update_checked_signal.emit(False, str(error))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_restart_alyssa(self):
        self._flush_pending_applies()
        self.update_status_label.setText("Restarting Alyssa…")
        try:
            actions.relaunch_alyssa()
        except OSError as error:
            self.update_status_label.setText(f"Restart failed: {error}")
            QMessageBox.warning(self, "Restart Failed", str(error))

    def _on_update_checked(self, ok: bool, detail):
        self.check_update_btn.setEnabled(True)
        if not ok:
            self.update_status_label.setStyleSheet(
                f"color: {self._t()['danger']}; font-size: 11px;"
            )
            self.update_status_label.setText(f"Update failed: {detail}")
            QMessageBox.warning(self, "Update Failed", detail)
            return

        current = detail["current_version"]
        latest = detail["latest_version"]
        self.current_version_label.setText(f"Current Version: {current} | Latest Version: {latest}")
        self.update_status_label.setStyleSheet(
            f"color: {self._t()['success']}; font-size: 11px;"
        )
        if not detail["update_available"]:
            self.update_status_label.setText(
                f"You are on the latest version ({current}). No update needed."
            )
            return

        self.update_status_label.setText(
            f"Update available: {current} → {latest}. Review the changes to continue."
        )
        prompt = QMessageBox(self)
        prompt.setIcon(QMessageBox.Information)
        prompt.setWindowTitle(f"Alyssa {latest} Update")
        prompt.setText(f"Current Version: {current} | Latest Version: {latest}")
        prompt.setInformativeText("Changes in this release:\n\n" + detail["notes"])
        prompt.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        prompt.setDefaultButton(QMessageBox.No)
        prompt.button(QMessageBox.Yes).setText("Download and Install")
        if prompt.exec() != QMessageBox.Yes:
            self.update_status_label.setText("Update cancelled. No files were changed.")
            return

        self.check_update_btn.setEnabled(False)
        self.update_status_label.setText(f"Downloading and installing {latest}…")

        def _worker():
            try:
                self._update_installed_signal.emit(True, updater.install_release(_BASE_DIR, detail))
            except Exception as error:
                self._update_installed_signal.emit(False, str(error))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_update_installed(self, ok: bool, detail: str):
        self.check_update_btn.setEnabled(True)
        if not ok:
            self.update_status_label.setStyleSheet(
                f"color: {self._t()['danger']}; font-size: 11px;"
            )
            self.update_status_label.setText(f"Update failed: {detail}")
            QMessageBox.warning(self, "Update Failed", detail)
            return

        self.current_version_label.setText(f"Current Version: {detail} | Latest Version: {detail}")
        self.update_status_label.setStyleSheet(
            f"color: {self._t()['success']}; font-size: 11px;"
        )
        message = f"Alyssa {detail} was installed. Restart Alyssa to use the update."
        self.update_status_label.setText(message)
        QMessageBox.information(self, "Update Installed", message)

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
            "MICROPHONE_DEVICE": self.audio_input_combo.currentData() or "default",
            "AUDIO_OUTPUT_DEVICE": self.audio_output_combo.currentData() or "default",
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

    def _gather_secret_updates(self, values=None) -> dict:
        """Secret fields changed since the dialog opened (or since the
        last apply). These never get patched into config.py as text -
        see _apply_assistant_live, which routes them to
        credential_store.set_secret() instead."""
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
            key: values[key] for key in originals
            if values[key] != originals[key]
        }

    def _gather_assistant_updates(self, values=None) -> dict:
        """Non-secret fields to patch into config.py as text. Secret
        fields (credential_store.SECRET_ENV_VARS) are excluded here on
        purpose - they're persisted via _gather_secret_updates() /
        credential_store.set_secret() instead, never as plaintext."""
        values = values or self._gather_assistant_values()
        return {
            key: repr(value) for key, value in values.items()
            if key not in credential_store.SECRET_ENV_VARS
        }

    def _queue_assistant_apply(self, *args):
        self._assistant_apply_timer.start()

    def _apply_edge_volume_live(self, value: int):
        # Synthesis and active playback both read this value directly; only
        # the config.py write remains debounced while the slider is dragged.
        config.EDGE_TTS_VOLUME = _percent_to_volume_str(value)
        self._queue_assistant_apply()

    def _apply_assistant_live(self):
        # Patch config.py on disk (non-secret settings only) + this
        # session's already-imported config module, so changes take
        # effect immediately without a restart. API keys/secrets never
        # touch config.py - they go to the OS keyring via
        # credential_store instead, so they aren't a plaintext file.
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

        secret_updates = self._gather_secret_updates(values)
        if secret_updates:
            failed = [key for key, value in secret_updates.items() if not credential_store.set_secret(key, value)]
            # Resync the "original" trackers (used to detect future changes
            # and to restore on Cancel) from what was actually attempted -
            # a failed set_secret leaves the keyring untouched, so its
            # tracker stays at the old value in that case.
            self._original_gemini_key = self.gemini_key_edit.text().strip() if "GEMINI_API_KEY" not in failed else self._original_gemini_key
            self._original_openai_key = self.openai_key_edit.text().strip() if "OPENAI_API_KEY" not in failed else self._original_openai_key
            self._original_anthropic_key = self.anthropic_key_edit.text().strip() if "ANTHROPIC_API_KEY" not in failed else self._original_anthropic_key
            self._original_custom_key = self.custom_key_edit.text().strip() if "CUSTOM_API_KEY" not in failed else self._original_custom_key
            self._original_elevenlabs_key = self.elevenlabs_key_edit.text().strip() if "ELEVENLABS_API_KEY" not in failed else self._original_elevenlabs_key
            self._original_spotify_client_id = self.spotify_client_id_edit.text().strip() if "SPOTIFY_CLIENT_ID" not in failed else self._original_spotify_client_id
            self._original_spotify_client_secret = self.spotify_client_secret_edit.text().strip() if "SPOTIFY_CLIENT_SECRET" not in failed else self._original_spotify_client_secret
            self._original_youtube_key = self.youtube_key_edit.text().strip() if "YOUTUBE_API_KEY" not in failed else self._original_youtube_key
            if failed:
                QMessageBox.warning(
                    self, "Settings",
                    f"Couldn't save {', '.join(failed)} to the OS credential store "
                    f"(backend: {credential_store.storage_backend_name()}). "
                    "The value is active for this session but won't persist after restart."
                )

        old_hide = config.HIDE_CONSOLE_WINDOW
        old_whisper = (config.WHISPER_MODEL_SIZE, config.WHISPER_DEVICE, config.WHISPER_COMPUTE_TYPE)
        old_spotify = (config.SPOTIFY_CLIENT_ID, config.SPOTIFY_CLIENT_SECRET)
        old_audio_output = getattr(config, "AUDIO_OUTPUT_DEVICE", "default")
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
        if old_audio_output != getattr(config, "AUDIO_OUTPUT_DEVICE", "default"):
            # pygame's mixer only opens its output device once, at
            # first use - without this, picking a new speaker here would
            # patch config.py but replies would keep playing through the
            # old device until a full restart.
            try:
                import voice
                voice.reinit_mixer()
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
