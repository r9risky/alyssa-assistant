import os
import threading

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
)

from brain.common import api_origin
import config
import credential_store
import memory
from ..credential_checks import (
    _extract_elevenlabs_voice_id,
    _fetch_custom_openai_models,
    _is_http_url,
    _patch_config_line,
    _percent_to_volume_str,
    _verify_anthropic_key,
    _verify_elevenlabs_key,
    _verify_gemini_key,
    _verify_groq_key,
    _verify_openai_key,
    _verify_spotify_credentials,
    _verify_youtube_key,
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
        form = getattr(self, "_tts_form", self._assistant_form)
        self._set_rows_visible(form, self._edge_voice_rows, provider == "edge")
        self._set_rows_visible(form, self._elevenlabs_rows, provider == "elevenlabs")

    # -- Assistant tab -----------------------------------------------------

    def _build_assistant_tab(self) -> QWidget:
        w = QWidget()
        w.setObjectName("tabPage")
        outer = QVBoxLayout(w)
        outer.setContentsMargins(18, 16, 18, 18)
        outer.setSpacing(14)

        outer.addWidget(
            self._page_header(
                "Assistant & AI Brain",
                "Configure Alyssa's identity, language model provider, voice synthesis, and autonomy presets.",
                "AI CORE",
            )
        )

        # -- Card 1: Identity & Brain --
        brain_card, form = self._create_card(
            "Identity & Language Model",
            "Choose who Alyssa is and which local or cloud LLM powers her reasoning.",
            "🧠",
        )
        self._assistant_form = form  # kept for _update_provider_rows_visibility and responsive tests

        self.name_edit = QLineEdit(config.ASSISTANT_NAME)
        self.name_edit.textChanged.connect(self._queue_assistant_apply)
        self._add_row(form, "Assistant name:", self.name_edit)

        self.provider_combo = QComboBox()
        self.provider_combo.addItems(["ollama", "gemini", "openai", "anthropic", "custom_openai"])
        self.provider_combo.setCurrentText(config.LLM_PROVIDER)
        self.provider_combo.currentTextChanged.connect(self._queue_assistant_apply)
        self.provider_combo.currentTextChanged.connect(self._update_provider_rows_visibility)
        self._add_row(form, "LLM provider:", self.provider_combo)

        # -- Ollama (local) --
        self.ollama_model_edit = QLineEdit(config.OLLAMA_MODEL)
        self.ollama_model_edit.textChanged.connect(self._queue_assistant_apply)
        self._ollama_model_label = QLabel("Ollama model:")
        self._add_row(form, self._ollama_model_label, self.ollama_model_edit)

        # -- Gemini --
        self._original_gemini_key = config.GEMINI_API_KEY
        self.gemini_key_status_label = self._help_label("")
        get_key_link = self._link_label(
            "https://aistudio.google.com/apikey", "Don't have one?", "Get a free Gemini API key"
        )
        self.gemini_setup_hint = QLabel("⬆ Paste your key above; it applies immediately.")
        self.gemini_setup_hint.setStyleSheet(f"color: {self._t()['warning']}; font-weight: 600; font-size: 11.5px;")
        self.gemini_setup_hint.setVisible(False)
        self.gemini_key_edit, self.gemini_key_row_label, self._gemini_key_rows = self._build_api_key_row(
            form, "Gemini API key:", config.GEMINI_API_KEY,
            self.gemini_key_status_label,
            "Paste your Gemini API key here",
            provider="gemini", verify=_verify_gemini_key, checking="Checking with Gemini…",
            extras=(self.gemini_key_status_label, get_key_link, self.gemini_setup_hint),
        )
        self.gemini_model_edit = self._build_model_combo(config.GEMINI_MODEL, provider="gemini")
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
        self.openai_model_edit = self._build_model_combo(getattr(config, "OPENAI_MODEL", "gpt-5-mini"), provider="openai")
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
        self.anthropic_model_edit = self._build_model_combo(getattr(config, "ANTHROPIC_MODEL", "claude-sonnet-5"), provider="anthropic")
        self._anthropic_key_rows.append(self._add_row(form, "Claude model:", self.anthropic_model_edit))

        # -- Custom OpenAI-compatible provider --
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
        self._custom_key_toggle.setFixedWidth(60)
        self._custom_key_toggle.setCheckable(True)
        self._custom_key_toggle.toggled.connect(
            lambda show: self.custom_key_edit.setEchoMode(QLineEdit.Normal if show else QLineEdit.Password)
        )
        custom_key_row = QHBoxLayout()
        custom_key_row.setSpacing(6)
        custom_key_row.addWidget(self.custom_key_edit, 1)
        custom_key_row.addWidget(self._custom_key_toggle)
        custom_key_wrap = self._stack_under(custom_key_row)
        self._custom_rows.append(self._add_row(form, "Custom API key:", custom_key_wrap))

        self.custom_model_edit = self._build_model_combo(getattr(config, "CUSTOM_MODEL", ""), provider="custom")
        self.custom_models_status_label = self._help_label("")
        self._custom_fetch_models_btn = QPushButton("Fetch models")
        self._custom_fetch_models_btn.setObjectName("compactButton")
        self._custom_fetch_models_btn.clicked.connect(self._start_custom_models_fetch)
        custom_model_row = QHBoxLayout()
        custom_model_row.setSpacing(6)
        custom_model_row.addWidget(self.custom_model_edit, 1)
        custom_model_row.addWidget(self._custom_fetch_models_btn)
        custom_model_field = self._stack_under(custom_model_row, self.custom_models_status_label)
        self._custom_rows.append(self._add_row(form, "Custom model:", custom_model_field))

        custom_note = self._help_label(
            "Works with OpenAI-compatible providers (Groq, OpenRouter, Together, LM Studio, vLLM)."
        )
        custom_note.setStyleSheet(f"color: {self._t()['subtext']}; font-size: 11.5px;")
        self._custom_rows.append(self._add_row(form, "", custom_note))

        note = self._help_label(
            "Keys apply immediately and are stored as plain text in config.py."
        )
        note.setWordWrap(True)
        note.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        note.setStyleSheet(f"color: {self._t()['subtext']}; font-size: 11.5px; padding-top: 4px; padding-bottom: 4px;")
        self._add_row(form, "", note)
        outer.addWidget(brain_card)

        # -- Card 2: Voice Synthesis (TTS) --
        tts_card, tts_form = self._create_card(
            "Voice Synthesis (TTS)",
            "Select Alyssa's spoken voice engine — free Microsoft Edge Neural TTS or ElevenLabs cloned voices.",
            "🔊",
        )
        self._tts_form = tts_form

        self.tts_provider_combo = QComboBox()
        self.tts_provider_combo.addItems(["edge", "elevenlabs"])
        self.tts_provider_combo.setCurrentText(getattr(config, "TTS_PROVIDER", "edge"))
        self.tts_provider_combo.currentTextChanged.connect(self._queue_assistant_apply)
        self.tts_provider_combo.currentTextChanged.connect(self._update_tts_provider_rows_visibility)
        self._add_row(tts_form, "TTS provider:", self.tts_provider_combo)

        self._edge_voice_rows = []
        self.voice_combo = QComboBox()
        self.voice_combo.setEditable(True)
        self.voice_combo.addItem(config.EDGE_TTS_VOICE)
        self.voice_combo.setCurrentText(config.EDGE_TTS_VOICE)
        self.voice_combo.currentTextChanged.connect(self._queue_assistant_apply)
        self.browse_edge_voices_btn = QPushButton("Browse all voices…")
        self.browse_edge_voices_btn.setObjectName("compactButton")
        self.browse_edge_voices_btn.clicked.connect(self._open_edge_voice_browser)
        edge_voice_row = QHBoxLayout()
        edge_voice_row.setSpacing(6)
        edge_voice_row.addWidget(self.voice_combo, 1)
        edge_voice_row.addWidget(self.browse_edge_voices_btn)
        edge_note = self._help_label("Search Microsoft's neural voice catalog by language, accent, or gender.")
        edge_note.setStyleSheet(f"color: {self._t()['subtext']}; font-size: 11.5px;")
        edge_voice_field = self._stack_under(edge_voice_row, edge_note)
        self._edge_voice_rows.append(self._add_row(tts_form, "Edge voice:", edge_voice_field))

        self._elevenlabs_rows = []
        self._original_elevenlabs_key = getattr(config, "ELEVENLABS_API_KEY", "")
        self.elevenlabs_key_status_label = self._help_label("")
        elevenlabs_key_link = self._link_label(
            "https://elevenlabs.io/app/settings/api-keys", "Don't have one?", "Get an ElevenLabs API key"
        )
        self.elevenlabs_key_edit, self.elevenlabs_key_row_label, self._elevenlabs_key_rows = self._build_api_key_row(
            tts_form, "ElevenLabs API key:", self._original_elevenlabs_key,
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
        elevenlabs_note = self._help_label("Verify your API key to load your voices, or paste a voice ID.")
        elevenlabs_note.setStyleSheet(f"color: {self._t()['subtext']}; font-size: 11.5px;")
        el_voice_field = self._stack_under(self.elevenlabs_voice_combo, elevenlabs_note)
        self._elevenlabs_rows.append(self._add_row(tts_form, "ElevenLabs voice:", el_voice_field))

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
        self._elevenlabs_rows.append(self._add_row(tts_form, "ElevenLabs model:", self.elevenlabs_model_combo))

        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(-50, 50)
        self.volume_slider.setValue(
            _volume_str_to_percent(getattr(config, "EDGE_TTS_VOLUME", "+0%"))
        )
        self.volume_value_label = QLabel()
        self.volume_value_label.setObjectName("sliderValueBadge")
        self.volume_value_label.setMinimumWidth(48)
        self.volume_value_label.setAlignment(Qt.AlignCenter)
        self.volume_slider.valueChanged.connect(
            lambda v: self.volume_value_label.setText(_percent_to_volume_str(v))
        )
        self.volume_value_label.setText(_percent_to_volume_str(self.volume_slider.value()))
        self.volume_slider.valueChanged.connect(self._apply_edge_volume_live)
        volume_wrap = QWidget()
        volume_row = QHBoxLayout(volume_wrap)
        volume_row.setContentsMargins(0, 0, 0, 0)
        volume_row.setSpacing(10)
        volume_row.addWidget(self.volume_slider, 1)
        volume_row.addWidget(self.volume_value_label)
        self._edge_voice_rows.append(self._add_row(tts_form, "Speech volume:", volume_wrap))

        self.speak_check = QCheckBox("Speak replies out loud")
        self.speak_check.setChecked(bool(config.SPEAK_RESPONSES))
        self.speak_check.toggled.connect(self._queue_assistant_apply)
        self._add_row(tts_form, "Voice output:", self.speak_check)
        outer.addWidget(tts_card)

        # -- Card 3: Behavior, Autonomy & Safety --
        behavior_card, behavior_form = self._create_card(
            "Behavior, Autonomy & Safety",
            "Control tool confirmation prompts, response brevity, quiet mode, and console visibility.",
            "🛡️",
        )
        self.security_preset_combo = QComboBox()
        self.security_preset_combo.addItem("Lockdown (Ask for every tool)", "lockdown")
        self.security_preset_combo.addItem("Guarded (Allow read-only, ask for changes)", "guarded")
        self.security_preset_combo.addItem("Trusted (Allow standard tools, ask for shell/files)", "trusted")
        self.security_preset_combo.addItem("Blackwall (Autonomous — no prompts)", "blackwall")
        current_preset = str(getattr(config, "SECURITY_PRESET", "trusted") or "trusted").strip().lower()
        preset_idx = self.security_preset_combo.findData(current_preset)
        self.security_preset_combo.setCurrentIndex(preset_idx if preset_idx >= 0 else 2)
        self.security_preset_combo.currentIndexChanged.connect(self._queue_assistant_apply)
        self._add_row(behavior_form, "Security preset:", self.security_preset_combo)

        self.confirm_check = QCheckBox("Ask for confirmation before every action")
        self.confirm_check.setChecked(bool(config.CONFIRM_BEFORE_ACTIONS))
        self.confirm_check.toggled.connect(self._queue_assistant_apply)
        self._add_row(behavior_form, "Action prompts:", self.confirm_check)

        self.caveman_combo = QComboBox()
        self.caveman_combo.addItem("Off (Full natural personality)", None)
        self.caveman_combo.addItem("Lite (Concise replies)", "lite")
        self.caveman_combo.addItem("Full (Terse caveman style)", "full")
        self.caveman_combo.addItem("Ultra (Minimal words)", "ultra")
        current_caveman = getattr(config, "CAVEMAN_MODE", None)
        idx = self.caveman_combo.findData(current_caveman)
        self.caveman_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.caveman_combo.currentIndexChanged.connect(self._queue_assistant_apply)
        self._add_row(behavior_form, "Caveman brevity:", self.caveman_combo)

        self.quiet_mode_check = QCheckBox("Quiet mode (pause proactive screen lookout & check-ins)")
        self.quiet_mode_check.setChecked(bool(getattr(config, "QUIET_MODE", False)))
        self.quiet_mode_check.toggled.connect(self._queue_assistant_apply)
        self._add_row(behavior_form, "Quiet mode:", self.quiet_mode_check)

        self.show_console_check = QCheckBox("Show command prompt window")
        self.show_console_check.setChecked(not bool(getattr(config, "HIDE_CONSOLE_WINDOW", False)))
        self.show_console_check.toggled.connect(self._on_show_console_toggled)
        self._add_row(behavior_form, "Developer console:", self.show_console_check)
        outer.addWidget(behavior_card)

        # -- Card 4: Proactive Monitoring --
        watch_card, watch_form = self._create_card(
            "Proactive System Monitoring",
            "Background health checks that alert you before disk space runs low.",
            "🔔",
        )
        self.system_watch_enabled_check = QCheckBox(
            "Warn me when the system drive is almost full"
        )
        self.system_watch_enabled_check.setChecked(
            bool(getattr(config, "SYSTEM_WATCH_ENABLED", False))
        )
        self.system_watch_enabled_check.toggled.connect(self._queue_assistant_apply)
        self._add_row(watch_form, "Disk space alert:", self.system_watch_enabled_check)

        self.system_watch_interval_spin = QSpinBox()
        self.system_watch_interval_spin.setRange(10, 86400)
        self.system_watch_interval_spin.setSuffix(" sec")
        self.system_watch_interval_spin.setValue(
            int(getattr(config, "SYSTEM_WATCH_INTERVAL_SECONDS", 300))
        )
        self.system_watch_interval_spin.valueChanged.connect(self._queue_assistant_apply)
        self._add_row(watch_form, "Check every:", self.system_watch_interval_spin)
        outer.addWidget(watch_card)
        outer.addStretch(1)

        self._update_provider_rows_visibility()
        self._update_tts_provider_rows_visibility()
        return w

    # -- Memory, Apps & Shortcuts Tab --------------------------------------

    def _build_integrations_tab(self) -> QWidget:
        w = QWidget()
        w.setObjectName("tabPage")
        outer = QVBoxLayout(w)
        outer.setContentsMargins(18, 16, 18, 18)
        outer.setSpacing(14)

        outer.addWidget(
            self._page_header(
                "Memory, Apps & Shortcuts",
                "Manage long-term memory, persona notes, weather location, Spotify/YouTube, Call Mode, and hotkeys.",
                "APPS & MEMORY",
            )
        )

        # -- Card 1: Memory & Persona --
        mem_card, mem_form = self._create_card(
            "Memory & Persona Notes",
            "Short-term conversation turns expire after idle; saved facts persist in memory.json & markdown notes.",
            "🧾",
        )
        self.user_nickname_edit = QLineEdit(str(getattr(config, "USER_NICKNAME", "") or ""))
        self.user_nickname_edit.setPlaceholderText("Optional nickname Alyssa calls you")
        self.user_nickname_edit.textChanged.connect(self._queue_assistant_apply)
        self._add_row(mem_form, "Your nickname:", self.user_nickname_edit)

        self.remember_chats_check = QCheckBox("Save daily chat transcripts (recall past chats)")
        self.remember_chats_check.setChecked(bool(getattr(config, "REMEMBER_CHATS_ENABLED", True)))
        self.remember_chats_check.toggled.connect(self._queue_assistant_apply)
        self._add_row(mem_form, "Chat history:", self.remember_chats_check)

        self.conversation_turns_spin = QSpinBox()
        self.conversation_turns_spin.setRange(1, 50)
        self.conversation_turns_spin.setValue(int(getattr(config, "CONVERSATION_MEMORY_TURNS", 10)))
        self.conversation_turns_spin.setToolTip("Conversation turns kept in short-term memory.")
        self.conversation_turns_spin.valueChanged.connect(self._queue_assistant_apply)
        self._add_row(mem_form, "Short-term turns:", self.conversation_turns_spin)

        self.conversation_timeout_spin = QSpinBox()
        self.conversation_timeout_spin.setRange(0, 3600)
        self.conversation_timeout_spin.setSuffix(" sec")
        self.conversation_timeout_spin.setValue(int(getattr(config, "CONVERSATION_TIMEOUT_SECONDS", 300)))
        self.conversation_timeout_spin.setToolTip("Idle seconds before memory clears. 0 disables auto-clear.")
        self.conversation_timeout_spin.valueChanged.connect(self._queue_assistant_apply)
        self._add_row(mem_form, "Auto-clear after idle:", self.conversation_timeout_spin)

        self.max_saved_memories_spin = QSpinBox()
        self.max_saved_memories_spin.setRange(1, 1000)
        self.max_saved_memories_spin.setValue(int(getattr(config, "MAX_SAVED_MEMORIES", 75)))
        self.max_saved_memories_spin.setToolTip("Maximum saved facts; oldest are removed first.")
        self.max_saved_memories_spin.valueChanged.connect(self._queue_assistant_apply)
        self._add_row(mem_form, "Saved memories (max):", self.max_saved_memories_spin)

        self.max_memory_chars_spin = QSpinBox()
        self.max_memory_chars_spin.setRange(20, 2000)
        self.max_memory_chars_spin.setSuffix(" chars")
        self.max_memory_chars_spin.setValue(int(getattr(config, "MAX_MEMORY_FACT_CHARACTERS", 400)))
        self.max_memory_chars_spin.setToolTip("Maximum characters per saved fact.")
        self.max_memory_chars_spin.valueChanged.connect(self._queue_assistant_apply)
        self._add_row(mem_form, "Max length per fact:", self.max_memory_chars_spin)

        self.max_memories_in_prompt_spin = QSpinBox()
        self.max_memories_in_prompt_spin.setRange(0, 200)
        self.max_memories_in_prompt_spin.setValue(int(getattr(config, "MAX_MEMORIES_IN_PROMPT", 20)))
        self.max_memories_in_prompt_spin.setToolTip("Relevant saved facts included per request.")
        self.max_memories_in_prompt_spin.valueChanged.connect(self._queue_assistant_apply)
        self._add_row(mem_form, "Facts recalled / turn:", self.max_memories_in_prompt_spin)

        self.personality_edit = QPlainTextEdit()
        self.personality_edit.setPlainText(memory.load_personality_notes())
        self.personality_edit.setMaximumHeight(92)
        self.save_personality_btn = QPushButton("Save Personality Notes")
        self.save_personality_btn.setObjectName("compactButton")
        self.save_personality_btn.clicked.connect(
            lambda: memory.save_personality_notes(self.personality_edit.toPlainText())
        )
        self._add_row(
            mem_form,
            "Personality (personality.md):",
            self._stack_under(self.personality_edit, self.save_personality_btn),
        )

        self.me_notes_edit = QPlainTextEdit()
        self.me_notes_edit.setPlainText(memory.load_me_notes())
        self.me_notes_edit.setMaximumHeight(92)
        self.save_me_notes_btn = QPushButton("Save About You Notes")
        self.save_me_notes_btn.setObjectName("compactButton")
        self.save_me_notes_btn.clicked.connect(
            lambda: memory.save_me_notes(self.me_notes_edit.toPlainText())
        )
        self._add_row(
            mem_form,
            "About You (me.md):",
            self._stack_under(self.me_notes_edit, self.save_me_notes_btn),
        )
        outer.addWidget(mem_card)

        # -- Card 2: Location & Weather --
        loc_card, loc_form = self._create_card(
            "Location & Weather",
            "Used for local weather forecasts and regional queries — no API key required.",
            "🌤️",
        )
        self.auto_detect_location_check = QCheckBox("Auto-detect my location from my IP address")
        self.auto_detect_location_check.setChecked(bool(getattr(config, "AUTO_DETECT_LOCATION", True)))
        self.auto_detect_location_check.setToolTip("Approximate city detection; VPNs may affect accuracy.")
        self.auto_detect_location_check.toggled.connect(self._queue_assistant_apply)
        self._add_row(loc_form, "IP auto-detection:", self.auto_detect_location_check)

        self.weather_default_location_edit = QLineEdit(getattr(config, "WEATHER_DEFAULT_LOCATION", ""))
        self.weather_default_location_edit.setPlaceholderText("e.g. Chicago, IL - leave blank to auto-detect")
        self.weather_default_location_edit.textChanged.connect(self._queue_assistant_apply)
        self._add_row(loc_form, "Default location:", self.weather_default_location_edit)

        self.weather_units_combo = QComboBox()
        self.weather_units_combo.addItem("Imperial (°F, mph)", "imperial")
        self.weather_units_combo.addItem("Metric (°C, km/h)", "metric")
        current_units = getattr(config, "WEATHER_UNITS", "imperial")
        idx = self.weather_units_combo.findData(current_units)
        self.weather_units_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.weather_units_combo.currentIndexChanged.connect(self._queue_assistant_apply)
        self._add_row(loc_form, "Weather units:", self.weather_units_combo)
        outer.addWidget(loc_card)

        # -- Card 3: Music (Spotify / YouTube Music) --
        music_card, music_form = self._create_card(
            "Music Services (Spotify & YouTube)",
            "Optional credentials so 'play [song]' launches the exact track instead of a search page.",
            "🎵",
        )
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
        id_row = QHBoxLayout()
        id_row.setSpacing(6)
        id_row.addWidget(self.spotify_client_id_edit, 1)
        id_spacer = QLabel("")
        id_spacer.setFixedWidth(60 + 64 + 6)
        id_row.addWidget(id_spacer)
        self._add_row(music_form, "Spotify Client ID:", id_row)

        self.spotify_status_label = self._help_label("")
        spotify_link = self._link_label(
            "https://developer.spotify.com/dashboard",
            "Don't have these?", "Create a free Spotify app",
        )
        self.spotify_client_secret_edit, _spotify_secret_label, _spotify_secret_rows = self._build_api_key_row(
            music_form, "Spotify Client Secret:", self._original_spotify_client_secret,
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
            music_form, "YouTube API key:", self._original_youtube_key,
            self.youtube_status_label,
            "Paste your YouTube Data API key here",
            provider="youtube", verify=_verify_youtube_key, checking="Checking with YouTube…",
            extras=(self.youtube_status_label, youtube_link),
        )
        outer.addWidget(music_card)

        # -- Card 4: Call & Meeting Mode + Card 5: Global Hotkeys (built via AudioTabMixin helpers) --
        self._build_call_and_hotkey_cards(outer)
        outer.addStretch(1)
        return w

    # -- Credential & Verification Helpers ---------------------------------

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
            status.setStyleSheet(f"font-size: 11.5px; color: {self._t()['danger']};")
            status.setText("Paste a key first.")
            return
        edit._verify_btn.setEnabled(False)
        edit._verify_btn.setText("…")
        edit.setStyleSheet("")
        status.setStyleSheet(f"font-size: 11.5px; color: {self._t()['subtext']};")
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
            "groq": (self.groq_key_edit, self.groq_key_status_label, self.groq_model_edit, "models"),
            "youtube": (self.youtube_key_edit, self.youtube_status_label, None, "items"),
        }[provider]
        edit._verify_btn.setEnabled(True)
        edit._verify_btn.setText("Verify")
        if edit.text().strip() != edit._verified_value:
            return
        color = self._t()["success" if valid else "danger"]
        edit.setStyleSheet(f"border: 2px solid {color};")
        status.setStyleSheet(f"font-size: 11.5px; color: {color};")
        if not valid:
            status.setText(f"✗ {message}")
        elif items:
            self._populate_model_combo(target, items)
            status.setText(f"✓ Valid key - {len(items)} {item_name} loaded.")
        else:
            status.setText("✓ Valid key.")

    def _confirm_custom_key_origin(self, base_url: str, key: str) -> bool:
        if not key:
            return True
        origin = api_origin(base_url)
        warned = getattr(self, "_warned_custom_key_origins", set())
        if origin in warned:
            return True
        choice = QMessageBox.warning(
            self,
            "Send API key?",
            f"This sends your custom-provider API key to {origin}. Continue?",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if choice != QMessageBox.Yes:
            return False
        warned.add(origin)
        self._warned_custom_key_origins = warned
        return True

    def _start_custom_models_fetch(self):
        base_url = self.custom_base_url_edit.text().strip()
        key = self.custom_key_edit.text().strip()
        if not base_url:
            self.custom_models_status_label.setStyleSheet(f"font-size: 11.5px; color: {self._t()['danger']};")
            self.custom_models_status_label.setText("Enter a base URL first.")
            return
        if not _is_http_url(base_url, key):
            self.custom_base_url_edit.setStyleSheet(f"border: 2px solid {self._t()['danger']};")
            self.custom_models_status_label.setStyleSheet(f"font-size: 11.5px; color: {self._t()['danger']};")
            self.custom_models_status_label.setText(
                "Use HTTPS with an API key; HTTP is allowed only for loopback."
            )
            return
        if not self._confirm_custom_key_origin(base_url, key):
            return

        self._custom_fetch_models_btn.setEnabled(False)
        self._custom_fetch_models_btn.setText("…")
        self.custom_models_status_label.setStyleSheet(f"font-size: 11.5px; color: {self._t()['subtext']};")
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
            return

        if ok and models:
            self._populate_model_combo(self.custom_model_edit, models)
            self.custom_models_status_label.setStyleSheet(f"font-size: 11.5px; color: {self._t()['success']};")
            self.custom_models_status_label.setText(f"✓ {len(models)} models loaded.")
        else:
            self.custom_models_status_label.setStyleSheet(f"font-size: 11.5px; color: {self._t()['danger']};")
            self.custom_models_status_label.setText(f"✗ {message}")

    def _open_edge_voice_browser(self):
        from ..settings_dialog import VoiceBrowserDialog

        dialog = VoiceBrowserDialog(self, dark=self.companion.settings)
        dialog.voice_picked.connect(self._on_edge_voice_picked)
        dialog.exec()

    def _on_edge_voice_picked(self, short_name: str):
        if self.voice_combo.findText(short_name) < 0:
            self.voice_combo.addItem(short_name)
        self.voice_combo.setCurrentText(short_name)

    def _start_spotify_verify(self):
        client_id = self.spotify_client_id_edit.text().strip()
        client_secret = self.spotify_client_secret_edit.text().strip()
        if not client_id or not client_secret:
            self.spotify_client_secret_edit.setStyleSheet(f"border: 2px solid {self._t()['danger']};")
            self.spotify_status_label.setStyleSheet(f"font-size: 11.5px; color: {self._t()['danger']};")
            self.spotify_status_label.setText("Fill in both the Client ID and Client Secret first.")
            return

        self.spotify_client_secret_edit._verify_btn.setEnabled(False)
        self.spotify_client_secret_edit._verify_btn.setText("…")
        self.spotify_client_secret_edit.setStyleSheet("")
        self.spotify_status_label.setStyleSheet(f"font-size: 11.5px; color: {self._t()['subtext']};")
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
            self.spotify_status_label.setStyleSheet(f"font-size: 11.5px; color: {self._t()['success']};")
            self.spotify_status_label.setText("✓ Valid credentials.")
        else:
            self.spotify_client_secret_edit.setStyleSheet(f"border: 2px solid {self._t()['danger']};")
            self.spotify_status_label.setStyleSheet(f"font-size: 11.5px; color: {self._t()['danger']};")
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

    def _gather_assistant_values(self) -> dict:
        custom_base_url = self.custom_base_url_edit.text().strip()
        if not _is_http_url(custom_base_url, self.custom_key_edit.text().strip()):
            self.custom_base_url_edit.setStyleSheet(f"border: 2px solid {self._t()['danger']};")
            self.custom_models_status_label.setStyleSheet(f"font-size: 11.5px; color: {self._t()['danger']};")
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
            "SECURITY_PRESET": self.security_preset_combo.currentData() or "trusted",
            "QUIET_MODE": self.quiet_mode_check.isChecked(),
            "HIDE_CONSOLE_WINDOW": not self.show_console_check.isChecked(),
            "SYSTEM_WATCH_ENABLED": self.system_watch_enabled_check.isChecked(),
            "SYSTEM_WATCH_INTERVAL_SECONDS": self.system_watch_interval_spin.value(),
            "WHISPER_MODEL_SIZE": self.whisper_model_combo.currentText().strip() or config.WHISPER_MODEL_SIZE,
            "WHISPER_DEVICE": self._whisper_device_display_to_value.get(self.whisper_device_combo.currentText(), "auto"),
            "WHISPER_COMPUTE_TYPE": self.whisper_compute_combo.currentText().strip() or "auto",
            "MICROPHONE_DEVICE": self.audio_input_combo.currentData() or "default",
            "AUDIO_OUTPUT_DEVICE": self.audio_output_combo.currentData() or "default",
            "CALL_MODE_ENABLED": getattr(self, "call_mode_enabled_check", None) is not None and self.call_mode_enabled_check.isChecked(),
            "CALL_APP": self.call_app_combo.currentData() if hasattr(self, "call_app_combo") else "auto",
            "CALL_VIRTUAL_MIC_DEVICE": self.call_virtual_mic_combo.currentData() if hasattr(self, "call_virtual_mic_combo") else "CABLE Input",
            "CALL_VIRTUAL_MIC_VOLUME": round(self.call_virtual_mic_volume_slider.value() / 100.0, 2) if hasattr(self, "call_virtual_mic_volume_slider") else 0.85,
            "CALL_FRIENDS_CAN_ASK": getattr(self, "call_friends_can_ask_check", None) is not None and self.call_friends_can_ask_check.isChecked(),
            "CALL_AUTO_JOIN_ENABLED": getattr(self, "call_auto_join_check", None) is not None and self.call_auto_join_check.isChecked(),
            "CALL_BARGE_IN_TOLERANCE_SECONDS": round(float(self.call_barge_in_spin.value()), 2) if hasattr(self, "call_barge_in_spin") else 1.0,
            "VAD_ENGINE": self.vad_engine_combo.currentData() or "auto",
            "SILERO_VAD_THRESHOLD": round(self.vad_threshold_slider.value() / 100.0, 2),
            "PRE_ROLL_BUFFER_MS": int(self.pre_roll_spin.value()),
            "WAKE_WORD_MODE": self.wake_word_mode_combo.currentData() or "wake_word",
            "WAKE_WORD_THRESHOLD": round(self.wake_sensitivity_slider.value() / 100.0, 2),
            "WAKE_CHIME_ENABLED": self.wake_chime_check.isChecked(),
            "WAKE_CHIME_VOLUME": round(self.wake_chime_volume_slider.value() / 100.0, 2),
            "VOICE_DSP_ENABLED": bool(getattr(self, "voice_dsp_enabled_check", None) and self.voice_dsp_enabled_check.isChecked()),
            "VOICE_PITCH_SEMITONES": round(float(self.voice_pitch_spin.value()), 2) if hasattr(self, "voice_pitch_spin") else float(getattr(config, "VOICE_PITCH_SEMITONES", 0.0)),
            "VOICE_SPEED_FACTOR": round(float(self.voice_speed_spin.value()), 2) if hasattr(self, "voice_speed_spin") else float(getattr(config, "VOICE_SPEED_FACTOR", 1.0)),
            "VOICE_ROBOT_AMOUNT": int(self.voice_robot_slider.value()) if hasattr(self, "voice_robot_slider") else int(getattr(config, "VOICE_ROBOT_AMOUNT", 0)),
            "VOICE_GLITCH_AMOUNT": int(self.voice_glitch_slider.value()) if hasattr(self, "voice_glitch_slider") else int(getattr(config, "VOICE_GLITCH_AMOUNT", 0)),
            "VOICE_LAYERS_ENABLED": bool(getattr(self, "voice_layers_check", None) and self.voice_layers_check.isChecked()),
            "TYPING_SOUND_ENABLED": bool(getattr(self, "typing_sound_check", None) and self.typing_sound_check.isChecked()),
            "HOTKEYS_ENABLED": bool(getattr(self, "hotkeys_enabled_check", None) and self.hotkeys_enabled_check.isChecked()),
            "HOTKEY_TALK": self.hotkey_talk_edit.text().strip() if hasattr(self, "hotkey_talk_edit") else str(getattr(config, "HOTKEY_TALK", "ctrl+alt+v")),
            "HOTKEY_MUTE": self.hotkey_mute_edit.text().strip() if hasattr(self, "hotkey_mute_edit") else str(getattr(config, "HOTKEY_MUTE", "ctrl+alt+n")),
            "HOTKEY_TYPE": self.hotkey_type_edit.text().strip() if hasattr(self, "hotkey_type_edit") else str(getattr(config, "HOTKEY_TYPE", "ctrl+alt+t")),
            "HOTKEY_HIDE": self.hotkey_hide_edit.text().strip() if hasattr(self, "hotkey_hide_edit") else str(getattr(config, "HOTKEY_HIDE", "ctrl+alt+h")),
            "HOTKEY_QUIET": self.hotkey_quiet_edit.text().strip() if hasattr(self, "hotkey_quiet_edit") else str(getattr(config, "HOTKEY_QUIET", "ctrl+alt+p")),
            "HOTKEY_HOP_OFF": self.hotkey_hop_off_edit.text().strip() if hasattr(self, "hotkey_hop_off_edit") else str(getattr(config, "HOTKEY_HOP_OFF", "ctrl+alt+c")),
            "USER_NICKNAME": self.user_nickname_edit.text().strip(),
            "REMEMBER_CHATS_ENABLED": self.remember_chats_check.isChecked(),
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
            "GROQ_API_KEY": self.groq_key_edit.text().strip(),
            "STT_GROQ_MODEL": self.groq_model_edit.currentText().strip() or "whisper-large-v3-turbo",
            "STT_PROVIDER": self.stt_provider_combo.currentData() or "auto",
            "SPOTIFY_CLIENT_ID": self.spotify_client_id_edit.text().strip(),
            "SPOTIFY_CLIENT_SECRET": self.spotify_client_secret_edit.text().strip(),
            "YOUTUBE_API_KEY": self.youtube_key_edit.text().strip(),
        }

    def _gather_secret_updates(self, values=None) -> dict:
        """Changed secrets since open/last apply. Never patched into config.py; see credential_store.set_secret()."""
        values = values or self._gather_assistant_values()
        originals = {
            "GEMINI_API_KEY": self._original_gemini_key,
            "OPENAI_API_KEY": self._original_openai_key,
            "ANTHROPIC_API_KEY": self._original_anthropic_key,
            "CUSTOM_API_KEY": self._original_custom_key,
            "ELEVENLABS_API_KEY": self._original_elevenlabs_key,
            "GROQ_API_KEY": self._original_groq_key,
            "SPOTIFY_CLIENT_ID": self._original_spotify_client_id,
            "SPOTIFY_CLIENT_SECRET": self._original_spotify_client_secret,
            "YOUTUBE_API_KEY": self._original_youtube_key,
        }
        return {
            key: values[key] for key in originals
            if values[key] != originals[key]
        }

    def _gather_assistant_updates(self, values=None) -> dict:
        """Non-secret fields to patch into config.py. SECRET_ENV_VARS go through _gather_secret_updates()."""
        values = values or self._gather_assistant_values()
        return {
            key: repr(value) for key, value in values.items()
            if key not in credential_store.SECRET_ENV_VARS
        }

    def _apply_edge_volume_live(self, value: int):
        # Playback reads this immediately; config.py write stays debounced.
        config.EDGE_TTS_VOLUME = _percent_to_volume_str(value)
        self._queue_assistant_apply()

    def _apply_assistant_live(self):
        from .. import settings_dialog as _settings_dialog

        _atomic_write_text = _settings_dialog._atomic_write_text
        _set_console_visible = _settings_dialog._set_console_visible
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
            self._original_gemini_key = self.gemini_key_edit.text().strip() if "GEMINI_API_KEY" not in failed else self._original_gemini_key
            self._original_openai_key = self.openai_key_edit.text().strip() if "OPENAI_API_KEY" not in failed else self._original_openai_key
            self._original_anthropic_key = self.anthropic_key_edit.text().strip() if "ANTHROPIC_API_KEY" not in failed else self._original_anthropic_key
            self._original_custom_key = self.custom_key_edit.text().strip() if "CUSTOM_API_KEY" not in failed else self._original_custom_key
            self._original_elevenlabs_key = self.elevenlabs_key_edit.text().strip() if "ELEVENLABS_API_KEY" not in failed else self._original_elevenlabs_key
            self._original_groq_key = self.groq_key_edit.text().strip() if "GROQ_API_KEY" not in failed else self._original_groq_key
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
        old_preset = getattr(config, "SECURITY_PRESET", "trusted")
        old_whisper = (config.WHISPER_MODEL_SIZE, config.WHISPER_DEVICE, config.WHISPER_COMPUTE_TYPE)
        old_system_watch = (
            config.SYSTEM_WATCH_ENABLED, config.SYSTEM_WATCH_INTERVAL_SECONDS
        )
        old_spotify = (config.SPOTIFY_CLIENT_ID, config.SPOTIFY_CLIENT_SECRET)
        old_audio_output = getattr(config, "AUDIO_OUTPUT_DEVICE", "default")
        for key, value in values.items():
            setattr(config, key, value)

        if old_preset != getattr(config, "SECURITY_PRESET", "trusted"):
            try:
                import actions
                actions.apply_security_preset(config.SECURITY_PRESET)
            except Exception:
                pass
        hotkey_mgr = getattr(self.companion, "_hotkey_manager", None)
        if hotkey_mgr is not None and hasattr(hotkey_mgr, "reload_from_config"):
            hotkey_mgr.reload_from_config()

        if old_system_watch != (
            config.SYSTEM_WATCH_ENABLED, config.SYSTEM_WATCH_INTERVAL_SECONDS
        ):
            self._reload_plugins_backend()
        if old_hide != config.HIDE_CONSOLE_WINDOW:
            _set_console_visible(not config.HIDE_CONSOLE_WINDOW)
        whisper_settings_changed = old_whisper != (
            config.WHISPER_MODEL_SIZE, config.WHISPER_DEVICE, config.WHISPER_COMPUTE_TYPE
        )
        if whisper_settings_changed:
            try:
                import transcribe
                transcribe.reload_model_async()
                if hasattr(self, "_poll_engine_status_after_change"):
                    self._poll_engine_status_after_change()
            except ImportError:
                pass
        if old_audio_output != getattr(config, "AUDIO_OUTPUT_DEVICE", "default"):
            try:
                import voice
                voice.reinit_mixer()
            except ImportError:
                pass
        if old_spotify != (config.SPOTIFY_CLIENT_ID, config.SPOTIFY_CLIENT_SECRET):
            try:
                import actions
                actions._spotify_token_cache["access_token"] = None
                actions._spotify_token_cache["expires_at"] = 0
            except ImportError:
                pass

        starter = getattr(self.companion, "start_assistant_worker", None)
        if starter is not None:
            starter()

        self.setWindowTitle(f"{config.ASSISTANT_NAME} - Settings")
