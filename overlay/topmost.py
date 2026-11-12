"""Keeps her little windows above everything else, including fullscreen apps."""
import sys

from PySide6.QtCore import QTimer



class _TopmostWindowMixin:
    """Re-apply the Windows topmost flag after a tool window is shown."""

    def showEvent(self, event):
        super().showEvent(event)
        if sys.platform == "win32":
            QTimer.singleShot(
                0,
                lambda: self.companion._apply_windows_topmost(
                    self, bool(self.settings.get("always_on_top", True))
                ),
            )
