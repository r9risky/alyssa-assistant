"""Lazy PyAutoGUI boundary: import actions without probing the display.
Loaded only when a desktop action runs."""

from __future__ import annotations

import importlib
import threading


class _LazyPyAutoGUI:
    """Proxy that loads PyAutoGUI on first use."""

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
