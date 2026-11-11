import os
import subprocess
import sys

from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFrame,
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
        """Read whether the AlyssaAssistant scheduled task exists (query only; no admin prompt)."""
        try:
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

    def _build_companion_tab(self) -> QWidget:
        w = QWidget()
        w.setObjectName("tabPage")
        outer = QVBoxLayout(w)
        outer.setContentsMargins(18, 16, 18, 18)
        outer.setSpacing(14)

        outer.addWidget(
            self._page_header(
                "Desktop Companion & Visuals",
                "Customize Alyssa's avatar sprites, color theme, animations, desktop physics, and floating bars.",
                "AVATAR & UI",
            )
        )

        top_row = QGridLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setHorizontalSpacing(16)
        top_row.setVerticalSpacing(14)
        self._companion_grid = top_row

        # -- Left Showcase Card: Character Preview --
        self._preview_panel = QFrame()
        self._preview_panel.setObjectName("previewShowcaseCard")
        preview_col = QVBoxLayout(self._preview_panel)
        preview_col.setContentsMargins(16, 14, 16, 14)
        preview_col.setSpacing(2)
        preview_col.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        self._preview_layout = preview_col

        stage_title = QLabel("LIVE AVATAR")
        stage_title.setObjectName("sectionHeaderFirst")
        stage_title.setAlignment(Qt.AlignCenter)
        preview_col.addWidget(stage_title, 0, Qt.AlignHCenter)

        self.preview_label = QLabel()
        self.preview_label.setFixedSize(140, 198)
        self.preview_label.setScaledContents(True)
        preview_col.addWidget(self.preview_label, 0, Qt.AlignHCenter)

        self.preview_talking_check = QCheckBox("Preview talking")
        self.preview_talking_check.toggled.connect(self._refresh_preview)
        preview_col.addWidget(self.preview_talking_check, 0, Qt.AlignHCenter)
        top_row.addWidget(self._preview_panel, 0, 0, Qt.AlignTop)

        # -- Right Column: Card Stack --
        self._companion_form_panel = QWidget()
        form_col = QVBoxLayout(self._companion_form_panel)
        form_col.setContentsMargins(0, 0, 0, 0)
        form_col.setSpacing(14)

        # Card 1: Character Appearance
        appear_card, appear_form = self._create_card(
            "Character Appearance & Scale",
            "Choose idle and speaking sprites (PNG, GIF, or SVG) and adjust desktop scale and opacity.",
            "🎨",
        )
        self.image_edit = QLineEdit(self.companion.settings.get("character_image", ""))
        self.image_edit.setPlaceholderText("(default: assets/nottalk.png, mouth closed)")
        browse_btn = QPushButton("Browse…")
        browse_btn.setObjectName("compactButton")
        browse_btn.clicked.connect(self._browse_image)
        default_btn = QPushButton("Default")
        default_btn.setObjectName("compactButton")
        default_btn.clicked.connect(self._use_default_image)
        img_row = QHBoxLayout()
        img_row.setSpacing(6)
        img_row.addWidget(self.image_edit, 1)
        img_row.addWidget(browse_btn)
        img_row.addWidget(default_btn)
        self._add_row(appear_form, "Idle image:", img_row)

        self.talk_image_edit = QLineEdit(self.companion.settings.get("character_image_talking", ""))
        self.talk_image_edit.setPlaceholderText("(default: assets/talkopen.png, mouth open)")
        talk_browse_btn = QPushButton("Browse…")
        talk_browse_btn.setObjectName("compactButton")
        talk_browse_btn.clicked.connect(self._browse_talk_image)
        talk_default_btn = QPushButton("Default")
        talk_default_btn.setObjectName("compactButton")
        talk_default_btn.clicked.connect(self._use_default_talk_image)
        talk_img_row = QHBoxLayout()
        talk_img_row.setSpacing(6)
        talk_img_row.addWidget(self.talk_image_edit, 1)
        talk_img_row.addWidget(talk_browse_btn)
        talk_img_row.addWidget(talk_default_btn)
        self._add_row(appear_form, "Speaking image:", talk_img_row)

        self.scale_slider = QSlider(Qt.Horizontal)
        self.scale_slider.setRange(50, 250)
        self.scale_slider.setValue(int(self.companion.settings.get("scale", 1.0) * 100))
        self._add_row(
            appear_form,
            "Character size:",
            self._slider_with_badge(self.scale_slider),
        )

        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(30, 100)
        self.opacity_slider.setValue(int(self.companion.settings.get("opacity", 1.0) * 100))
        self._add_row(
            appear_form,
            "Opacity:",
            self._slider_with_badge(self.opacity_slider),
        )
        form_col.addWidget(appear_card)

        # Card 2: Theme, Window & Startup
        win_card, win_form = self._create_card(
            "Theme, Window & Startup",
            "Switch color palettes across Settings and floating widgets, and configure always-on-top behavior.",
            "🪟",
        )
        self.theme_combo = QComboBox()
        current_theme = self.companion.settings.get("color_theme")
        if not current_theme:
            current_theme = "dark" if self.companion.settings.get("dark_mode", False) else "light"
        for key, info in COLOR_THEMES.items():
            self.theme_combo.addItem(info.get("name", key.title()), key)
        idx = self.theme_combo.findData(current_theme)
        self.theme_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        self._add_row(win_form, "Color theme:", self.theme_combo)

        self.dark_mode_check = QCheckBox("Dark mode (Settings window + speech bubble)")
        self.dark_mode_check.setChecked(bool(self.companion.settings.get("dark_mode", False)))
        self.dark_mode_check.toggled.connect(self._on_dark_mode_toggled)
        self._add_row(win_form, "Dark mode:", self.dark_mode_check)

        self.always_on_top_check = QCheckBox("Keep companion always on top of other windows")
        self.always_on_top_check.setChecked(bool(self.companion.settings.get("always_on_top", True)))
        self._add_row(win_form, "Pin on top:", self.always_on_top_check)

        if sys.platform.startswith("win"):
            self.startup_status_label = self._help_label("")
            self.startup_status_label.setStyleSheet(f"color: {self._t()['subtext']}; font-size: 11.5px;")

            startup_row = QHBoxLayout()
            startup_row.setSpacing(6)
            self.startup_enable_btn = QPushButton("Enable admin startup")
            self.startup_enable_btn.setObjectName("compactButton")
            self.startup_enable_btn.setToolTip("Start Alyssa as administrator at Windows login.")
            self.startup_enable_btn.clicked.connect(self._on_enable_startup)
            startup_row.addWidget(self.startup_enable_btn)

            self.startup_disable_btn = QPushButton("Disable")
            self.startup_disable_btn.setObjectName("compactButton")
            self.startup_disable_btn.setToolTip("Disable startup at Windows login.")
            self.startup_disable_btn.clicked.connect(self._on_disable_startup)
            startup_row.addWidget(self.startup_disable_btn)
            startup_row.addStretch(1)
            self._add_row(
                win_form,
                "Windows login:",
                self._stack_under(startup_row, self.startup_status_label),
            )
            self._refresh_startup_status()
        form_col.addWidget(win_card)

        # Card 3: Speech Bubble, Chat Bar & Media Deck
        bubble_card, bubble_form = self._create_card(
            "Speech Bubble, Chat Bar & Media Deck",
            "Configure how long replies linger and where the floating chat input and mini player dock.",
            "💬",
        )
        self.bubble_seconds_spin = QSpinBox()
        self.bubble_seconds_spin.setRange(2, 30)
        self.bubble_seconds_spin.setSuffix(" sec")
        self.bubble_seconds_spin.setValue(int(self.companion.settings.get("bubble_seconds", 7)))
        self._add_row(bubble_form, "Bubble linger time:", self.bubble_seconds_spin)

        self.chatbox_enabled_check = QCheckBox("Show typed-command chat bar")
        self.chatbox_enabled_check.setChecked(bool(self.companion.settings.get("chatbox_enabled", True)))
        self.chatbox_enabled_check.setToolTip("Type commands instead of speaking them.")
        self._add_row(bubble_form, "Chat input bar:", self.chatbox_enabled_check)

        self.chatbox_position_combo = QComboBox()
        self.chatbox_position_combo.addItem("Bottom (Under companion)", "bottom")
        self.chatbox_position_combo.addItem("Left of companion", "left")
        self.chatbox_position_combo.addItem("Right of companion", "right")
        current_pos = self.companion.settings.get("chatbox_position", "bottom")
        idx = self.chatbox_position_combo.findData(current_pos)
        self.chatbox_position_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.chatbox_position_combo.setEnabled(self.chatbox_enabled_check.isChecked())
        self.chatbox_enabled_check.toggled.connect(self.chatbox_position_combo.setEnabled)
        self._add_row(bubble_form, "Chat bar position:", self.chatbox_position_combo)

        self.mini_player_check = QCheckBox("Show floating mini music player bar")
        self.mini_player_check.setChecked(bool(self.companion.settings.get("mini_player_enabled", False)))
        self._add_row(bubble_form, "Mini media deck:", self.mini_player_check)
        form_col.addWidget(bubble_card)

        # Card 4: Talk Animation & Idle Dimming
        anim_card, anim_form = self._create_card(
            "Talk Animation & Idle Dimming",
            "Mouth-flap animation, speaking bounce, and automatic opacity dimming while idle.",
            "🗣️",
        )
        self.mouth_flap_check = QCheckBox("Swap talk / idle sprite while speaking")
        self.mouth_flap_check.setChecked(bool(self.companion.settings.get("talk_mouth_flap_enabled", True)))
        self.mouth_flap_check.toggled.connect(self._refresh_preview)
        self._add_row(anim_form, "Mouth animation:", self.mouth_flap_check)

        self.bounce_check = QCheckBox("Bounce gently while speaking")
        self.bounce_check.setChecked(bool(self.companion.settings.get("talk_bounce_enabled", True)))
        self._add_row(anim_form, "Speaking bounce:", self.bounce_check)

        self.bounce_height_spin = QSpinBox()
        self.bounce_height_spin.setRange(0, 60)
        self.bounce_height_spin.setSuffix(" px")
        self.bounce_height_spin.setValue(int(self.companion.settings.get("talk_bounce_height", 0)))
        self._add_row(anim_form, "Bounce height:", self.bounce_height_spin)

        self.dim_idle_check = QCheckBox("Dim companion when she's not speaking")
        self.dim_idle_check.setChecked(bool(self.companion.settings.get("dim_when_idle_enabled", False)))
        self._add_row(anim_form, "Idle dimming:", self.dim_idle_check)

        self.dim_idle_spin = QSpinBox()
        self.dim_idle_spin.setRange(10, 100)
        self.dim_idle_spin.setSuffix("% opacity")
        self.dim_idle_spin.setValue(int(self.companion.settings.get("dim_when_idle_opacity", 55)))
        self._add_row(anim_form, "Dimmed level:", self.dim_idle_spin)
        form_col.addWidget(anim_card)

        # Card 5: Companion Life, Movement & Physics
        life_card, life_form = self._create_card(
            "Companion Life, Movement & Physics",
            "Autonomous wandering, window ledge perching, head-pat reactions, breathing, and eye gaze.",
            "✨",
        )
        self.wander_mode_combo = QComboBox()
        for label, val in (
            ("Off (Stay where placed)", "off"),
            ("Rarely", "rarely"),
            ("Sometimes", "sometimes"),
            ("Often", "often"),
            ("Restless", "restless"),
        ):
            self.wander_mode_combo.addItem(label, val)
        w_idx = self.wander_mode_combo.findData(self.companion.settings.get("wander_mode", "off"))
        self.wander_mode_combo.setCurrentIndex(w_idx if w_idx >= 0 else 0)
        self._add_row(life_form, "Wandering:", self.wander_mode_combo)

        self.perch_windows_check = QCheckBox("Perch on active window top ledges")
        self.perch_windows_check.setChecked(bool(self.companion.settings.get("perch_on_windows", True)))
        self._add_row(life_form, "Window ledges:", self.perch_windows_check)

        self.sleep_after_spin = QSpinBox()
        self.sleep_after_spin.setRange(0, 240)
        self.sleep_after_spin.setSuffix(" min (0 = off)")
        self.sleep_after_spin.setValue(int(self.companion.settings.get("sleep_after_minutes", 15)))
        self._add_row(life_form, "Nap after idle:", self.sleep_after_spin)

        self.head_pats_check = QCheckBox("React to head pats (cursor rub)")
        self.head_pats_check.setChecked(bool(self.companion.settings.get("head_pats_enabled", True)))
        self._add_row(life_form, "Head pats:", self.head_pats_check)

        self.cursor_ride_check = QCheckBox("Allow cursor riding (Shift + double-click)")
        self.cursor_ride_check.setChecked(bool(self.companion.settings.get("cursor_ride_enabled", True)))
        self._add_row(life_form, "Cursor riding:", self.cursor_ride_check)

        self.breathing_check = QCheckBox("Idle breathing physics")
        self.breathing_check.setChecked(bool(self.companion.settings.get("idle_breathing_enabled", True)))
        self._add_row(life_form, "Breathing motion:", self.breathing_check)

        self.gaze_check = QCheckBox("Cursor gaze & parallax tracking")
        self.gaze_check.setChecked(bool(self.companion.settings.get("cursor_gaze_enabled", True)))
        self._add_row(life_form, "Cursor tracking:", self.gaze_check)

        self.emotes_check = QCheckBox("Dynamic mood emote badges")
        self.emotes_check.setChecked(bool(self.companion.settings.get("emotes_enabled", True)))
        self._add_row(life_form, "Mood emotes:", self.emotes_check)

        self.blinking_check = QCheckBox("Natural periodic blinking")
        self.blinking_check.setChecked(bool(self.companion.settings.get("blinking_enabled", True)))
        self._add_row(life_form, "Blinking:", self.blinking_check)
        form_col.addWidget(life_card)

        top_row.addWidget(self._companion_form_panel, 0, 1)
        top_row.setColumnStretch(1, 1)
        outer.addLayout(top_row)
        outer.addStretch(1)

        for widget, signal in [
            (self.scale_slider, self.scale_slider.valueChanged),
            (self.opacity_slider, self.opacity_slider.valueChanged),
        ]:
            signal.connect(self._refresh_preview)
        self.image_edit.textChanged.connect(self._refresh_preview)
        self.talk_image_edit.textChanged.connect(self._refresh_preview)

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
            (self.wander_mode_combo, self.wander_mode_combo.currentIndexChanged),
            (self.perch_windows_check, self.perch_windows_check.toggled),
            (self.sleep_after_spin, self.sleep_after_spin.valueChanged),
            (self.head_pats_check, self.head_pats_check.toggled),
            (self.cursor_ride_check, self.cursor_ride_check.toggled),
            (self.mini_player_check, self.mini_player_check.toggled),
            (self.breathing_check, self.breathing_check.toggled),
            (self.gaze_check, self.gaze_check.toggled),
            (self.emotes_check, self.emotes_check.toggled),
            (self.blinking_check, self.blinking_check.toggled),
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
            "wander_mode": self.wander_mode_combo.currentData(),
            "perch_on_windows": self.perch_windows_check.isChecked(),
            "sleep_after_minutes": self.sleep_after_spin.value(),
            "head_pats_enabled": self.head_pats_check.isChecked(),
            "cursor_ride_enabled": self.cursor_ride_check.isChecked(),
            "mini_player_enabled": self.mini_player_check.isChecked(),
            "idle_breathing_enabled": self.breathing_check.isChecked(),
            "cursor_gaze_enabled": self.gaze_check.isChecked(),
            "emotes_enabled": self.emotes_check.isChecked(),
            "blinking_enabled": self.blinking_check.isChecked(),
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

    def _apply_companion_live(self):
        current = self._gather_companion_settings()
        changed = {
            key: value for key, value in current.items()
            if self._last_companion_form_values.get(key) != value
        }
        self._last_companion_form_values = current
        if changed:
            self.companion.apply_companion_settings(changed)
