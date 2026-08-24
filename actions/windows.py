import time

from .desktop import pyautogui

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
    # Fallback Alt+F4: hold-tap-release, not hotkey() (some apps ignore a burst).
    pyautogui.keyDown("alt")
    pyautogui.press("f4")
    time.sleep(0.3)
    pyautogui.keyUp("alt")
    return "Closed the window."


def switch_window() -> str:
    """Alt+Tab to the previous window. Must be a real key simulation."""
    if not _confirm("switch to the previous window"):
        return "Cancelled by user."
    pyautogui.keyDown("alt")
    pyautogui.press("tab")
    time.sleep(0.3)
    pyautogui.keyUp("alt")
    return "Switched windows."


def snap_window(side: str) -> str:
    """Snap focused window: side is 'left' or 'right' (Win+Left/Right)."""
    side = side.strip().lower()
    if side not in ("left", "right"):
        return f"'{side}' isn't a side I recognize - use 'left' or 'right'."
    if not _confirm(f"snap the current window to the {side}"):
        return "Cancelled by user."
    pyautogui.hotkey("win", side)
    return f"Snapped the window to the {side}."


def show_desktop() -> str:
    """Minimize all windows (Win+D)."""
    if not _confirm("show the desktop"):
        return "Cancelled by user."
    pyautogui.hotkey("win", "d")
    return "Showed the desktop."
