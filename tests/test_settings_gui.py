import os
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFormLayout, QLabel

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
        self.assistant_apply_mock = self.assistant_apply_patch.start()
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

    def test_credential_text_changes_wait_for_explicit_commit(self):
        self.dialog._assistant_apply_timer.stop()
        self.assistant_apply_mock.reset_mock()

        edit = self.dialog.openai_key_edit
        edit.setText("sk-part-one")
        edit.setText("sk-part-one-two")
        edit.setText("sk-complete-value")
        self.app.processEvents()

        self.assertFalse(self.dialog._assistant_apply_timer.isActive())
        self.assistant_apply_mock.assert_not_called()
        self.assertIn(
            "Unsaved", self.dialog.openai_key_status_label.text()
        )

        edit.editingFinished.emit()
        self.assistant_apply_mock.assert_called_once_with()

        self.assistant_apply_mock.reset_mock()
        self.dialog.anthropic_key_edit.setText("anthropic-complete-value")
        self.dialog.apply_btn.click()
        self.assistant_apply_mock.assert_called_once_with()

        self.assistant_apply_mock.reset_mock()
        youtube = self.dialog.youtube_key_edit
        youtube.setText("youtube-complete-value")
        youtube._verified_value = youtube.text().strip()
        self.dialog._on_key_verification_result(
            "youtube", True, "", []
        )
        self.assistant_apply_mock.assert_called_once_with()


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

    def test_system_watch_controls_apply_and_reload_plugins(self):
        original = (
            config.SYSTEM_WATCH_ENABLED, config.SYSTEM_WATCH_INTERVAL_SECONDS
        )
        enabled = not original[0]
        interval = original[1] + 10
        try:
            self.dialog.system_watch_enabled_check.setChecked(enabled)
            self.dialog.system_watch_interval_spin.setValue(interval)
            with (
                patch.object(settings_dialog, "_atomic_write_text") as write_text,
                patch.object(self.dialog, "_reload_plugins_backend") as reload_plugins,
            ):
                self.real_assistant_apply(self.dialog)

            saved = write_text.call_args.args[1]
            self.assertIn(f"SYSTEM_WATCH_ENABLED = {enabled}", saved)
            self.assertIn(f"SYSTEM_WATCH_INTERVAL_SECONDS = {interval}", saved)
            self.assertEqual(config.SYSTEM_WATCH_ENABLED, enabled)
            self.assertEqual(config.SYSTEM_WATCH_INTERVAL_SECONDS, interval)
            reload_plugins.assert_called_once_with()
        finally:
            config.SYSTEM_WATCH_ENABLED, config.SYSTEM_WATCH_INTERVAL_SECONDS = original

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
            patch.object(settings_dialog.QMessageBox, "setTextFormat") as set_format,
        ):
            self.dialog._on_update_checked(True, release)

        set_format.assert_called_once_with(Qt.RichText)
        set_notes.assert_called_once_with(
            "<p><b>Changes in this release:</b></p><p>A safer updater</p>"
        )
        self.assertEqual(
            self.dialog.update_status_label.text(),
            "Update cancelled. No files were changed.",
        )


    def _reload_with_plugin_source(self, temp_dir: str, filename: str, source: str):
        """Write plugin source, reload, rebuild GUI tabs (same as Settings > Plugins > Save)."""
        with open(os.path.join(temp_dir, filename), "w", encoding="utf-8") as f:
            f.write(source)
        settings_dialog.actions.reload_plugins()
        self.dialog._rebuild_plugin_gui_tabs()

    def test_plugin_can_register_its_own_settings_tab(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(settings_dialog.plugin_loader, "PLUGINS_DIR", temp_dir):
                self._reload_with_plugin_source(
                    temp_dir, "hello_gui.py",
                    "from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel\n"
                    "GUI_PANEL_TITLE = 'Hello GUI'\n"
                    "def build_gui_panel(parent):\n"
                    "    w = QWidget()\n"
                    "    layout = QVBoxLayout(w)\n"
                    "    label = QLabel('hi')\n"
                    "    label.setObjectName('helloGuiLabel')\n"
                    "    layout.addWidget(label)\n"
                    "    return w\n",
                )
                try:
                    labels = [
                        self.dialog.tabs.navigation.item(i).text()
                        for i in range(self.dialog.tabs.navigation.count())
                    ]
                    self.assertIn("Hello GUI", labels)
                    self.assertEqual(self.dialog._plugin_gui_tab_count, 1)
                    label_widget = self.dialog.findChild(QLabel, "helloGuiLabel")
                    self.assertIsNotNone(label_widget)
                    self.assertEqual(label_widget.text(), "hi")

                    # Disabling the plugin (rename to _hello_gui.py, the
                    # same convention the Plugins tab's Enable toggle
                    # uses) and reloading should drop its tab again.
                    os.rename(
                        os.path.join(temp_dir, "hello_gui.py"),
                        os.path.join(temp_dir, "_hello_gui.py"),
                    )
                    settings_dialog.actions.reload_plugins()
                    self.dialog._rebuild_plugin_gui_tabs()
                    labels = [
                        self.dialog.tabs.navigation.item(i).text()
                        for i in range(self.dialog.tabs.navigation.count())
                    ]
                    self.assertNotIn("Hello GUI", labels)
                    self.assertEqual(self.dialog._plugin_gui_tab_count, 0)
                finally:
                    settings_dialog.actions.reload_plugins()
                    self.dialog._rebuild_plugin_gui_tabs()

    def test_broken_plugin_panel_shows_inline_error_instead_of_crashing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(settings_dialog.plugin_loader, "PLUGINS_DIR", temp_dir):
                self._reload_with_plugin_source(
                    temp_dir, "broken_gui.py",
                    "GUI_PANEL_TITLE = 'Broken Panel'\n"
                    "def build_gui_panel(parent):\n"
                    "    raise RuntimeError('boom')\n",
                )
                try:
                    labels = [
                        self.dialog.tabs.navigation.item(i).text()
                        for i in range(self.dialog.tabs.navigation.count())
                    ]
                    self.assertIn("Broken Panel", labels)
                    self.assertEqual(self.dialog._plugin_gui_tab_count, 1)
                    page = self.dialog.tabs.pages.widget(
                        self.dialog.tabs.count() - 1
                    )
                    error_label = page.findChild(settings_dialog._StatusLabel)
                    self.assertIn("boom", error_label.text())
                finally:
                    settings_dialog.actions.reload_plugins()
                    self.dialog._rebuild_plugin_gui_tabs()


    def test_model_combos_populated_with_defaults(self):
        gemini_combo = self.dialog.gemini_model_edit
        openai_combo = self.dialog.openai_model_edit
        anthropic_combo = self.dialog.anthropic_model_edit
        groq_combo = self.dialog.groq_model_edit
        custom_combo = self.dialog.custom_model_edit

        # All dropdowns must be editable so users can type custom model names
        self.assertTrue(gemini_combo.isEditable())
        self.assertTrue(openai_combo.isEditable())
        self.assertTrue(anthropic_combo.isEditable())
        self.assertTrue(groq_combo.isEditable())
        self.assertTrue(custom_combo.isEditable())

        # Curated default models must be populated out of the box
        self.assertGreater(gemini_combo.count(), 1)
        self.assertGreater(openai_combo.count(), 1)
        self.assertGreater(anthropic_combo.count(), 1)
        self.assertGreater(groq_combo.count(), 1)
        self.assertGreater(custom_combo.count(), 1)

        # Current selection must match config / initial values
        self.assertEqual(gemini_combo.currentText(), config.GEMINI_MODEL)
        self.assertEqual(groq_combo.currentText(), getattr(config, "STT_GROQ_MODEL", "whisper-large-v3-turbo"))

        # Check that standard models exist in the dropdowns
        gemini_items = [gemini_combo.itemText(i) for i in range(gemini_combo.count())]
        self.assertIn("gemini-3.5-flash-lite", gemini_items)
        self.assertNotIn("gemini-1.5-flash", gemini_items)  # retired

        openai_items = [openai_combo.itemText(i) for i in range(openai_combo.count())]
        self.assertIn("gpt-5-mini", openai_items)

        anthropic_items = [anthropic_combo.itemText(i) for i in range(anthropic_combo.count())]
        self.assertIn("claude-sonnet-5", anthropic_items)

        groq_items = [groq_combo.itemText(i) for i in range(groq_combo.count())]
        self.assertIn("whisper-large-v3-turbo", groq_items)

        # Test quiet dynamic model enrichment preserves selection and does not disrupt status
        self.dialog._on_quiet_models_loaded("gemini", ["gemini-live-1", "gemini-live-2"])
        new_gemini_items = [gemini_combo.itemText(i) for i in range(gemini_combo.count())]
        self.assertIn("gemini-live-1", new_gemini_items)
        self.assertEqual(gemini_combo.currentText(), config.GEMINI_MODEL)


if __name__ == "__main__":
    unittest.main()
