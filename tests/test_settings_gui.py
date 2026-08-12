import os
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

import overlay


class SettingsGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.save_patch = patch.object(overlay, "save_overlay_settings")
        self.save_patch.start()
        self.assistant_apply_patch = patch.object(
            overlay.ConfigDialog, "_apply_assistant_live"
        )
        self.assistant_apply_patch.start()
        settings = dict(overlay.DEFAULT_OVERLAY_SETTINGS)
        settings.update({"pos_x": 120, "pos_y": 140, "scale": 1.25})
        self.companion = overlay.CompanionWindow(settings, overlay.Bridge())
        self.dialog = overlay.ConfigDialog(self.companion)

    def tearDown(self):
        self.dialog.close()
        self.companion.close()
        self.app.processEvents()
        self.assistant_apply_patch.stop()
        self.save_patch.stop()

    def test_non_scale_companion_controls_preserve_geometry(self):
        self.companion.resize(333, 472)
        self.companion.move(211, 173)
        self.companion.settings["scale"] = self.companion.width() / overlay.BASE_W
        self.dialog.sync_companion_scale(self.companion.settings["scale"])
        expected_geometry = self.companion.geometry()

        changes = [
            (self.dialog.opacity_slider, "setValue", 73),
            (self.dialog.always_on_top_check, "setChecked", False),
            (self.dialog.bubble_seconds_spin, "setValue", 11),
            (self.dialog.chatbox_enabled_check, "setChecked", False),
            (self.dialog.chatbox_position_combo, "setCurrentIndex", 1),
            (self.dialog.mouth_flap_check, "setChecked", False),
            (self.dialog.bounce_check, "setChecked", False),
            (self.dialog.bounce_height_spin, "setValue", 17),
            (self.dialog.dim_idle_check, "setChecked", True),
            (self.dialog.dim_idle_spin, "setValue", 44),
        ]
        for widget, method, value in changes:
            getattr(widget, method)(value)
            self.dialog._apply_companion_live()
            self.assertEqual(self.companion.geometry(), expected_geometry)

    def test_invalid_character_path_is_not_applied(self):
        original = self.companion.settings["character_image"]
        self.dialog.image_edit.setText("missing-character.exe")
        self.dialog._apply_companion_live()
        self.assertEqual(self.companion.settings["character_image"], original)
        self.assertIn("existing PNG", self.dialog.image_edit.toolTip())

    def test_always_on_top_toggle_keeps_visible_geometry(self):
        self.companion.show()
        self.app.processEvents()
        expected_geometry = self.companion.geometry()
        self.dialog.always_on_top_check.setChecked(False)
        self.dialog._apply_companion_live()
        self.app.processEvents()
        self.assertTrue(self.companion.isVisible())
        self.assertEqual(self.companion.geometry(), expected_geometry)

    def test_reopening_settings_restores_existing_window(self):
        self.companion._settings_dialog = self.dialog
        self.dialog.showMinimized()
        self.app.processEvents()
        self.companion.open_settings()
        self.app.processEvents()
        self.assertIs(self.companion._settings_dialog, self.dialog)
        self.assertFalse(self.dialog.isMinimized())

    def test_preview_is_large_and_tightly_spaced(self):
        self.assertEqual(self.dialog.preview_label.width(), 140)
        self.assertEqual(self.dialog.preview_label.height(), 198)
        self.assertEqual(self.dialog._preview_layout.spacing(), 2)

    def test_show_command_prompt_applies_immediately(self):
        with patch.object(overlay, "_set_console_visible") as set_visible:
            self.dialog.show_console_check.setChecked(
                not self.dialog.show_console_check.isChecked()
            )
            set_visible.assert_called_once_with(
                self.dialog.show_console_check.isChecked()
            )

    def test_invalid_custom_url_is_not_saved(self):
        original = overlay.config.CUSTOM_BASE_URL
        self.dialog.custom_base_url_edit.setText("not a URL")
        updates = self.dialog._gather_assistant_updates()
        self.assertEqual(updates["CUSTOM_BASE_URL"], repr(original))
        self.assertIn("valid http", self.dialog.custom_models_status_label.text())


if __name__ == "__main__":
    unittest.main()
