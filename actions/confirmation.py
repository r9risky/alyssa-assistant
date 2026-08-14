import codecs
import ctypes
import contextlib
import datetime
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from ctypes import wintypes
from functools import lru_cache

import pyautogui
import pyperclip
import requests
import send2trash

import config
import memory
import plugin_loader

try:
    import winreg
except ImportError:
    winreg = None

try:
    from PIL import ImageGrab
except ImportError:
    ImageGrab = None

_power_confirmation_callback = None


_critical_confirmation_callback = None


_tool_confirmation = threading.local()


class VoiceConfirmationRequired(Exception):
    """Raised internally when a tool has been deferred for spoken approval."""


@contextlib.contextmanager
def tool_confirmation_context(name: str, arguments: dict, approved: bool = False):
    """Makes the current tool call visible to the shared confirmation hook."""
    previous = getattr(_tool_confirmation, "current", None)
    _tool_confirmation.current = (name, dict(arguments), approved)
    try:
        yield
    finally:
        _tool_confirmation.current = previous


def set_power_confirmation_callback(callback):
    """Sets the callback that begins a spoken power-action confirmation."""
    global _power_confirmation_callback
    _power_confirmation_callback = callback


def set_critical_confirmation_callback(callback):
    """Sets the callback for spoken command and deletion approvals."""
    global _critical_confirmation_callback
    _critical_confirmation_callback = callback


def _confirm(description: str, force: bool = False) -> bool:
    """Asks for y/n confirmation. Normally only if CONFIRM_BEFORE_ACTIONS is
    enabled in config.py - but force=True (used by delete_file, run_command,
    and restart/shutdown) always asks regardless of that setting, since
    those are the actions that are genuinely hard to undo."""
    current = getattr(_tool_confirmation, "current", None)
    if current and current[2]:
        return True
    if not force and not config.CONFIRM_BEFORE_ACTIONS:
        return True
    if current and _critical_confirmation_callback is not None:
        name, arguments, _approved = current
        decision = _critical_confirmation_callback(name, description, arguments)
        if decision is None:
            raise VoiceConfirmationRequired
        return bool(decision)
    answer = input(f'About to: {description}\nProceed? [y/N] ').strip().lower()
    return answer == "y"
