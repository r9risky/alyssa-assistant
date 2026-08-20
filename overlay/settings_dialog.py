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


class ConfigDialog(QDialog, AssistantTabMixin, EngineTabMixin, AudioTabMixin,
                   CompanionTabMixin, UpdatesTabMixin, PluginsTabMixin):
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
        self.setStyleSheet(build_voice_browser_style(companion.settings))
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
    def _wrap_in_scroll_area(self, content: QWidget) -> QScrollArea:
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

    def _stack_under(self, primary, *extras) -> QWidget:
        """Stacks `extras` (status text, "don't have one?" links, etc.)
        directly beneath `primary` in a single column, so they line up
        under their field instead of spanning the full row width the way
        addRow("", ...) would."""
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

    def _build_api_key_row(
        self, form, label_text, initial_value, status, placeholder,
        provider=None, verify=None, checking="", verify_slot=None, extras=(),
    ):
        """Builds the common "[key field] [Show] [Verify]" row shared by
        every cloud provider's API key setting, with any status/help-link
        labels stacked directly under the field. Returns (key_edit,
        row_label, rows) where `rows` is a single-item list of (label,
        field) suitable for _set_rows_visible."""
        key_edit = QLineEdit(initial_value)
        key_edit.setEchoMode(QLineEdit.Password)
        key_edit.setPlaceholderText(placeholder)
        key_edit.textChanged.connect(self._queue_assistant_apply)
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

    def _poll_engine_status_after_change(self):
        # Called right after a Whisper setting change kicks off a
        # background reload (see _apply_assistant_live()) - checks the
        # status label every second for a few seconds to catch the reload
        # finishing (or failing) without a manual refresh.
        self._engine_status_poll_ticks = 8
        self._engine_status_poll_timer.start()

    def _apply_theme_stylesheet(self):
        self.setStyleSheet("")
        self.setStyleSheet(build_voice_browser_style(self.companion.settings))

    def _queue_assistant_apply(self, *args):
        self._assistant_apply_timer.start()

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
