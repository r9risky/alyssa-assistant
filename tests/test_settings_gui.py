import os
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFormLayout

import overlay
from overlay import settings_dialog, widgets
import config
import updater


class SettingsGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.real_assistant_apply = overlay.ConfigDialog._apply_assistant_live
        self.save_patch = patch.object(widgets, "save_overlay_settings")
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

    def test_show_command_prompt_applies_and_persists_immediately(self):
        original = settings_dialog.config.HIDE_CONSOLE_WINDOW
        show = not self.dialog.show_console_check.isChecked()
        try:
            with (
                patch.object(settings_dialog, "_atomic_write_text") as write_text,
                patch.object(settings_dialog, "_set_console_visible") as set_visible,
            ):
                self.dialog.show_console_check.setChecked(show)

            self.assertIn(
                f"HIDE_CONSOLE_WINDOW = {not show}", write_text.call_args.args[1]
            )
            self.assertEqual(settings_dialog.config.HIDE_CONSOLE_WINDOW, not show)
            set_visible.assert_called_once_with(show)
        finally:
            settings_dialog.config.HIDE_CONSOLE_WINDOW = original

    def test_failed_settings_write_does_not_change_runtime_state(self):
        original = settings_dialog.config.HIDE_CONSOLE_WINDOW
        self.dialog.show_console_check.blockSignals(True)
        self.dialog.show_console_check.setChecked(original)
        self.dialog.show_console_check.blockSignals(False)
        with (
            patch.object(settings_dialog, "_atomic_write_text", side_effect=OSError("disk full")),
            patch.object(settings_dialog.QMessageBox, "warning"),
            patch.object(settings_dialog, "_set_console_visible") as set_visible,
        ):
            self.real_assistant_apply(self.dialog)

        self.assertEqual(settings_dialog.config.HIDE_CONSOLE_WINDOW, original)
        set_visible.assert_not_called()

    def test_invalid_custom_url_is_not_saved(self):
        original = settings_dialog.config.CUSTOM_BASE_URL
        self.dialog.custom_base_url_edit.setText("not a URL")
        updates = self.dialog._gather_assistant_updates()
        self.assertEqual(updates["CUSTOM_BASE_URL"], repr(original))
        self.assertIn("valid http", self.dialog.custom_models_status_label.text())

    def test_edge_volume_slider_updates_runtime_immediately(self):
        original = config.EDGE_TTS_VOLUME
        try:
            self.dialog.volume_slider.setValue(-25)
            self.assertEqual(config.EDGE_TTS_VOLUME, "-25%")
        finally:
            config.EDGE_TTS_VOLUME = original

    def test_settings_layout_reflows_at_narrow_and_wide_sizes(self):
        self.dialog.show()
        self.dialog.resize(560, 480)
        self.app.processEvents()

        self.assertEqual(self.dialog.width(), 560)
        self.assertLess(self.dialog.tabs.navigation.width(), 190)
        self.assertGreater(self.dialog.tabs.pages.width(), 300)
        self.assertEqual(
            self.dialog._assistant_form.rowWrapPolicy(), QFormLayout.WrapAllRows
        )
        self.assertEqual(self.dialog._plugin_splitter.orientation(), Qt.Vertical)
        for tab_index in range(3):
            self.dialog.tabs.setCurrentIndex(tab_index)
            self.app.processEvents()
            page = self.dialog.tabs.pages.currentWidget()
            self.assertEqual(page.horizontalScrollBar().maximum(), 0)
            self.assertEqual(page.widget().width(), page.viewport().width())
        preview_position = self.dialog._companion_grid.getItemPosition(
            self.dialog._companion_grid.indexOf(self.dialog._preview_panel)
        )
        form_position = self.dialog._companion_grid.getItemPosition(
            self.dialog._companion_grid.indexOf(self.dialog._companion_form_panel)
        )
        self.assertEqual(preview_position, (0, 0, 1, 2))
        self.assertEqual(form_position, (1, 0, 1, 2))

        self.dialog.resize(900, 700)
        self.app.processEvents()
        self.assertEqual(self.dialog.tabs.navigation.width(), 190)
        self.assertEqual(
            self.dialog._assistant_form.rowWrapPolicy(), QFormLayout.WrapLongRows
        )
        self.assertEqual(self.dialog._plugin_splitter.orientation(), Qt.Horizontal)

    def test_updates_tab_has_check_update_button(self):
        labels = [
            self.dialog.tabs.navigation.item(i).text()
            for i in range(self.dialog.tabs.navigation.count())
        ]
        self.assertIn("Updates", labels)
        self.assertEqual(self.dialog.check_update_btn.text(), "Check Update")
        self.assertEqual(self.dialog.restart_alyssa_btn.text(), "Restart Alyssa")
        self.assertIn(updater.CURRENT_VERSION, self.dialog.current_version_label.text())

    def test_restart_button_flushes_settings_and_relaunches(self):
        with (
            patch.object(self.dialog, "_flush_pending_applies") as flush,
            patch.object(settings_dialog.actions, "relaunch_alyssa") as relaunch,
        ):
            self.dialog.restart_alyssa_btn.click()

        flush.assert_called_once_with()
        relaunch.assert_called_once_with()

    def test_new_plugin_template_generates_importable_function(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(settings_dialog.plugin_loader, "PLUGINS_DIR", temp_dir),
                patch.object(settings_dialog.QInputDialog, "getText", return_value=("Weather Tool", True)),
                patch.object(self.dialog, "_reload_plugins_backend"),
                patch.object(self.dialog, "_refresh_plugin_list"),
            ):
                self.dialog._on_new_plugin()

            namespace = runpy.run_path(os.path.join(temp_dir, "weather_tool.py"))

        function = namespace["FUNCTIONS"]["weather_tool_example"]
        self.assertEqual(function("hello"), "hello, from the weather_tool plugin!")

    def test_update_check_reports_when_current_version_is_latest(self):
        release = {
            "current_version": updater.CURRENT_VERSION,
            "latest_version": "v1.0.4",
            "update_available": False,
            "notes": "Older release",
            "download_url": "https://example.invalid/release.zip",
        }

        self.dialog._on_update_checked(True, release)

        self.assertEqual(
            self.dialog.update_status_label.text(),
            f"You are on the latest version ({updater.CURRENT_VERSION}). No update needed.",
        )

    def test_update_check_shows_notes_and_requires_confirmation(self):
        release = {
            "current_version": updater.CURRENT_VERSION,
            "latest_version": "v1.6.0",
            "update_available": True,
            "notes": "A safer updater",
            "download_url": "https://example.invalid/release.zip",
        }

        with (
            patch.object(settings_dialog.QMessageBox, "exec", return_value=settings_dialog.QMessageBox.No),
            patch.object(settings_dialog.QMessageBox, "setInformativeText") as set_notes,
        ):
            self.dialog._on_update_checked(True, release)

        set_notes.assert_called_once_with("Changes in this release:\n\nA safer updater")
        self.assertEqual(
            self.dialog.update_status_label.text(),
            "Update cancelled. No files were changed.",
        )


if __name__ == "__main__":
    unittest.main()
