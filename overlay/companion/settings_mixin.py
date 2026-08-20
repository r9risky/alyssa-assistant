import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QMessageBox

import config

from ..rendering import BASE_H, BASE_W, MIN_W
from . import save_overlay_settings
from ..theming import _build_messagebox_style, _theme


class SettingsMixin:
    def open_settings(self, focus_gemini: bool = False):
        # If Settings is already open, just bring it to front instead of
        # spawning a second one on top of it.
        existing = getattr(self, "_settings_dialog", None)
        if existing is not None:
            try:
                if existing.isMinimized():
                    existing.showNormal()
                else:
                    existing.show()
                existing.raise_()
                existing.activateWindow()
                existing.setFocus(Qt.ActiveWindowFocusReason)
                if sys.platform == "win32":
                    try:
                        import ctypes
                        hwnd = int(existing.winId())
                        ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                        ctypes.windll.user32.SetForegroundWindow(hwnd)
                    except Exception:
                        pass
                if focus_gemini:
                    existing.tabs.setCurrentIndex(0)
                    existing.provider_combo.setCurrentText("gemini")
                    existing.gemini_setup_hint.setVisible(True)
                    existing.gemini_key_edit.setFocus()
                return
            except RuntimeError:
                # The underlying C++ object was already destroyed (closed
                # since we last checked, e.g. via WA_DeleteOnClose) -
                # fall through and open a fresh one below.
                self._settings_dialog = None

        from ..settings_dialog import ConfigDialog

        dialog = ConfigDialog(self, focus_gemini=focus_gemini)
        # Keep a reference on self so Python doesn't garbage-collect the
        # dialog the moment this method returns - unlike exec()'s blocking
        # local event loop, show() returns immediately, so nothing else
        # would otherwise keep it alive.
        self._settings_dialog = dialog
        dialog.finished.connect(self._on_settings_dialog_finished)
        dialog.ensurePolished()
        dialog.updateGeometry()
        QTimer.singleShot(0, lambda: self._show_settings_dialog(dialog))

    def _show_settings_dialog(self, dialog):
        if getattr(self, "_settings_dialog", None) is not dialog:
            return
        dialog.show()
        dialog.finalize_initial_layout()
        QTimer.singleShot(0, dialog.finalize_initial_layout)
        dialog.raise_()
        dialog.activateWindow()

    def _on_settings_dialog_finished(self, *_args):
        self._settings_dialog = None

    def prompt_gemini_key_setup(self):
        """Friendly, beginner-guided version of 'you need an API key' -
        shown instead of a scary error box when Gemini is selected but no
        key is set yet, e.g. on first launch."""
        if self.bubble:
            self.bubble.show_message(
                "I need a free Gemini API key before I can think! "
                "Right-click me and choose Settings to add one →"
            )
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle(f"Let's get {config.ASSISTANT_NAME} set up")
        box.setStyleSheet(_build_messagebox_style(self.settings))
        box.setText(
            "One quick step before I can start listening: I need a free "
            "Gemini API key.\n\n"
            "1. Get a free key at aistudio.google.com/apikey\n"
            "2. Come back here and click \"Open Settings\" below\n"
            "3. Paste the key into \"Gemini API key\" (Assistant tab)\n"
            "That's it - it takes effect immediately, no restart or Save "
            "needed.\n\n"
            "(You can always get back to this screen later by "
            "right-clicking me and choosing Settings.)"
        )
        open_btn = box.addButton("Open Settings", QMessageBox.AcceptRole)
        box.addButton("I'll do it later", QMessageBox.RejectRole)
        box.setDefaultButton(open_btn)
        box.exec()
        if box.clickedButton() == open_btn:
            self.open_settings(focus_gemini=True)

    def apply_companion_settings(self, new_settings: dict):
        changed = {
            key for key, value in new_settings.items()
            if self.settings.get(key) != value
        }
        if not changed:
            return
        self.settings.update(new_settings)

        if "scale" in changed:
            w = max(MIN_W, int(BASE_W * self.settings.get("scale", 1.0)))
            self.resize(w, int(w * BASE_H / BASE_W))

        if changed & {"opacity", "dim_when_idle_enabled", "dim_when_idle_opacity"}:
            self._update_effective_opacity()

        if "always_on_top" in changed:
            self._set_always_on_top(
                bool(self.settings.get("always_on_top", True)), persist=False
            )

        if changed & {"scale", "talk_bounce_enabled", "talk_bounce_height"}:
            height = max(0, float(self.settings.get("talk_bounce_height", 0)))
            height *= self.settings.get("scale", 1.0)
            self._bounce_target = -height if (
                self._talking and self.settings.get("talk_bounce_enabled", True)
            ) else 0.0
            if self._bounce_target != self._bounce_offset and not self._bounce_anim_timer.isActive():
                self._bounce_anim_timer.start(20)

        if "talk_mouth_flap_enabled" in changed and not self.settings.get(
            "talk_mouth_flap_enabled", True
        ):
            self._mouth_open = False

        if changed & {
            "scale", "character_image", "character_image_talking",
            "talk_mouth_flap_enabled",
        }:
            self._update_pixmap()
        elif changed & {"color_theme", "dark_mode"}:
            self.update()

        theme_changed = bool(changed & {"color_theme", "dark_mode"})
        if self.bubble is not None and theme_changed:
            t = _theme(self.settings)
            self.bubble.label.setStyleSheet(f"QLabel {{ background: transparent; color: {t['bubble_text']}; }}")
            self.bubble.update()  # re-paint with the new theme if it's on screen right now
        if self.bubble is not None and "scale" in changed:
            self.bubble.reposition()
        if self.chatbar is not None:
            if theme_changed:
                self.chatbar.apply_theme()
            if changed & {"chatbox_enabled", "chatbox_position", "scale"}:
                self.chatbar.apply_enabled()
        if self.tray is not None and theme_changed:
            from ..app_shell import _build_app_icon

            self.tray.setIcon(_build_app_icon(theme=_theme(self.settings)))
        save_overlay_settings(self.settings)
