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
