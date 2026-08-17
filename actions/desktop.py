"""Desktop automation boundary for Windows actions.

The rest of the application can import the actions package without immediately
initializing PyAutoGUI (which probes the desktop/display at import time).  The
actual dependency is loaded only when a desktop action is executed.
"""

from __future__ import annotations

import importlib
import threading


class _LazyPyAutoGUI:
    """Small module proxy that delays PyAutoGUI initialization until use."""

    def __init__(self) -> None:
        object.__setattr__(self, "_module", None)
        object.__setattr__(self, "_lock", threading.Lock())

    def _load(self):
        module = object.__getattribute__(self, "_module")
        if module is not None:
            return module
        lock = object.__getattribute__(self, "_lock")
        with lock:
            module = object.__getattribute__(self, "_module")
            if module is None:
                module = importlib.import_module("pyautogui")
                module.FAILSAFE = True
                object.__setattr__(self, "_module", module)
        return module

    def __getattr__(self, name):
        return getattr(self._load(), name)

    def __setattr__(self, name, value):
        if name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        setattr(self._load(), name, value)


pyautogui = _LazyPyAutoGUI()
