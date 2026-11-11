from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import config
from ..credential_checks import _verify_groq_key


class EngineTabMixin:
    def _build_engine_tab(self) -> QWidget:
        w = QWidget()
        w.setObjectName("tabPage")
        outer = QVBoxLayout(w)
        outer.setContentsMargins(18, 16, 18, 18)
        outer.setSpacing(14)

        outer.addWidget(
            self._page_header(
                "Speech Recognition Engine",
                "Configure ultra-fast Groq Cloud Whisper (~90ms) and the local Faster-Whisper acoustic model.",
                "STT ENGINE",
            )
        )

        # -- Card 1: Speech-to-Text Routing & Groq Cloud --
        groq_card, groq_form = self._create_card(
            "Speech-to-Text Routing & Groq Cloud",
            "Groq LPUs transcribe speech in ~90ms and fall back to Local Whisper automatically if offline.",
            "⚡",
        )
        self.stt_provider_combo = QComboBox()
        self.stt_provider_combo.addItem("Auto (Groq when key set, else Local)", "auto")
        self.stt_provider_combo.addItem("Groq Cloud (Fast LPU, fallback Local)", "groq")
        self.stt_provider_combo.addItem("Local Whisper only (Offline-first)", "local")
        curr_stt = str(getattr(config, "STT_PROVIDER", "auto") or "auto").lower()
        stt_idx = self.stt_provider_combo.findData(curr_stt)
        self.stt_provider_combo.setCurrentIndex(stt_idx if stt_idx >= 0 else 0)
        self.stt_provider_combo.currentIndexChanged.connect(self._queue_assistant_apply)
        self._add_row(groq_form, "STT provider:", self.stt_provider_combo)

        self._original_groq_key = getattr(config, "GROQ_API_KEY", "")
        self.groq_key_status_label = self._help_label("")
        groq_link = self._link_label(
            "https://console.groq.com/keys", "Don't have one?", "Get a free Groq API key"
        )
        self.groq_key_edit, self.groq_key_row_label, self._groq_key_rows = self._build_api_key_row(
            groq_form, "Groq API key:", self._original_groq_key,
            self.groq_key_status_label,
            "Paste your Groq API key here (gsk_...)",
            provider="groq", verify=_verify_groq_key, checking="Checking with Groq…",
            extras=(self.groq_key_status_label, groq_link),
        )

        self.groq_model_edit = self._build_model_combo(getattr(config, "STT_GROQ_MODEL", "whisper-large-v3-turbo"))
        self._add_row(groq_form, "Groq Whisper model:", self.groq_model_edit)
        outer.addWidget(groq_card)

        # -- Card 2: Local Whisper Engine --
        local_card, form = self._create_card(
            "Local Faster-Whisper Engine",
            "On-device speech recognition. Changes reload automatically in the background without restarting.",
            "🎙️",
        )
        self.whisper_model_combo = QComboBox()
        self.whisper_model_combo.setEditable(True)
        whisper_models = ["tiny.en", "base.en", "small.en", "medium.en", "large-v3", "large-v3-turbo", "distil-large-v3"]
        self.whisper_model_combo.addItems(whisper_models)
        current_model = getattr(config, "WHISPER_MODEL_SIZE", "base.en")
        if current_model not in whisper_models:
            self.whisper_model_combo.addItem(current_model)
        self.whisper_model_combo.setCurrentText(current_model)
        self.whisper_model_combo.currentTextChanged.connect(self._queue_assistant_apply)
        model_hint = QLabel("Larger models improve accuracy on accents and names; NVIDIA GPUs accelerate inference.")
        model_hint.setWordWrap(True)
        model_hint.setStyleSheet(f"color: {self._t()['subtext']}; font-size: 11.5px;")
        self._add_row(form, "Whisper model:", self._stack_under(self.whisper_model_combo, model_hint))

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
        device_hint = QLabel("GPU mode requires NVIDIA CUDA 12 libraries; otherwise leave on Auto or CPU.")
        device_hint.setWordWrap(True)
        device_hint.setStyleSheet(f"color: {self._t()['subtext']}; font-size: 11.5px;")
        self._add_row(form, "Compute device:", self._stack_under(self.whisper_device_combo, device_hint))

        self.whisper_compute_combo = QComboBox()
        compute_types = ["auto", "float16", "float32", "int8", "int8_float16"]
        self.whisper_compute_combo.addItems(compute_types)
        current_compute = str(getattr(config, "WHISPER_COMPUTE_TYPE", "auto") or "auto").lower()
        if current_compute not in compute_types:
            self.whisper_compute_combo.addItem(current_compute)
        self.whisper_compute_combo.setCurrentText(current_compute)
        self.whisper_compute_combo.currentTextChanged.connect(self._queue_assistant_apply)
        compute_hint = QLabel("Auto selects float16 on CUDA GPUs and int8 quantization on CPUs.")
        compute_hint.setWordWrap(True)
        compute_hint.setStyleSheet(f"color: {self._t()['subtext']}; font-size: 11.5px;")
        self._add_row(form, "Precision type:", self._stack_under(self.whisper_compute_combo, compute_hint))

        self.engine_status_label = QLabel("Checking current status…")
        self.engine_status_label.setWordWrap(True)
        self.engine_status_label.setMinimumHeight(38)
        self.engine_status_label.setStyleSheet(
            f"font-size: 11.5px; color: {self._t()['text']}; padding: 8px 10px; "
            f"background: rgba(99, 102, 241, 0.08); border-radius: 7px;"
        )

        refresh_status_btn = QPushButton("⟳ Refresh status")
        refresh_status_btn.setObjectName("compactButton")
        refresh_status_btn.clicked.connect(self._refresh_engine_status)
        btn_row = QHBoxLayout()
        btn_row.addWidget(refresh_status_btn)
        btn_row.addStretch(1)

        self._add_row(
            form,
            "Runtime status:",
            self._stack_under(self.engine_status_label, btn_row),
        )
        outer.addWidget(local_card)
        outer.addStretch(1)

        self._refresh_engine_status()
        self._engine_status_poll_timer = QTimer(self)
        self._engine_status_poll_timer.setInterval(1000)
        self._engine_status_poll_timer.timeout.connect(self._on_engine_status_poll_tick)
        self._engine_status_poll_ticks = 0

        return w

    def _refresh_engine_status(self):
        try:
            import transcribe
            self.engine_status_label.setText(transcribe.get_engine_status())
        except Exception as e:
            self.engine_status_label.setText(f"Couldn't check status: {e}")

    def _on_engine_status_poll_tick(self):
        self._refresh_engine_status()
        self._engine_status_poll_ticks -= 1
        if self._engine_status_poll_ticks <= 0:
            self._engine_status_poll_timer.stop()
