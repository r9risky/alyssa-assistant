import time

from .desktop import pyautogui
import pyperclip

from .confirmation import _confirm

def _type(text: str, interval: float = 0.02):
    """Type at cursor.

    - ASCII: typewrite (US keyboard only; KeyError on non-ASCII).
    - Else: clipboard paste (any Unicode).
    - Check ASCII first — never try/except around typewrite(). It types
      char-by-char; mid-string KeyError + paste duplicates the prefix
      ("hello"+bad+"world" → "hellohello world")."""
    if text.isascii():
        pyautogui.typewrite(text, interval=interval)
        return

    previous_clipboard = None
    try:
        previous_clipboard = pyperclip.paste()
    except Exception:
        pass  # clipboard read can fail; non-fatal

    pyperclip.copy(text)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.1)  # let paste land before restoring clipboard

    if previous_clipboard is not None:
        try:
            pyperclip.copy(previous_clipboard)
        except Exception:
            pass


def type_text(text: str) -> str:
    """Types text at the current cursor location."""
    if text is None:
        return "I need some text before I can type it."
    if not _confirm(f"type: {text!r}"):
        return "Cancelled by user."
    _type(text)
    return "Typed the text."


_KEY_ALIASES = {
    "control": "ctrl", "windows": "win", "window": "win", "super": "win",
    "cmd": "win", "command": "win", "return": "enter", "esc": "escape",
    "del": "delete", "ins": "insert", "pgup": "pageup", "pgdn": "pagedown",
    "spacebar": "space", "plus": "+", "minus": "-",
}


def press_keys(keys: str) -> str:
    """Presses a key combo, e.g. 'ctrl+s' or 'alt+tab'."""
    if not _confirm(f"press keys: {keys}"):
        return "Cancelled by user."
    key_list = [k.strip().lower() for k in keys.split("+") if k.strip()]
    key_list = [_KEY_ALIASES.get(k, k) for k in key_list]
    if not key_list:
        return f"'{keys}' isn't a key combo I can press."
    pyautogui.hotkey(*key_list)
    return f"Pressed {keys}."
