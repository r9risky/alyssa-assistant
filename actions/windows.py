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

from .apps_and_files import (
    _SC_MAXIMIZE, _SC_MINIMIZE, _close_foreground_window,
    _post_syscommand_to_foreground,
)
from .confirmation import _confirm

def minimize_window() -> str:
    """Minimizes the currently focused window."""
    if not _confirm("minimize the current window"):
        return "Cancelled by user."
    if not _post_syscommand_to_foreground(_SC_MINIMIZE):
        pyautogui.hotkey("win", "down")  # fallback: non-Windows, or no foreground window found
    return "Minimized the window."


def maximize_window() -> str:
    """Maximizes the currently focused window."""
    if not _confirm("maximize the current window"):
        return "Cancelled by user."
    if not _post_syscommand_to_foreground(_SC_MAXIMIZE):
        pyautogui.hotkey("win", "up")  # fallback: non-Windows, or no foreground window found
    return "Maximized the window."


def close_window() -> str:
    """Closes the currently focused window/application."""
    if not _confirm("close the current window"):
        return "Cancelled by user."
    if _close_foreground_window():
        return "Closed the window."
    # Fallback (non-Windows, or no foreground window found): simulate
    # Alt+F4. Not pyautogui.hotkey() - some apps don't register a hotkey()
    # burst as a real Alt+F4 unless Alt is actually held a moment first.
    # switch_window() below uses the same hold-tap-release pattern.
    pyautogui.keyDown("alt")
    pyautogui.press("f4")
    time.sleep(0.3)
    pyautogui.keyUp("alt")
    return "Closed the window."


def switch_window() -> str:
    """Switches focus to the previously active window (alt+tab). Left as a
    real Alt+Tab simulation on purpose - there's no quieter version that
    still brings something else to the screen."""
    if not _confirm("switch to the previous window"):
        return "Cancelled by user."
    pyautogui.keyDown("alt")
    pyautogui.press("tab")
    time.sleep(0.3)
    pyautogui.keyUp("alt")
    return "Switched windows."


def snap_window(side: str) -> str:
    """Snaps the currently focused window to one side of the screen. side:
    'left' or 'right'. Left as a real Win+Left/Right simulation - same
    reasoning as switch_window() above, the visible move IS the result."""
    side = side.strip().lower()
    if side not in ("left", "right"):
        return f"'{side}' isn't a side I recognize - use 'left' or 'right'."
    if not _confirm(f"snap the current window to the {side}"):
        return "Cancelled by user."
    pyautogui.hotkey("win", side)
    return f"Snapped the window to the {side}."


def show_desktop() -> str:
    """Minimizes all windows to show the desktop. Left as a real Win+D
    simulation - same reasoning as switch_window() above."""
    if not _confirm("show the desktop"):
        return "Cancelled by user."
    pyautogui.hotkey("win", "d")
    return "Showed the desktop."
