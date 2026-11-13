"""User-changed settings, stored in settings.json next to config.py.

config.py holds the defaults and is code; this file holds what the user picked in
Settings or the setup wizard. A missing or broken settings.json falls back to the
defaults instead of stopping Alyssa from starting.
"""
import json
import os
import sys
import threading

if getattr(sys, "frozen", False):
    _BASE_DIR = os.path.dirname(sys.executable)
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SETTINGS_FILE = os.path.join(_BASE_DIR, "settings.json")

_lock = threading.Lock()


def load(path: str | None = None) -> dict:
    """Saved settings, or {} if the file is missing or unreadable."""
    path = path or SETTINGS_FILE
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        print(f"[settings] Ignoring {os.path.basename(path)} (using defaults): {e}")
        return {}
    if not isinstance(data, dict):
        print(f"[settings] Ignoring {os.path.basename(path)} (not a JSON object); using defaults.")
        return {}
    return _migrate(data)


# Voice effects used to need a "DSP pipeline" switch hidden under Show advanced, so effects
# saved while it was off never played. They now play whenever they're set: keep those off.
_NEUTRAL_VOICE_EFFECTS = {
    "VOICE_PITCH_SEMITONES": 0.0, "VOICE_SPEED_FACTOR": 1.0, "VOICE_ROBOT_AMOUNT": 0,
    "VOICE_GLITCH_AMOUNT": 0, "VOICE_LAYERS_ENABLED": False,
}


def _migrate(data: dict) -> dict:
    """Bring an older settings.json up to date. The next save() writes the result."""
    if data.pop("VOICE_DSP_ENABLED", None) is False:
        data.update(_NEUTRAL_VOICE_EFFECTS)
    return data


def apply(namespace: dict, path: str | None = None, skip=()) -> list[str]:
    """Overlay saved settings onto config's globals. Only keys config already defines
    are applied, so a stale or hand-edited entry can't inject anything new."""
    applied = []
    for key, value in load(path).items():
        if key.isupper() and key in namespace and key not in skip:
            namespace[key] = value
            applied.append(key)
    return applied


def change(key: str, value) -> None:
    """Use a setting now and keep it after a restart (for Settings tabs that add-ons build)."""
    import config
    import startup_logging

    setattr(config, key, value)
    try:
        save({key: value})
    except OSError as e:
        startup_logging.warn_once(f"saving {key}", e)


def save(updates: dict, path: str | None = None) -> None:
    """Merge updates into settings.json atomically. Raises OSError on failure."""
    path = path or SETTINGS_FILE
    with _lock:
        data = load(path)
        data.update(updates)
        tmp_path = path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        except OSError:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise
