import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QMessageBox

import config

from ..rendering import BASE_H, BASE_W, MIN_W
from . import save_overlay_settings
from ..theming import _build_messagebox_style, _theme


class SettingsMixin:
    def open_settings(self, focus_gemini: bool = False):
        # Reuse an already-open Settings instead of spawning a second.
        existing = getattr(self, "_settings_dialog", None)
        if existing is not None:
            try:
                existing.refresh_tokens_today()
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
                # C++ object already destroyed (WA_DeleteOnClose) — open a fresh one.
                self._settings_dialog = None

        from ..settings_dialog import ConfigDialog

        dialog = ConfigDialog(self, focus_gemini=focus_gemini)
        # Keep a ref: show() returns immediately, unlike exec().
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
        """First launch with no brain: the setup wizard (brain, mic, voice)."""
        if getattr(self, "_setup_wizard_open", False):
            return
        from ..setup_wizard import SetupWizard

        self._setup_wizard_open = True
        try:
            wizard = SetupWizard(self, self.settings)
            accepted = wizard.exec() == SetupWizard.Accepted
        finally:
            self._setup_wizard_open = False
        if not accepted:
            if self.bubble:
                self.bubble.show_message(
                    "No rush! Right-click me and choose Settings when you're ready to give me a brain."
                )
            return
        if wizard.problems:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle("Setup")
            box.setStyleSheet(_build_messagebox_style(self.settings))
            box.setText("\n\n".join(wizard.problems))
            box.exec()
        elif self.bubble:
            self.bubble.show_message("All set! Give me a second to wake up properly...")

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
        if getattr(self, "mini_player", None) is not None:
            if theme_changed:
                self.mini_player.apply_theme()
            if changed & {"mini_player_enabled", "chatbox_enabled", "scale"}:
                self.mini_player.apply_enabled()
        if getattr(self, "hover_pill", None) is not None and theme_changed:
            self.hover_pill.apply_theme()
        if getattr(self, "call_panel", None) is not None and theme_changed:
            self.call_panel.apply_theme()
        if self.tray is not None and theme_changed:
            from ..app_shell import _build_app_icon

            self.tray.setIcon(_build_app_icon(theme=_theme(self.settings)))
        save_overlay_settings(self.settings)
