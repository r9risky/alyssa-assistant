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
    _HoverPress, _theme, build_voice_browser_style,
)
from .widgets import CompanionWindow
from .settings_tabs.assistant_tab import AssistantTabMixin
from .settings_tabs.engine_tab import EngineTabMixin
from .settings_tabs.audio_tab import AudioTabMixin
from .settings_tabs.companion_tab import CompanionTabMixin
from .settings_tabs.updates_tab import UpdatesTabMixin
from .settings_tabs.plugins_tab import PluginsTabMixin

class _StatusLabel(QLabel):
    """QLabel that hides when its text is empty (no blank layout gap)."""

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

    def removeTab(self, index: int):
        """Drop one addTab(); used by ConfigDialog._rebuild_plugin_gui_tabs."""
        item = self.navigation.takeItem(index)
        if item is not None:
            del item
        page = self.pages.widget(index)
        if page is not None:
            self.pages.removeWidget(page)
            page.deleteLater()

    def count(self) -> int:
        return self.pages.count()

    def setCurrentIndex(self, index: int):
        self.navigation.setCurrentRow(index)

    def fit_navigation(self, available_width: int):
        """Keep the rail proportional without crowding the form."""
        self.navigation.setFixedWidth(max(120, min(190, round(available_width * 0.22))))


class VoiceBrowserDialog(QDialog):
    """Search/pick Edge neural voices. First open fetches (voice.list_edge_voices caches); search filters ShortName/Locale/Gender/FriendlyName."""

    voice_picked = Signal(str)
    _voices_loaded_signal = Signal(bool, str, list)

    def _t(self) -> dict:
        """Theme dict for the current light/dark mode."""
        return _theme(self._dark)

    def __init__(self, parent, dark: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Browse Edge TTS voices")
        self.resize(560, 520)
        self._all_voices = []
        self._dark = dark
        self.setStyleSheet(build_voice_browser_style(dark))

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


class ConfigDialog(QDialog, AssistantTabMixin, EngineTabMixin, AudioTabMixin,
                   CompanionTabMixin, UpdatesTabMixin, PluginsTabMixin):
    # Background checks emit here so Qt queues the result onto the GUI thread.
    _key_verified_signal = Signal(str, bool, str, list)
    # Custom OpenAI-compatible: fetching models is the verify step (no cheaper auth probe).
    _custom_models_fetched_signal = Signal(bool, str, list)
    # Spotify Client ID + Secret verified together (token request needs both).
    _spotify_verified_signal = Signal(bool, str)
    _update_checked_signal = Signal(bool, object)
    _CREDENTIAL_EDIT_SPECS = (
        ("gemini_key_edit", "GEMINI_API_KEY", "_original_gemini_key", "gemini_key_status_label"),
        ("openai_key_edit", "OPENAI_API_KEY", "_original_openai_key", "openai_key_status_label"),
        ("anthropic_key_edit", "ANTHROPIC_API_KEY", "_original_anthropic_key", "anthropic_key_status_label"),
        ("custom_key_edit", "CUSTOM_API_KEY", "_original_custom_key", "custom_models_status_label"),
        ("elevenlabs_key_edit", "ELEVENLABS_API_KEY", "_original_elevenlabs_key", "elevenlabs_key_status_label"),
        ("groq_key_edit", "GROQ_API_KEY", "_original_groq_key", "groq_key_status_label"),
        ("spotify_client_id_edit", "SPOTIFY_CLIENT_ID", "_original_spotify_client_id", "spotify_status_label"),
        ("spotify_client_secret_edit", "SPOTIFY_CLIENT_SECRET", "_original_spotify_client_secret", "spotify_status_label"),
        ("youtube_key_edit", "YOUTUBE_API_KEY", "_original_youtube_key", "youtube_status_label"),
    )

    _update_installed_signal = Signal(bool, str)

    def _t(self) -> dict:
        """Theme dict for the companion's color_theme / dark_mode."""
        return _theme(self.companion.settings)

    def __init__(self, companion: CompanionWindow, focus_gemini: bool = False):
        super().__init__(None, Qt.Window)
        # NonModal + .show() (not exec()): no parent would make exec() ApplicationModal and freeze the companion.
        self.setWindowModality(Qt.NonModal)
        # WA_DeleteOnClose: reopen builds fresh from current settings/config.py.
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.companion = companion
        self.setWindowTitle(f"{config.ASSISTANT_NAME} - Settings")
        # Size to the current screen; tabs scroll internally (see _wrap_in_scroll_area).
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
        self.setStyleSheet(build_voice_browser_style(companion.settings))
        self._responsive_forms = []
        self._compact_layout = None
        self._credential_commit_names = frozenset()

        # Live-apply non-secrets. Credentials skip debounce: commit only on Apply, successful Verify, or editingFinished.
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
        # Plugins tab owns its splitter height; do not wrap in a QScrollArea.
        self.tabs.addTab(self._build_plugins_tab(), "Plugins")
        self.tabs.addTab(self._wrap_in_scroll_area(self._build_updates_tab()), "Updates")
        # Plugin GUI tabs last (GUI_PANEL_TITLE + build_gui_panel) so built-ins stay first.
        self._plugin_gui_tab_count = 0
        self._add_plugin_gui_tabs()
        self._configure_credential_commit_behavior()


        # QFormLayout won't grow QComboBox; force Expanding so dropdowns match QLineEdit width.
        for combo in self.findChildren(QComboBox):
            combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        close_btn = QPushButton("Close")
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.setObjectName("primaryButton")
        self.apply_btn.clicked.connect(self._apply_all_pending)

        close_btn.setObjectName("primaryButton")
        close_btn.clicked.connect(self._on_close)
        close_btn._press_fx = _HoverPress(close_btn)
        # Flush pending applies on Close / window X / Esc.
        self.finished.connect(self._flush_pending_applies)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(self.apply_btn)
        btn_row.addWidget(close_btn)
        btn_row.setContentsMargins(0, 6, 0, 0)

        content = QWidget()
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

        # Snapshot form values; don't re-apply live companion geometry (drag/resize while Settings is open).
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
        """Section label; Qt has no text-transform, so uppercase here."""
        label = QLabel(f"{icon}  {text.upper()}" if icon else text.upper())
        label.setObjectName("sectionHeaderFirst" if first else "sectionHeader")
        return label

    # -- Assistant tab helpers ----------------------------------------------
    def _wrap_in_scroll_area(self, content: QWidget) -> QScrollArea:
        """Scroll wrapper so a long tab doesn't force the dialog taller than the screen."""
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

    def _add_plugin_gui_tabs(self):
        """Append one tab per plugin_loader.get_gui_panels() after built-ins.

        build_gui_panel() must return a QWidget; exceptions become an inline error tab.
        wrap in _wrap_in_scroll_area unless panel.scrollable is False.
        """
        added = 0
        for panel in plugin_loader.get_gui_panels():
            try:
                widget = panel["build"](self)
                if not isinstance(widget, QWidget):
                    raise TypeError(
                        f"build_gui_panel() must return a QWidget, got "
                        f"{type(widget).__name__!r}"
                    )
            except Exception as e:
                widget = self._build_plugin_panel_error_tab(panel["name"], e)
            else:
                if panel.get("scrollable", True):
                    widget = self._wrap_in_scroll_area(widget)
            self.tabs.addTab(widget, panel["title"])
            added += 1
        self._plugin_gui_tab_count = added

    def _build_plugin_panel_error_tab(self, plugin_filename: str, error: Exception) -> QWidget:
        """Error stand-in when build_gui_panel() raises; shows plugin filename + exception."""
        w = QWidget()
        w.setObjectName("tabPage")
        layout = QVBoxLayout(w)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)
        label = self._help_label(
            f"⚠ '{plugin_filename}' couldn't build its Settings tab:\n{error}"
        )
        label.setVisible(True)
        label.setWordWrap(True)
        label.setStyleSheet(f"color: {self._t()['warning']}; font-size: 11px;")
        layout.addWidget(label)
        layout.addStretch(1)
        return w

    def _rebuild_plugin_gui_tabs(self):
        """Drop then re-add plugin GUI tabs after reload_plugins(); keep current row if still valid."""
        current_row = self.tabs.navigation.currentRow()
        built_in_count = self.tabs.count() - self._plugin_gui_tab_count
        for _ in range(self._plugin_gui_tab_count):
            self.tabs.removeTab(built_in_count)
        self._add_plugin_gui_tabs()
        if 0 <= current_row < self.tabs.count():
            self.tabs.setCurrentIndex(current_row)

    def _add_row(self, form, label, field):
        """addRow and return (label, field) so the pair can be shown/hidden together."""
        label_widget = QLabel(label) if isinstance(label, str) else label
        form.addRow(label_widget, field)
        return (label_widget, field)

    def _stack_under(self, primary, *extras) -> QWidget:
        """Stack extras under primary in one column (not full-row addRow('', ...))."""
        wrapper = QWidget()
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
        """Hidden until it has text (empty status must not leave a blank line)."""
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
        """Show/hide (label, field) via QFormLayout.setRowVisible so hidden rows collapse spacing."""
        for label_widget, field_widget in rows:
            form.setRowVisible(field_widget, visible)

    def _build_api_key_row(
        self, form, label_text, initial_value, status, placeholder,
        provider=None, verify=None, checking="", verify_slot=None, extras=(),
    ):
        """`[key] [Show] [Verify]` row; extras stacked under the field. Returns (key_edit, row_label, rows)."""
        key_edit = QLineEdit(initial_value)
        key_edit.setEchoMode(QLineEdit.Password)
        key_edit.setPlaceholderText(placeholder)
        key_edit.textChanged.connect(
            lambda *args: self._reset_key_status(key_edit, status)
        )

        toggle_btn = QPushButton("Show")
        toggle_btn.setObjectName("compactButton")
        toggle_btn.setFixedWidth(58)
        toggle_btn.setCheckable(True)
        toggle_btn.toggled.connect(
            lambda show: self._toggle_key_visibility(key_edit, show)
        )

        verify_btn = QPushButton("Verify")
        verify_btn.setObjectName("compactButton")
        verify_btn.setFixedWidth(58)
        if verify_slot is None:
            verify_slot = lambda: self._start_key_verification(
                provider, key_edit, status, verify, checking
            )
        verify_btn.clicked.connect(verify_slot)

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
        """Editable model dropdown; starts with current value; user can type a name not in the fetched list."""
        combo = QComboBox()
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.NoInsert)
        if current_value:
            combo.addItem(current_value)
        combo.setCurrentText(current_value)
        combo.currentTextChanged.connect(self._queue_assistant_apply)
        return combo

    def _populate_model_combo(self, combo: QComboBox, models: list):
        """Refill combo from a fetched list; keep current text even if missing from the new items."""
        current = combo.currentText().strip()
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(models)
        if current and combo.findText(current) < 0:
            combo.insertItem(0, current)
        combo.setCurrentText(current or (models[0] if models else ""))
        combo.blockSignals(False)

    def _poll_engine_status_after_change(self):
        # After a Whisper reload, poll the status label for a few seconds.
        self._engine_status_poll_ticks = 8
        self._engine_status_poll_timer.start()

    def _apply_theme_stylesheet(self):
        self.setStyleSheet("")
        self.setStyleSheet(build_voice_browser_style(self.companion.settings))


    def _configure_credential_commit_behavior(self):
        self._credential_bindings = {}
        for edit_attr, name, original_attr, status_attr in self._CREDENTIAL_EDIT_SPECS:
            edit = getattr(self, edit_attr)
            status = getattr(self, status_attr)
            self._credential_bindings[edit] = (name, original_attr, status)
            if edit_attr in {"custom_key_edit", "spotify_client_id_edit"}:
                try:
                    edit.textChanged.disconnect(self._queue_assistant_apply)
                except (RuntimeError, TypeError):
                    pass
            edit.editingFinished.connect(
                lambda edit=edit: self._commit_credential_edits({edit})
            )

        self.custom_key_edit.textChanged.connect(
            lambda *args: self._mark_credential_pending(
                self.custom_models_status_label
            )
        )
        self.gemini_setup_hint.setText(
            "⬆ Paste your key above, then Apply, Verify, or leave the field."
        )
        for label in self.findChildren(QLabel):
            if label.text().startswith("Keys apply immediately"):
                label.setText(
                    "Credential changes save on Apply, successful Verify, or "
                    "when you leave the field. Stored in the OS credential store."
                )

    def _mark_credential_pending(self, status):
        status.setStyleSheet(
            f"font-size: 11px; color: {self._t()['warning']};"
        )
        status.setText("Unsaved credential change.")

    def _reset_key_status(self, edit, status):
        AssistantTabMixin._reset_key_status(self, edit, status)
        self._mark_credential_pending(status)

    def _gather_assistant_values(self) -> dict:
        values = AssistantTabMixin._gather_assistant_values(self)
        allowed = getattr(self, "_credential_commit_names", frozenset())
        for _edit_attr, name, original_attr, _status_attr in self._CREDENTIAL_EDIT_SPECS:
            if name not in allowed:
                values[name] = getattr(self, original_attr)
        return values

    def _commit_credential_names(self, names):
        previous = self._credential_commit_names
        self._credential_commit_names = frozenset(names)
        if self._assistant_apply_timer.isActive():
            self._assistant_apply_timer.stop()
        try:
            self._apply_assistant_live()
        finally:
            self._credential_commit_names = previous
        return {
            name
            for edit, (name, original_attr, _status) in self._credential_bindings.items()
            if name in names and edit.text().strip() == getattr(self, original_attr)
        }

    def _commit_credential_edits(self, edits):
        names = {self._credential_bindings[edit][0] for edit in edits}
        saved = self._commit_credential_names(names)
        for edit in edits:
            name, _original_attr, status = self._credential_bindings[edit]
            if name in saved:
                status.setStyleSheet(
                    f"font-size: 11px; color: {self._t()['success']};"
                )
                status.setText("Saved.")
            else:
                self._mark_credential_pending(status)

    def _apply_all_pending(self, *args):
        pending = {
            name
            for _edit, (name, _original_attr, status) in self._credential_bindings.items()
            if status.text().startswith("Unsaved")
        }
        saved = self._commit_credential_names(
            {spec[1] for spec in self._CREDENTIAL_EDIT_SPECS}
        )
        for _edit, (name, _original_attr, status) in self._credential_bindings.items():
            if name in pending:
                if name in saved:
                    status.setStyleSheet(
                        f"font-size: 11px; color: {self._t()['success']};"
                    )
                    status.setText("Saved.")
                else:
                    self._mark_credential_pending(status)

    def _on_key_verification_result(self, provider, valid, message, items):
        AssistantTabMixin._on_key_verification_result(
            self, provider, valid, message, items
        )
        edit, status = {
            "gemini": (self.gemini_key_edit, self.gemini_key_status_label),
            "openai": (self.openai_key_edit, self.openai_key_status_label),
            "anthropic": (self.anthropic_key_edit, self.anthropic_key_status_label),
            "elevenlabs": (self.elevenlabs_key_edit, self.elevenlabs_key_status_label),
            "youtube": (self.youtube_key_edit, self.youtube_status_label),
        }[provider]
        if valid and edit.text().strip() == getattr(edit, "_verified_value", None):
            verified_text = status.text()
            saved = self._commit_credential_names(
                {self._credential_bindings[edit][0]}
            )
            if self._credential_bindings[edit][0] in saved:
                status.setText(f"{verified_text} Saved.")

    def _on_custom_models_fetch_result(self, ok: bool, message: str, models: list):
        AssistantTabMixin._on_custom_models_fetch_result(self, ok, message, models)
        if ok and (
            self.custom_base_url_edit.text().strip(),
            self.custom_key_edit.text().strip(),
        ) == self._last_custom_fetch_checked:
            verified_text = self.custom_models_status_label.text()
            saved = self._commit_credential_names({"CUSTOM_API_KEY"})
            if "CUSTOM_API_KEY" in saved:
                self.custom_models_status_label.setText(f"{verified_text} Saved.")

    def _on_spotify_verify_result(self, valid: bool, message: str):
        AssistantTabMixin._on_spotify_verify_result(self, valid, message)
        current = (
            self.spotify_client_id_edit.text().strip(),
            self.spotify_client_secret_edit.text().strip(),
        )
        if valid and current == self._last_verified_spotify_checked:
            verified_text = self.spotify_status_label.text()
            names = {"SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET"}
            if names <= self._commit_credential_names(names):
                self.spotify_status_label.setText(f"{verified_text} Saved.")

    def _queue_assistant_apply(self, *args):
        self._assistant_apply_timer.start()

    def _flush_pending_applies(self, *args):
        # Flush pending debounce immediately on any close path (Close / X / Esc).
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
