import pyautogui

from .confirmation import _confirm

def media_play_pause() -> str:
    """Toggles play/pause on whatever media is currently active."""
    if not _confirm("toggle play/pause"):
        return "Cancelled by user."
    pyautogui.press("playpause")
    return "Toggled play/pause."


def media_next_track() -> str:
    """Skips to the next media track."""
    if not _confirm("skip to the next track"):
        return "Cancelled by user."
    pyautogui.press("nexttrack")
    return "Skipped to the next track."


def media_previous_track() -> str:
    """Goes back to the previous media track."""
    if not _confirm("go to the previous track"):
        return "Cancelled by user."
    pyautogui.press("prevtrack")
    return "Went back to the previous track."


def volume_up(steps: int = 2) -> str:
    """Turns the system volume up. Each step is roughly a 2% increase."""
    steps = max(1, min(int(steps), 20))
    if not _confirm(f"turn the volume up ({steps} step(s))"):
        return "Cancelled by user."
    for _ in range(steps):
        pyautogui.press("volumeup")
    return "Turned the volume up."


def volume_down(steps: int = 2) -> str:
    """Turns the system volume down. Each step is roughly a 2% decrease."""
    steps = max(1, min(int(steps), 20))
    if not _confirm(f"turn the volume down ({steps} step(s))"):
        return "Cancelled by user."
    for _ in range(steps):
        pyautogui.press("volumedown")
    return "Turned the volume down."


def toggle_mute() -> str:
    """Mutes or unmutes system audio."""
    if not _confirm("toggle mute"):
        return "Cancelled by user."
    pyautogui.press("volumemute")
    return "Toggled mute."


def set_volume_level(percent: int = 50) -> str:
    """Sets system volume to a specific percentage (0 to 100%)."""
    try:
        pct = max(0, min(100, int(percent)))
    except (ValueError, TypeError):
        return f"Invalid volume percentage: '{percent}'."

    if not _confirm(f"set system volume to {pct}%"):
        return "Cancelled by user."

    for _ in range(50):
        pyautogui.press("volumedown")

    up_steps = int(pct / 2)
    for _ in range(up_steps):
        pyautogui.press("volumeup")

    return f"Set volume to {pct}%."
