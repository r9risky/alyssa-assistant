import sys

from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QWidget

from . import save_overlay_settings


class WindowMixin:
    def _apply_windows_topmost(self, window: QWidget, on: bool) -> bool:
        if sys.platform != "win32" or not window.isVisible():
            return False
        try:
            import ctypes
            hwnd = int(window.winId())
            insert_after = -1 if on else -2  # HWND_TOPMOST / HWND_NOTOPMOST
            flags = 0x0001 | 0x0002 | 0x0010 | 0x0200  # NOSIZE|NOMOVE|NOACTIVATE|NOOWNERZORDER
            return bool(ctypes.windll.user32.SetWindowPos(hwnd, insert_after, 0, 0, 0, 0, flags))
        except Exception:
            return False

    def _set_always_on_top(self, on: bool, persist: bool = True):
        """Change Z-order without losing the overlay's geometry or visibility."""
        on = bool(on)
        self.settings["always_on_top"] = on
        for window in (self, self.bubble, self.chatbar):
            if window is None or self._apply_windows_topmost(window, on):
                continue
            geometry = QRect(window.geometry())
            was_visible = window.isVisible()
            window.setWindowFlag(Qt.WindowStaysOnTopHint, on)
            window.setGeometry(geometry)
            if was_visible:
                window.show()
        if persist:
            save_overlay_settings(self.settings)

    def showEvent(self, event):
        super().showEvent(event)
        if sys.platform == "win32":
            QTimer.singleShot(
                0,
                lambda: self._apply_windows_topmost(
                    self,
                    bool(self.settings.get("always_on_top", True))
                ),
            )

    def _reset_position_only(self):
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(screen.left() + 24, screen.bottom() - self.height() - 24)
