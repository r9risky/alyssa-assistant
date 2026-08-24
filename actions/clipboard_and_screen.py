import os
import time

from .desktop import pyautogui
import pyperclip

try:
    from PIL import ImageGrab
except ImportError:
    ImageGrab = None

from . import bridges, confirmation
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
    """Full-screen shot to Pictures via ImageGrab, not pyautogui.screenshot
    (pyscreeze import can fail on some Python/Pillow combos)."""
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
    """Describe screen or answer `question` (OLLAMA_VISION_MODEL / GEMINI_MODEL).
    In-memory screenshot only — never writes to disk."""
    return bridges.describe_screen(question)


def click_screen_element(description: str, double_click: bool = False, confirmed: bool = False) -> str:
    """Vision-locate then click (e.g. 'Send button'). Confirm first (like
    run_command/delete_file). locate_screen_element → % coords →
    click/doubleClick. Approximate; best on distinct labeled targets."""
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

    point = bridges.locate_screen_element(description)
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
