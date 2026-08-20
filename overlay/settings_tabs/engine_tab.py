from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QWidget, QFormLayout, QComboBox, QLabel, QPushButton

import config


class EngineTabMixin:
    def _build_engine_tab(self) -> QWidget:
        w = QWidget()
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

