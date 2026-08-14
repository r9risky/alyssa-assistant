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

from . import confirmation
from .apps_and_files import _friendly_file_name
from .confirmation import _confirm

def read_clipboard() -> str:
    """Reads and returns the current clipboard text content."""
    try:
        content = pyperclip.paste()
    except Exception as e:
        return f"Couldn't read the clipboard: {e}"
    if not content:
        return "The clipboard is empty."
    preview = content if len(content) <= 500 else content[:500] + "... (truncated)"
    return f"Clipboard contains: {preview}"


def set_clipboard(text: str) -> str:
    """Copies the given text to the clipboard."""
    if not _confirm(f"copy this to the clipboard: {text!r}"):
        return "Cancelled by user."
    try:
        pyperclip.copy(text)
    except Exception as e:
        return f"Couldn't set the clipboard: {e}"
    return "Copied to the clipboard."


def take_screenshot() -> str:
    """Takes a screenshot of the whole screen and saves it to the Pictures
    folder. Uses Pillow's ImageGrab directly rather than
    pyautogui.screenshot(), which routes through pyscreeze and has a known
    import failure on some Python/Pillow combos."""
    if ImageGrab is None:
        return "Couldn't take a screenshot: Pillow isn't installed (pip install Pillow)."
    if not _confirm("take a screenshot"):
        return "Cancelled by user."
    pictures_dir = os.path.join(os.path.expanduser("~"), "Pictures")
    os.makedirs(pictures_dir, exist_ok=True)
    filename = f"alyssa_screenshot_{time.strftime('%Y%m%d_%H%M%S')}.png"
    path = os.path.join(pictures_dir, filename)
    try:
        ImageGrab.grab().save(path)
    except Exception as e:
        return f"Couldn't take a screenshot: {e}"
    return f"Saved a screenshot to your Pictures folder as {_friendly_file_name(path)}."


def describe_screen(question: str = "") -> str:
    """Looks at what's currently on screen and describes it, or answers a
    specific question about it, using a vision-capable model (see
    OLLAMA_VISION_MODEL / GEMINI_MODEL in config.py). Captures a fresh
    screenshot in memory only - unlike take_screenshot(), nothing is ever
    written to disk here."""
    # Imported here, not at module load time, to avoid a circular import -
    # brain.py imports this module, so this resolves fine only once brain
    # has finished importing.
    import brain
    return brain.describe_screen_with_vision(question)


def click_screen_element(description: str, double_click: bool = False, confirmed: bool = False) -> str:
    """Finds a described UI element on screen (via the vision model) and
    clicks it - e.g. 'click the Send button', 'click the X to close that
    popup'. This is what lets Alyssa act on what she sees rather than
    just narrate it, at the cost of being approximate: a language model
    estimating pixel coordinates from a screenshot isn't as reliable as a
    real UI-element lookup, so it works best on clearly labeled, distinct
    targets and can miss small or ambiguous ones. Always confirmed first
    (same as run_command/delete_file) since a misplaced click is hard to
    predict the consequences of - it could hit anything."""
    if not confirmed:
        if confirmation._critical_confirmation_callback is None:
            return "Confirmation required: please ask the user to approve this click."
        decision = confirmation._critical_confirmation_callback(
            "click_screen_element",
            f"click on '{description}'",
            {"description": description, "double_click": double_click},
        )
        if decision is None:
            return "VOICE_CONFIRMATION_REQUIRED"
        if not decision:
            return "Cancelled by user."

    import brain
    point = brain.locate_screen_element_with_vision(description)
    if point is None:
        return f"I couldn't find '{description}' on screen."
    x_pct, y_pct = point
    screen_w, screen_h = pyautogui.size()
    x = int(screen_w * x_pct / 100)
    y = int(screen_h * y_pct / 100)
    try:
        if double_click:
            pyautogui.doubleClick(x, y)
        else:
            pyautogui.click(x, y)
    except Exception as e:
        return f"Found '{description}' but couldn't click it: {e}"
    return f"Clicked '{description}'."
