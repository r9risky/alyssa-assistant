import threading

from PySide6.QtCore import Qt, QTime, QTimer
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
    QTimeEdit,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
)

from brain.common import api_origin
import config
import credential_store
import memory
import user_settings
import startup_logging
from ..credential_checks import (
    _extract_elevenlabs_voice_id,
    _fetch_custom_openai_models,
    _is_http_url,
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


class AssistantTabMixin:
    def _update_provider_rows_visibility(self, *args):
        provider = self.provider_combo.currentText()
        # The heavy-task service needs its key too, but only the main service's chat model shows.
        heavy = (
            self.strong_provider_combo.currentText()
            if (self.reasoning_tier_combo.currentData() or "fast") != "fast" else provider
        )
        form = self._assistant_form
        for name, key_rows in (
            ("gemini", self._gemini_key_rows),
            ("openai", self._openai_key_rows),
            ("anthropic", self._anthropic_key_rows),
            ("custom_openai", self._custom_rows),
        ):
            self._set_rows_visible(form, key_rows, name in (provider, heavy))
        for name, row in self._chat_model_rows.items():
            self._set_rows_visible(form, [row], name == provider)

    def _update_tts_provider_rows_visibility(self, *args):
        provider = self.tts_provider_combo.currentText()
        form = getattr(self, "_tts_form", self._assistant_form)
        self._set_rows_visible(form, self._edge_voice_rows, provider == "edge")
        self._set_rows_visible(form, self._kokoro_rows, provider == "kokoro")
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
        self._add_row(form, "AI service:", self.provider_combo)
        self._chat_model_rows = {}  # service -> its "Chat model:" row; only the main service's shows

        # -- Ollama (local) --
        self.ollama_model_edit = QLineEdit(config.OLLAMA_MODEL)
        self.ollama_model_edit.textChanged.connect(self._queue_assistant_apply)
        self._ollama_model_label = QLabel("Chat model:")
        self._chat_model_rows["ollama"] = self._add_row(form, self._ollama_model_label, self.ollama_model_edit)

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
        self._chat_model_rows["gemini"] = self._add_row(form, "Chat model:", self.gemini_model_edit)

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
        self._chat_model_rows["openai"] = self._add_row(form, "Chat model:", self.openai_model_edit)

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
        self._chat_model_rows["anthropic"] = self._add_row(form, "Chat model:", self.anthropic_model_edit)

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
        self._chat_model_rows["custom_openai"] = self._add_row(form, "Chat model:", custom_model_field)

        custom_note = self._help_label(
            "Works with OpenAI-compatible providers (Groq, OpenRouter, Together, LM Studio, vLLM)."
        )
        custom_note.setStyleSheet(f"color: {self._t()['subtext']}; font-size: 11.5px;")
        self._custom_rows.append(self._add_row(form, "", custom_note))

        # Heavy tasks: chat stays on the cheap chat model; coding/research can use a bigger one.
        self.reasoning_tier_combo = QComboBox()
        self.reasoning_tier_combo.addItem("Use the chat model too (cheapest)", "fast")
        self.reasoning_tier_combo.addItem("Auto: heavy model for coding, research and writing", "auto")
        self.reasoning_tier_combo.addItem("Heavy model for everything", "strong")
        tier_idx = self.reasoning_tier_combo.findData(str(getattr(config, "LLM_REASONING_TIER", "fast")))
        self.reasoning_tier_combo.setCurrentIndex(max(0, tier_idx))
        self.reasoning_tier_combo.currentIndexChanged.connect(self._queue_assistant_apply)
        self.reasoning_tier_combo.currentIndexChanged.connect(self._update_strong_provider_visibility)
        self.tokens_today_label = self._help_label("")
        self._add_row(form, "Heavy tasks:", self._stack_under(self.reasoning_tier_combo, self.tokens_today_label))

        self.strong_provider_combo = QComboBox()
        self.strong_provider_combo.addItems(["gemini", "anthropic", "openai", "custom_openai", "ollama"])
        self.strong_provider_combo.setCurrentText(str(getattr(config, "LLM_STRONG_PROVIDER", "anthropic")))
        self.strong_provider_combo.currentTextChanged.connect(self._queue_assistant_apply)
        self.strong_provider_combo.currentTextChanged.connect(self._update_strong_provider_visibility)
        self.strong_provider_combo.currentTextChanged.connect(self._refill_strong_model_combo)
        self._strong_provider_row = self._add_row(form, "Heavy-task service:", self.strong_provider_combo)

        self.strong_model_edit = self._build_model_combo(
            str(getattr(config, "LLM_STRONG_MODEL", "") or ""),
            provider=self._model_list_key(self.strong_provider_combo.currentText()),
        )
        self.strong_model_edit.lineEdit().setPlaceholderText("Blank = same as that service's chat model")
        self._strong_model_row = self._add_row(form, "Heavy-task model:", self.strong_model_edit)
        self.refresh_tokens_today()

        note = self._help_label(
            "Keys apply immediately and are stored in Windows Credential Manager."
        )
        note.setWordWrap(True)
        note.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        note.setStyleSheet(f"color: {self._t()['subtext']}; font-size: 11.5px; padding-top: 4px; padding-bottom: 4px;")
        self._add_row(form, "", note)
        outer.addWidget(brain_card)

        # -- Card 2: Voice Synthesis (TTS) --
        tts_card, tts_form = self._create_card(
            "Voice Synthesis (TTS)",
            "Select Alyssa's spoken voice engine — free Microsoft Edge Neural TTS, free offline Kokoro, or ElevenLabs cloned voices.",
            "🔊",
        )
        self._tts_form = tts_form

        self.tts_provider_combo = QComboBox()
        self.tts_provider_combo.addItems(["edge", "kokoro", "elevenlabs"])
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

        self.kokoro_voice_combo = QComboBox()
        self.kokoro_voice_combo.setEditable(True)
        self.kokoro_voice_combo.addItems(["af_heart", "af_bella", "af_nicole", "af_sky", "bf_emma", "bf_isabella", "jf_alpha"])
        self.kokoro_voice_combo.setCurrentText(getattr(config, "KOKORO_VOICE", "af_heart"))
        self.kokoro_voice_combo.currentTextChanged.connect(self._queue_assistant_apply)
        kokoro_note = self._help_label(
            "Runs on your PC, no internet or key needed. Downloads about 120 MB once, the first time she speaks."
        )
        kokoro_note.setStyleSheet(f"color: {self._t()['subtext']}; font-size: 11.5px;")
        self._kokoro_rows = [
            self._add_row(tts_form, "Kokoro voice:", self._stack_under(self.kokoro_voice_combo, kokoro_note))
        ]

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
        self.security_preset_combo.addItem("Ask always (asks before every tool)", "ask_always")
        self.security_preset_combo.addItem("Careful (looking is fine, asks before changes)", "careful")
        self.security_preset_combo.addItem("Balanced (everyday tools, asks for shell/files)", "balanced")
        self.security_preset_combo.addItem("Autopilot (never asks)", "autopilot")
        from actions.confirmation import normalize_preset
        current_preset = normalize_preset(getattr(config, "SECURITY_PRESET", None))
        preset_idx = self.security_preset_combo.findData(current_preset)
        self.security_preset_combo.setCurrentIndex(preset_idx if preset_idx >= 0 else 2)
        self.security_preset_combo.currentIndexChanged.connect(self._queue_assistant_apply)
        self._add_row(behavior_form, "Security preset:", self.security_preset_combo)

        self.confirm_check = QCheckBox("Ask for confirmation before every action")
        self.confirm_check.setChecked(bool(config.CONFIRM_BEFORE_ACTIONS))
        self.confirm_check.toggled.connect(self._queue_assistant_apply)
        self._add_row(behavior_form, "Action prompts:", self.confirm_check)

        from brain.persona import DEFAULT_PRESET, PRESETS

        self.personality_combo = QComboBox()
        for key, (label, vibe) in PRESETS.items():
            self.personality_combo.addItem(label, key)
            self.personality_combo.setItemData(self.personality_combo.count() - 1, vibe.capitalize(), Qt.ToolTipRole)
        idx = self.personality_combo.findData(getattr(config, "PERSONALITY_PRESET", DEFAULT_PRESET))
        self.personality_combo.setCurrentIndex(max(idx, 0))
        self.personality_combo.currentIndexChanged.connect(self._queue_assistant_apply)
        self._add_row(behavior_form, "Personality:", self.personality_combo)

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

        brief_card, brief_form = self._create_card(
            "Daily Briefing",
            "Once a day she sums up the weather, your calendar, due reminders and news.",
            "☀️",
        )
        self.briefing_enabled_check = QCheckBox("Give me a briefing every day")
        self.briefing_enabled_check.setChecked(bool(getattr(config, "BRIEFING_ENABLED", False)))
        self.briefing_enabled_check.toggled.connect(self._queue_assistant_apply)
        self._add_row(brief_form, "Daily briefing:", self.briefing_enabled_check)

        self.briefing_time_edit = QTimeEdit()
        self.briefing_time_edit.setDisplayFormat("h:mm AP")
        self.briefing_time_edit.setTime(
            QTime.fromString(str(getattr(config, "BRIEFING_TIME", "08:00")), "HH:mm")
        )
        self.briefing_time_edit.timeChanged.connect(self._queue_assistant_apply)
        self._add_row(brief_form, "At:", self.briefing_time_edit)

        self.briefing_topics_edit = QLineEdit(str(getattr(config, "BRIEFING_NEWS_TOPICS", "") or ""))
        self.briefing_topics_edit.setPlaceholderText("e.g. world news, AI, Lakers")
        self.briefing_topics_edit.editingFinished.connect(self._queue_assistant_apply)
        self._add_row(brief_form, "News topics:", self.briefing_topics_edit)
        outer.addWidget(brief_card)

        remote_card, remote_form = self._create_card(
            "Phone Remote",
            "Chat with her from your phone's browser on the same Wi-Fi. Only devices on your "
            "local network with this secret link get in.",
            "📱",
        )
        self.phone_remote_check = QCheckBox("Let my phone talk to her")
        self.phone_remote_check.setChecked(bool(getattr(config, "PHONE_REMOTE_ENABLED", False)))
        self.phone_remote_check.toggled.connect(self._queue_assistant_apply)
        self.phone_remote_check.toggled.connect(self._refresh_phone_remote_link)
        self._add_row(remote_form, "Phone remote:", self.phone_remote_check)

        self.phone_remote_qr = QLabel()
        self.phone_remote_qr.setAlignment(Qt.AlignLeft)
        self._add_row(remote_form, "Scan:", self.phone_remote_qr)
        self.phone_remote_url = QLabel()
        self.phone_remote_url.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.phone_remote_url.setWordWrap(True)
        self._add_row(remote_form, "Or open:", self.phone_remote_url)
        self.phone_remote_new_btn = QPushButton("New link (logs other phones out)")
        self.phone_remote_new_btn.setObjectName("secondaryButton")
        self.phone_remote_new_btn.clicked.connect(lambda: self._refresh_phone_remote_link(regenerate=True))
        self._add_row(remote_form, "", self.phone_remote_new_btn)
        outer.addWidget(remote_card)
        self._refresh_phone_remote_link()

        discord_card, discord_form = self._create_card(
            "Discord Bot",
            "She joins your Discord voice channel as her own bot and talks there. Friends' "
            "speech is turned into text on this PC; the AI only runs when someone says her name. "
            "Create the bot at discord.com/developers/applications (Bot > Reset Token).",
            "🎧",
        )
        self.discord_enabled_check = QCheckBox("Use her Discord bot for calls")
        self.discord_enabled_check.setChecked(bool(getattr(config, "DISCORD_BOT_ENABLED", False)))
        self.discord_enabled_check.toggled.connect(self._queue_assistant_apply)
        self._add_row(discord_form, "Discord bot:", self.discord_enabled_check)
        import credential_store
        self.discord_token_edit = QLineEdit()
        self.discord_token_edit.setEchoMode(QLineEdit.Password)
        has_token = bool(credential_store.get_secret("DISCORD_BOT_TOKEN"))
        self.discord_token_edit.setPlaceholderText("Saved - paste a new one to replace it" if has_token else "Paste the bot token")
        self.discord_token_edit.editingFinished.connect(self._queue_assistant_apply)
        self._add_row(discord_form, "Bot token:", self.discord_token_edit)
        self.discord_owner_edit = QLineEdit(str(getattr(config, "DISCORD_OWNER_ID", "") or ""))
        self.discord_owner_edit.setPlaceholderText("Your user ID (Developer Mode > right-click you > Copy User ID)")
        self.discord_owner_edit.editingFinished.connect(self._queue_assistant_apply)
        self._add_row(discord_form, "Your Discord ID:", self.discord_owner_edit)
        self.discord_listen_check = QCheckBox("Let her hear my friends")
        self.discord_listen_check.setChecked(bool(getattr(config, "DISCORD_LISTEN", True)))
        self.discord_listen_check.toggled.connect(self._queue_assistant_apply)
        self._add_row(discord_form, "Listening:", self.discord_listen_check)
        self.discord_status_label = QLabel()
        self.discord_status_label.setWordWrap(True)
        self.discord_status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._add_row(discord_form, "Status:", self.discord_status_label)
        outer.addWidget(discord_card)
        self._refresh_discord_status()
        outer.addStretch(1)

        self._update_strong_provider_visibility()  # also sets the provider rows
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

    def _refresh_discord_status(self):
        import discord_bot

        text = f"Bot: {discord_bot.status()}."
        if not discord_bot.can_listen():
            text += (" Bot listening isn't installed, so she hears the call through this PC's "
                     "audio (stay in the call yourself).")
        link = discord_bot.invite_url()
        if link:
            text += f"\nAdd her to a server: {link}"
        self.discord_status_label.setText(text)

    def _refresh_phone_remote_link(self, *args, regenerate: bool = False):
        """Show the phone link and its QR code while the remote is switched on."""
        import phone_remote

        on = self.phone_remote_check.isChecked()
        for widget in (self.phone_remote_qr, self.phone_remote_url, self.phone_remote_new_btn):
            widget.setVisible(on)
        if not on:
            return
        try:
            if regenerate:
                phone_remote.token(regenerate=True)
            url = phone_remote.phone_url()
        except OSError as e:
            self.phone_remote_url.setText(f"Couldn't create the link: {e}")
            return
        self.phone_remote_url.setText(f"{url}\n(Windows may ask to allow network access the first time.)")
        try:
            import io
            import segno
            from PySide6.QtGui import QPixmap

            buffer = io.BytesIO()
            segno.make(url, error="m").save(buffer, kind="png", scale=5, border=2)
            pixmap = QPixmap()
            pixmap.loadFromData(buffer.getvalue(), "PNG")
            self.phone_remote_qr.setPixmap(pixmap)
        except ImportError:
            self.phone_remote_qr.setText("(QR code needs the segno package - use the link below)")

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

        _set_console_visible = _settings_dialog._set_console_visible
        hide = not show
        try:
            user_settings.save({"HIDE_CONSOLE_WINDOW": hide})
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
            "LLM_REASONING_TIER": self.reasoning_tier_combo.currentData() or "fast",
            "LLM_STRONG_PROVIDER": self.strong_provider_combo.currentText(),
            "LLM_STRONG_MODEL": self.strong_model_edit.currentText().strip(),
            "OLLAMA_MODEL": self.ollama_model_edit.text().strip() or config.OLLAMA_MODEL,
            "GEMINI_MODEL": self.gemini_model_edit.currentText().strip() or config.GEMINI_MODEL,
            "OPENAI_MODEL": self.openai_model_edit.currentText().strip() or getattr(config, "OPENAI_MODEL", ""),
            "ANTHROPIC_MODEL": self.anthropic_model_edit.currentText().strip() or getattr(config, "ANTHROPIC_MODEL", ""),
            "CUSTOM_BASE_URL": custom_base_url,
            "CUSTOM_MODEL": self.custom_model_edit.currentText().strip(),
            "TTS_PROVIDER": self.tts_provider_combo.currentText(),
            "EDGE_TTS_VOICE": self.voice_combo.currentText().strip() or config.EDGE_TTS_VOICE,
            "KOKORO_VOICE": self.kokoro_voice_combo.currentText().strip() or "af_heart",
            "EDGE_TTS_VOLUME": _percent_to_volume_str(self.volume_slider.value()),
            "ELEVENLABS_VOICE_ID": _extract_elevenlabs_voice_id(self.elevenlabs_voice_combo.currentText()),
            "ELEVENLABS_MODEL": (
                self.elevenlabs_model_combo.currentText().strip() or getattr(config, "ELEVENLABS_MODEL", "eleven_multilingual_v2")
            ),
            "SPEAK_RESPONSES": self.speak_check.isChecked(),
            "CAVEMAN_MODE": self.caveman_combo.currentData(),
            "PERSONALITY_PRESET": self.personality_combo.currentData() or "playful",
            "CONFIRM_BEFORE_ACTIONS": self.confirm_check.isChecked(),
            "SECURITY_PRESET": self.security_preset_combo.currentData() or "balanced",
            "QUIET_MODE": self.quiet_mode_check.isChecked(),
            "HIDE_CONSOLE_WINDOW": not self.show_console_check.isChecked(),
            "SYSTEM_WATCH_ENABLED": self.system_watch_enabled_check.isChecked(),
            "SYSTEM_WATCH_INTERVAL_SECONDS": self.system_watch_interval_spin.value(),
            "BRIEFING_ENABLED": self.briefing_enabled_check.isChecked(),
            "BRIEFING_TIME": self.briefing_time_edit.time().toString("HH:mm"),
            "BRIEFING_NEWS_TOPICS": self.briefing_topics_edit.text().strip(),
            "PHONE_REMOTE_ENABLED": self.phone_remote_check.isChecked(),
            "DISCORD_BOT_ENABLED": self.discord_enabled_check.isChecked(),
            "DISCORD_OWNER_ID": self.discord_owner_edit.text().strip(),
            "DISCORD_LISTEN": self.discord_listen_check.isChecked(),
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
            "VAD_ENGINE": self.vad_engine_combo.currentData() or "auto",
            "SILERO_VAD_THRESHOLD": round(self.vad_threshold_slider.value() / 100.0, 2),
            "PRE_ROLL_BUFFER_MS": int(self.pre_roll_spin.value()),
            "WAKE_WORD_MODE": self.wake_word_mode_combo.currentData() or "wake_word",
            "WAKE_WORD_THRESHOLD": round(self.wake_sensitivity_slider.value() / 100.0, 2),
            "WAKE_CHIME_ENABLED": self.wake_chime_check.isChecked(),
            "WAKE_CHIME_VOLUME": round(self.wake_chime_volume_slider.value() / 100.0, 2),
            "VOICE_DSP_ENABLED": bool(self.voice_dsp_enabled_check.isChecked()) if hasattr(self, "voice_dsp_enabled_check") else bool(getattr(config, "VOICE_DSP_ENABLED", False)),
            "VOICE_PITCH_SEMITONES": round(float(self.voice_pitch_spin.value()), 2) if hasattr(self, "voice_pitch_spin") else float(getattr(config, "VOICE_PITCH_SEMITONES", 0.0)),
            "VOICE_SPEED_FACTOR": round(float(self.voice_speed_spin.value()), 2) if hasattr(self, "voice_speed_spin") else float(getattr(config, "VOICE_SPEED_FACTOR", 1.0)),
            "VOICE_ROBOT_AMOUNT": int(self.voice_robot_slider.value()) if hasattr(self, "voice_robot_slider") else int(getattr(config, "VOICE_ROBOT_AMOUNT", 0)),
            "VOICE_GLITCH_AMOUNT": int(self.voice_glitch_slider.value()) if hasattr(self, "voice_glitch_slider") else int(getattr(config, "VOICE_GLITCH_AMOUNT", 0)),
            "VOICE_LAYERS_ENABLED": bool(self.voice_layers_check.isChecked()) if hasattr(self, "voice_layers_check") else bool(getattr(config, "VOICE_LAYERS_ENABLED", False)),
            "TYPING_SOUND_ENABLED": bool(self.typing_sound_check.isChecked()) if hasattr(self, "typing_sound_check") else bool(getattr(config, "TYPING_SOUND_ENABLED", False)),
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

    def _update_strong_provider_visibility(self, *args):
        visible = (self.reasoning_tier_combo.currentData() or "fast") != "fast"
        self._set_rows_visible(self._assistant_form, [self._strong_provider_row, self._strong_model_row], visible)
        self._update_provider_rows_visibility()

    @staticmethod
    def _model_list_key(service: str) -> str:
        return "custom" if service == "custom_openai" else service

    def _refill_strong_model_combo(self, service: str):
        """Offer the new service's models; a name from the old service would just 404."""
        self.strong_model_edit.blockSignals(True)
        self.strong_model_edit.clear()
        self.strong_model_edit.addItems(self.DEFAULT_PROVIDER_MODELS.get(self._model_list_key(service), []))
        self.strong_model_edit.setCurrentText("")
        self.strong_model_edit.blockSignals(False)
        self._queue_assistant_apply()

    def refresh_tokens_today(self):
        from brain import usage

        used = usage.today()
        total = used["input"] + used["output"]
        self.tokens_today_label.setText(
            f"Today: {total:,} tokens over {used['calls']} model calls." if used["calls"] else ""
        )
        self.tokens_today_label.setVisible(bool(used["calls"]))

    def _gather_assistant_updates(self, values=None) -> dict:
        """Non-secret fields to save to settings.json. SECRET_ENV_VARS go through _gather_secret_updates()."""
        values = values or self._gather_assistant_values()
        return {
            key: value for key, value in values.items()
            if key not in credential_store.SECRET_ENV_VARS
        }

    def _apply_edge_volume_live(self, value: int):
        # Playback reads this immediately; the settings.json write stays debounced.
        config.EDGE_TTS_VOLUME = _percent_to_volume_str(value)
        self._queue_assistant_apply()

    def _apply_assistant_live(self):
        from .. import settings_dialog as _settings_dialog

        _set_console_visible = _settings_dialog._set_console_visible
        values = self._gather_assistant_values()
        updates = self._gather_assistant_updates(values)
        try:
            user_settings.save(updates)
        except OSError as e:
            QMessageBox.warning(self, "Settings", f"Couldn't save your settings: {e}")
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
        old_preset = getattr(config, "SECURITY_PRESET", "balanced")
        old_whisper = (config.WHISPER_MODEL_SIZE, config.WHISPER_DEVICE, config.WHISPER_COMPUTE_TYPE)
        old_system_watch = (
            config.SYSTEM_WATCH_ENABLED, config.SYSTEM_WATCH_INTERVAL_SECONDS
        )
        old_spotify = (config.SPOTIFY_CLIENT_ID, config.SPOTIFY_CLIENT_SECRET)
        old_audio_output = getattr(config, "AUDIO_OUTPUT_DEVICE", "default")
        for key, value in values.items():
            setattr(config, key, value)
        import phone_remote
        remote_error = phone_remote.start()
        if remote_error:
            QMessageBox.warning(self, "Phone remote", remote_error)
        import discord_bot
        new_token = self.discord_token_edit.text().strip()
        if new_token:
            if credential_store.set_secret("DISCORD_BOT_TOKEN", new_token):
                self.discord_token_edit.clear()
                self.discord_token_edit.setPlaceholderText("Saved - paste a new one to replace it")
            discord_bot.stop()  # log in again with the new token
        discord_error = discord_bot.start()
        if discord_error and self.discord_enabled_check.isChecked():
            QMessageBox.warning(self, "Discord bot", discord_error)
        QTimer.singleShot(4000, self._refresh_discord_status)

        if old_preset != getattr(config, "SECURITY_PRESET", "balanced"):
            try:
                import actions
                actions.apply_security_preset(config.SECURITY_PRESET)
            except Exception as e:
                startup_logging.warn_once("security preset", e)
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
