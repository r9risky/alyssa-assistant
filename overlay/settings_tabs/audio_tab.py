from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QLineEdit,
    QSlider,
    QSpinBox,
    QWidget,
)

import config

try:
    import sounddevice as sd
except ImportError:  # pragma: no cover - sounddevice is a core dependency
    sd = None


class AudioTabMixin:
    def _populate_device_combo(self, combo: QComboBox, channel_key: str, configured_value: str):
        """Fill combo with Default + devices exposing `channel_key`. Item data is the config.py string.

        Select Default, or a name containing configured_value. Dedupes by name (Windows lists one device per driver).
        """
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
                continue
            seen_names.add(name)
            combo.addItem(name, name)
            if configured and configured.lower() != "default" and configured.lower() in name.lower():
                combo.setCurrentText(name)
                matched = True
        if not matched and configured and configured.lower() != "default":
            # Configured device not detected — keep it selectable so save doesn't drop it.
            combo.addItem(f"{configured} (not detected)", configured)
            combo.setCurrentText(f"{configured} (not detected)")

    def _build_audio_tab(self) -> QWidget:
        w = QWidget()
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

        form.addRow("", self._section_header("Call & Meeting Mode", "📞"))

        self.call_mode_enabled_check = QCheckBox("Enable Call Mode (Voice chat / meeting relay)")
        self.call_mode_enabled_check.setChecked(bool(getattr(config, "CALL_MODE_ENABLED", False)))
        self.call_mode_enabled_check.toggled.connect(self._queue_assistant_apply)
        form.addRow("", self.call_mode_enabled_check)

        self.call_app_combo = QComboBox()
        call_apps = [
            ("Auto (Auto-detect front app)", "auto"),
            ("Discord", "discord"),
            ("Zoom", "zoom"),
            ("Microsoft Teams", "teams"),
            ("WhatsApp", "whatsapp"),
            ("Telegram", "telegram"),
            ("Slack", "slack"),
            ("Signal", "signal"),
            ("Skype", "skype"),
            ("TeamSpeak", "teamspeak"),
            ("Mumble", "mumble"),
            ("Steam Voice Chat", "steam"),
            ("Browser Call", "browser"),
        ]
        for name, key in call_apps:
            self.call_app_combo.addItem(name, key)
        cur_call_app = str(getattr(config, "CALL_APP", "auto") or "auto").lower()
        idx_app = self.call_app_combo.findData(cur_call_app)
        self.call_app_combo.setCurrentIndex(idx_app if idx_app >= 0 else 0)
        self.call_app_combo.currentIndexChanged.connect(self._queue_assistant_apply)
        form.addRow("Call application:", self.call_app_combo)

        self.call_virtual_mic_combo = QComboBox()
        self._populate_device_combo(
            self.call_virtual_mic_combo,
            "max_output_channels",
            getattr(config, "CALL_VIRTUAL_MIC_DEVICE", "CABLE Input"),
        )
        self.call_virtual_mic_combo.setToolTip("Virtual cable / mic Alyssa speaks into so friends in call hear her.")
        self.call_virtual_mic_combo.currentIndexChanged.connect(self._queue_assistant_apply)
        form.addRow("Her voice in your mic:", self.call_virtual_mic_combo)

        self.call_virtual_mic_volume_slider = QSlider(Qt.Horizontal)
        self.call_virtual_mic_volume_slider.setRange(0, 100)
        self.call_virtual_mic_volume_slider.setValue(int(float(getattr(config, "CALL_VIRTUAL_MIC_VOLUME", 0.85)) * 100))
        self.call_virtual_mic_volume_slider.valueChanged.connect(self._queue_assistant_apply)
        form.addRow("Call voice volume (%):", self.call_virtual_mic_volume_slider)

        self.call_friends_can_ask_check = QCheckBox("Friends can ask Alyssa things (requires your voice/chat confirmation)")
        self.call_friends_can_ask_check.setChecked(bool(getattr(config, "CALL_FRIENDS_CAN_ASK", False)))
        self.call_friends_can_ask_check.toggled.connect(self._queue_assistant_apply)
        form.addRow("", self.call_friends_can_ask_check)

        self.call_auto_join_check = QCheckBox("Auto-click detected 'Join' / voice channel buttons")
        self.call_auto_join_check.setChecked(bool(getattr(config, "CALL_AUTO_JOIN_ENABLED", True)))
        self.call_auto_join_check.toggled.connect(self._queue_assistant_apply)
        form.addRow("", self.call_auto_join_check)

        self.call_barge_in_spin = QDoubleSpinBox()
        self.call_barge_in_spin.setRange(0.2, 5.0)
        self.call_barge_in_spin.setSingleStep(0.1)
        self.call_barge_in_spin.setSuffix(" s")
        self.call_barge_in_spin.setValue(float(getattr(config, "CALL_BARGE_IN_TOLERANCE_SECONDS", 1.0)))
        self.call_barge_in_spin.valueChanged.connect(self._queue_assistant_apply)
        form.addRow("Barge-in tolerance:", self.call_barge_in_spin)

        form.addRow("", self._section_header("Voice Activity Detection (VAD)", "🎙️"))

        self.vad_engine_combo = QComboBox()
        self.vad_engine_combo.addItem("Auto (Silero Neural preferred)", "auto")
        self.vad_engine_combo.addItem("Silero VAD (Neural, clean)", "silero")
        self.vad_engine_combo.addItem("WebRTC (Classic)", "webrtc")
        curr_vad = str(getattr(config, "VAD_ENGINE", "auto") or "auto").lower()
        idx = self.vad_engine_combo.findData(curr_vad)
        self.vad_engine_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.vad_engine_combo.currentIndexChanged.connect(self._queue_assistant_apply)
        form.addRow("VAD engine:", self.vad_engine_combo)

        self.vad_threshold_slider = QSlider(Qt.Horizontal)
        self.vad_threshold_slider.setRange(10, 90)
        self.vad_threshold_slider.setValue(int(float(getattr(config, "SILERO_VAD_THRESHOLD", 0.50)) * 100))
        self.vad_threshold_slider.valueChanged.connect(self._queue_assistant_apply)
        form.addRow("Speech sensitivity (%):", self.vad_threshold_slider)

        self.pre_roll_spin = QSpinBox()
        self.pre_roll_spin.setRange(100, 1200)
        self.pre_roll_spin.setSingleStep(50)
        self.pre_roll_spin.setSuffix(" ms")
        self.pre_roll_spin.setValue(int(getattr(config, "PRE_ROLL_BUFFER_MS", 400)))
        self.pre_roll_spin.setToolTip("Audio prepended to speech to prevent clipped starts.")
        self.pre_roll_spin.valueChanged.connect(self._queue_assistant_apply)
        form.addRow("Pre-speech buffer:", self.pre_roll_spin)

        form.addRow("", self._section_header("Wake Word Engine", "👂"))

        self.wake_word_mode_combo = QComboBox()
        self.wake_word_mode_combo.addItem("Wake word ('Hey Alyssa')", "wake_word")
        self.wake_word_mode_combo.addItem("Always listening (No wake word)", "always_listening")
        self.wake_word_mode_combo.addItem("Push-to-talk (Hotkey only)", "push_to_talk")
        curr_mode = str(getattr(config, "WAKE_WORD_MODE", "wake_word") or "wake_word").lower()
        idx_ww = self.wake_word_mode_combo.findData(curr_mode)
        self.wake_word_mode_combo.setCurrentIndex(idx_ww if idx_ww >= 0 else 0)
        self.wake_word_mode_combo.currentIndexChanged.connect(self._queue_assistant_apply)
        form.addRow("Listening mode:", self.wake_word_mode_combo)

        self.wake_sensitivity_slider = QSlider(Qt.Horizontal)
        self.wake_sensitivity_slider.setRange(20, 95)
        self.wake_sensitivity_slider.setValue(int(float(getattr(config, "WAKE_WORD_THRESHOLD", 0.65)) * 100))
        self.wake_sensitivity_slider.valueChanged.connect(self._queue_assistant_apply)
        form.addRow("Wake sensitivity (%):", self.wake_sensitivity_slider)

        self.wake_chime_check = QCheckBox("Play confirmation chime on wake-up")
        self.wake_chime_check.setChecked(bool(getattr(config, "WAKE_CHIME_ENABLED", True)))
        self.wake_chime_check.toggled.connect(self._queue_assistant_apply)
        form.addRow("", self.wake_chime_check)

        self.wake_chime_volume_slider = QSlider(Qt.Horizontal)
        self.wake_chime_volume_slider.setRange(0, 100)
        self.wake_chime_volume_slider.setValue(int(float(getattr(config, "WAKE_CHIME_VOLUME", 0.50)) * 100))
        self.wake_chime_volume_slider.valueChanged.connect(self._queue_assistant_apply)
        form.addRow("Chime volume (%):", self.wake_chime_volume_slider)

        form.addRow("", self._section_header("Voice DSP Effects", "🎛️"))
        self.voice_dsp_enabled_check = QCheckBox("Enable Voice DSP post-processing")
        self.voice_dsp_enabled_check.setChecked(bool(getattr(config, "VOICE_DSP_ENABLED", False)))
        self.voice_dsp_enabled_check.toggled.connect(self._queue_assistant_apply)
        form.addRow("", self.voice_dsp_enabled_check)

        self.voice_pitch_spin = QDoubleSpinBox()
        self.voice_pitch_spin.setRange(-12.0, 12.0)
        self.voice_pitch_spin.setSingleStep(0.5)
        self.voice_pitch_spin.setSuffix(" st")
        self.voice_pitch_spin.setValue(float(getattr(config, "VOICE_PITCH_SEMITONES", 0.0)))
        self.voice_pitch_spin.valueChanged.connect(self._queue_assistant_apply)
        form.addRow("Pitch shift:", self.voice_pitch_spin)

        self.voice_speed_spin = QDoubleSpinBox()
        self.voice_speed_spin.setRange(0.5, 2.0)
        self.voice_speed_spin.setSingleStep(0.05)
        self.voice_speed_spin.setSuffix("x")
        self.voice_speed_spin.setValue(float(getattr(config, "VOICE_SPEED_FACTOR", 1.0)))
        self.voice_speed_spin.valueChanged.connect(self._queue_assistant_apply)
        form.addRow("Speed factor:", self.voice_speed_spin)

        self.voice_robot_slider = QSlider(Qt.Horizontal)
        self.voice_robot_slider.setRange(0, 100)
        self.voice_robot_slider.setValue(int(getattr(config, "VOICE_ROBOT_AMOUNT", 0)))
        self.voice_robot_slider.valueChanged.connect(self._queue_assistant_apply)
        form.addRow("Robot synth (%):", self.voice_robot_slider)

        self.voice_glitch_slider = QSlider(Qt.Horizontal)
        self.voice_glitch_slider.setRange(0, 100)
        self.voice_glitch_slider.setValue(int(getattr(config, "VOICE_GLITCH_AMOUNT", 0)))
        self.voice_glitch_slider.valueChanged.connect(self._queue_assistant_apply)
        form.addRow("Glitch / bitcrush (%):", self.voice_glitch_slider)

        self.voice_layers_check = QCheckBox("Enable dual pitch-shifted voice layers")
        self.voice_layers_check.setChecked(bool(getattr(config, "VOICE_LAYERS_ENABLED", False)))
        self.voice_layers_check.toggled.connect(self._queue_assistant_apply)
        form.addRow("", self.voice_layers_check)

        self.typing_sound_check = QCheckBox("Play subtle typing blip when speech bubble appears")
        self.typing_sound_check.setChecked(bool(getattr(config, "TYPING_SOUND_ENABLED", False)))
        self.typing_sound_check.toggled.connect(self._queue_assistant_apply)
        form.addRow("", self.typing_sound_check)

        form.addRow("", self._section_header("Global Hotkeys", "⌨️"))
        self.hotkeys_enabled_check = QCheckBox("Enable global keyboard shortcuts")
        self.hotkeys_enabled_check.setChecked(bool(getattr(config, "HOTKEYS_ENABLED", True)))
        self.hotkeys_enabled_check.toggled.connect(self._queue_assistant_apply)
        form.addRow("", self.hotkeys_enabled_check)

        self.hotkey_talk_edit = QLineEdit(str(getattr(config, "HOTKEY_TALK", "ctrl+alt+v")))
        self.hotkey_talk_edit.editingFinished.connect(self._queue_assistant_apply)
        form.addRow("Wake / Talk:", self.hotkey_talk_edit)

        self.hotkey_mute_edit = QLineEdit(str(getattr(config, "HOTKEY_MUTE", "ctrl+alt+n")))
        self.hotkey_mute_edit.editingFinished.connect(self._queue_assistant_apply)
        form.addRow("Mute / Unmute:", self.hotkey_mute_edit)

        self.hotkey_type_edit = QLineEdit(str(getattr(config, "HOTKEY_TYPE", "ctrl+alt+t")))
        self.hotkey_type_edit.editingFinished.connect(self._queue_assistant_apply)
        form.addRow("Focus Chat Box:", self.hotkey_type_edit)

        self.hotkey_hide_edit = QLineEdit(str(getattr(config, "HOTKEY_HIDE", "ctrl+alt+h")))
        self.hotkey_hide_edit.editingFinished.connect(self._queue_assistant_apply)
        form.addRow("Hide / Show:", self.hotkey_hide_edit)

        self.hotkey_quiet_edit = QLineEdit(str(getattr(config, "HOTKEY_QUIET", "ctrl+alt+p")))
        self.hotkey_quiet_edit.editingFinished.connect(self._queue_assistant_apply)
        form.addRow("Quiet Mode:", self.hotkey_quiet_edit)

        self.hotkey_hop_off_edit = QLineEdit(str(getattr(config, "HOTKEY_HOP_OFF", "ctrl+alt+c")))
        self.hotkey_hop_off_edit.editingFinished.connect(self._queue_assistant_apply)
        form.addRow("Hop Off Cursor:", self.hotkey_hop_off_edit)

        return w


