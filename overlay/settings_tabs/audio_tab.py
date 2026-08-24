from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QFormLayout, QComboBox

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

        return w

