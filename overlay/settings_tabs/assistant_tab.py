import os
import threading

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QFormLayout, QHBoxLayout, QLineEdit, QComboBox,
    QCheckBox, QSlider, QSpinBox, QPushButton, QLabel, QMessageBox,
)

import config
import credential_store

from ..credential_checks import (
    _extract_elevenlabs_voice_id, _fetch_custom_openai_models,
    _is_http_url, _patch_config_line, _percent_to_volume_str,
    _verify_anthropic_key, _verify_elevenlabs_key, _verify_gemini_key,
    _verify_openai_key, _verify_spotify_credentials, _verify_youtube_key,
    _volume_str_to_percent,
)
from ..rendering import _BASE_DIR


class AssistantTabMixin:
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

    def _build_assistant_tab(self) -> QWidget:
        w = QWidget()
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
            self.gemini_key_status_label,
            "Paste your Gemini API key here",
            provider="gemini", verify=_verify_gemini_key, checking="Checking with Gemini…",
            extras=(self.gemini_key_status_label, get_key_link, self.gemini_setup_hint),
        )
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
            self.openai_key_status_label,
            "Paste your OpenAI API key here",
            provider="openai", verify=_verify_openai_key, checking="Checking with OpenAI…",
            extras=(self.openai_key_status_label, openai_key_link),
        )
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
            self.anthropic_key_status_label,
            "Paste your Anthropic API key here",
            provider="anthropic", verify=_verify_anthropic_key, checking="Checking with Anthropic…",
            extras=(self.anthropic_key_status_label, anthropic_key_link),
        )
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
        self.spotify_client_id_edit.textChanged.connect(
            lambda *args: self._reset_key_status(
                self.spotify_client_secret_edit, self.spotify_status_label
            )
        )
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
            self.spotify_status_label,
            "Paste your Spotify Client Secret here",
            verify_slot=self._start_spotify_verify,
            extras=(self.spotify_status_label, spotify_link),
        )

        self._original_youtube_key = getattr(config, "YOUTUBE_API_KEY", "")
        self.youtube_status_label = self._help_label("")
        youtube_link = self._link_label(
            "https://console.cloud.google.com/apis/library/youtube.googleapis.com",
            "Don't have one?", "Enable the YouTube Data API v3",
        )
        self.youtube_key_edit, _youtube_key_label, _youtube_key_rows = self._build_api_key_row(
            form, "YouTube API key:", self._original_youtube_key,
            self.youtube_status_label,
            "Paste your YouTube Data API key here",
            provider="youtube", verify=_verify_youtube_key, checking="Checking with YouTube…",
            extras=(self.youtube_status_label, youtube_link),
        )

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
            self.elevenlabs_key_status_label,
            "Paste your ElevenLabs API key here",
            provider="elevenlabs", verify=_verify_elevenlabs_key,
            checking="Loading voices from ElevenLabs…",
            extras=(self.elevenlabs_key_status_label, elevenlabs_key_link),
        )
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

    # -- Edge TTS "Browse all voices" - opens VoiceBrowserDialog, which
    # fetches the full catalog on its own background thread. -------------

    def _open_edge_voice_browser(self):
        from ..settings_dialog import VoiceBrowserDialog

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

    def _on_show_console_toggled(self, show: bool):
        from .. import settings_dialog as _settings_dialog

        _atomic_write_text = _settings_dialog._atomic_write_text
        _set_console_visible = _settings_dialog._set_console_visible
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

    def _apply_edge_volume_live(self, value: int):
        # Synthesis and active playback both read this value directly; only
        # the config.py write remains debounced while the slider is dragged.
        config.EDGE_TTS_VOLUME = _percent_to_volume_str(value)
        self._queue_assistant_apply()

    def _apply_assistant_live(self):
        from .. import settings_dialog as _settings_dialog

        _atomic_write_text = _settings_dialog._atomic_write_text
        _set_console_visible = _settings_dialog._set_console_visible
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

