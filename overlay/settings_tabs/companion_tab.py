import os
import subprocess
import sys

from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import (
    QWidget, QFormLayout, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLineEdit, QComboBox, QCheckBox, QSlider, QSpinBox, QPushButton,
    QLabel, QFileDialog,
)

from ..rendering import _BASE_DIR, render_character
from ..theming import COLOR_THEMES


class CompanionTabMixin:
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

    def _build_companion_tab(self) -> QWidget:
        w = QWidget()
        w.setObjectName("tabPage")
        outer = QVBoxLayout(w)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(10)

        top_row = QGridLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setHorizontalSpacing(16)
        top_row.setVerticalSpacing(12)
        self._companion_grid = top_row

        self._preview_panel = QWidget()
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

        self._companion_form_panel = QWidget()
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

    def _apply_companion_live(self):
        current = self._gather_companion_settings()
        changed = {
            key: value for key, value in current.items()
            if self._last_companion_form_values.get(key) != value
        }
        self._last_companion_form_values = current
        if changed:
            self.companion.apply_companion_settings(changed)

