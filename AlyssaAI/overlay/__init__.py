"""Desktop companion overlay for Alyssa."""

import sys

from PySide6.QtCore import qInstallMessageHandler, QtMsgType


def _qt_message_filter(msg_type, context, message):
    """Filter out noisy DPI warning messages."""
    if "SetProcessDpiAwarenessContext" in message:
        return
    sys.stderr.write(message + "\n")
    if msg_type == QtMsgType.QtFatalMsg:
        sys.exit(1)


def _print_uncaught_exception(exc_type, exc_value, exc_tb):
    """Print uncaught exceptions in Qt event loops to console."""
    import traceback
    traceback.print_exception(exc_type, exc_value, exc_tb)


qInstallMessageHandler(_qt_message_filter)
sys.excepthook = _print_uncaught_exception

from .app_shell import run_with_assistant
from .rendering import (
    BASE_H, BASE_W, DEFAULT_OVERLAY_SETTINGS, load_overlay_settings,
    render_character, render_svg, save_overlay_settings,
)
from .settings_dialog import ConfigDialog, VoiceBrowserDialog
from .widgets import Bridge, ChatInputBar, CompanionWindow, SpeechBubble

__all__ = [
    "BASE_H", "BASE_W", "Bridge", "ChatInputBar", "CompanionWindow",
    "ConfigDialog", "DEFAULT_OVERLAY_SETTINGS", "SpeechBubble",
    "VoiceBrowserDialog", "load_overlay_settings", "render_character",
    "render_svg", "run_with_assistant", "save_overlay_settings",
]
