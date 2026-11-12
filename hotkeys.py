"""Global Hotkey Manager for Alyssa Assistant.

Supports Capri Desktop global shortcuts:
- Talk / Wake (`HOTKEY_TALK`, default `ctrl+alt+v`)
- Mute / Unmute Mic (`HOTKEY_MUTE`, default `ctrl+alt+n`)
- Focus Chat Input (`HOTKEY_TYPE`, default `ctrl+alt+t`)
- Hide / Show Companion (`HOTKEY_HIDE`, default `ctrl+alt+h`)
- Toggle Quiet Mode (`HOTKEY_QUIET`, default `ctrl+alt+p`)
- Hop Off Cursor (`HOTKEY_HOP_OFF`, default `ctrl+alt+c`)

Uses Windows `GetAsyncKeyState` polling on Windows when available, and registers Qt
application-level shortcuts as a cross-platform fallback.
"""

from __future__ import annotations

import ctypes
import os
import threading
import time
import startup_logging
from collections.abc import Callable

import config

_VK_MAP: dict[str, int] = {
    "ctrl": 0x11,
    "control": 0x11,
    "alt": 0x12,
    "menu": 0x12,
    "shift": 0x10,
    "win": 0x5B,
    "space": 0x20,
    "tab": 0x09,
    "enter": 0x0D,
    "return": 0x0D,
    "esc": 0x1B,
    "escape": 0x1B,
}
for _ch_code in range(ord("a"), ord("z") + 1):
    _VK_MAP[chr(_ch_code)] = _ch_code - 32
for _digit_code in range(ord("0"), ord("9") + 1):
    _VK_MAP[chr(_digit_code)] = _digit_code
for _fn_idx in range(1, 13):
    _VK_MAP[f"f{_fn_idx}"] = 0x70 + (_fn_idx - 1)


def parse_hotkey_chord(chord_str: str) -> list[int]:
    """Convert a hotkey string like 'ctrl+alt+v' into virtual-key codes."""
    parts = [p.strip().lower() for p in (chord_str or "").split("+") if p.strip()]
    vks: list[int] = []
    for part in parts:
        vk = _VK_MAP.get(part)
        if vk is None:
            return []
        if vk not in vks:
            vks.append(vk)
    return vks


def format_qt_key_sequence(chord_str: str) -> str:
    """Normalize 'ctrl+alt+v' into Qt's 'Ctrl+Alt+V' format."""
    parts = [p.strip() for p in (chord_str or "").split("+") if p.strip()]
    if not parts:
        return ""
    norm = []
    for p in parts:
        low = p.lower()
        if low in ("ctrl", "control"):
            norm.append("Ctrl")
        elif low == "alt":
            norm.append("Alt")
        elif low == "shift":
            norm.append("Shift")
        else:
            norm.append(p.upper() if len(p) == 1 else p.capitalize())
    return "+".join(norm)


class GlobalHotkeyManager:
    """Manages global shortcut bindings and invokes registered action callbacks."""

    def __init__(self, callbacks: dict[str, Callable[[], None]] | None = None):
        self.callbacks: dict[str, Callable[[], None]] = dict(callbacks or {})
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._pressed_last: set[str] = set()
        self._qt_shortcuts: list = []
        self._qt_parent = None

    def get_bindings(self) -> dict[str, str]:
        """Return current action -> chord mapping from config."""
        return {
            "talk": str(getattr(config, "HOTKEY_TALK", "ctrl+alt+v") or ""),
            "mute": str(getattr(config, "HOTKEY_MUTE", "ctrl+alt+n") or ""),
            "type": str(getattr(config, "HOTKEY_TYPE", "ctrl+alt+t") or ""),
            "hide": str(getattr(config, "HOTKEY_HIDE", "ctrl+alt+h") or ""),
            "quiet": str(getattr(config, "HOTKEY_QUIET", "ctrl+alt+p") or ""),
            "hop_off": str(getattr(config, "HOTKEY_HOP_OFF", "ctrl+alt+c") or ""),
        }

    def trigger(self, action_name: str) -> bool:
        """Manually trigger a named hotkey action (used by tests, polling, or Qt shortcuts)."""
        if not getattr(config, "HOTKEYS_ENABLED", True):
            return False
        cb = self.callbacks.get(action_name)
        if cb is None:
            return False
        cb()
        return True

    def bind_qt_shortcuts(self, parent_widget) -> None:
        """Attach QShortcut instances to `parent_widget` for in-app responsiveness."""
        self._qt_parent = parent_widget
        try:
            from PySide6.QtCore import Qt
            from PySide6.QtGui import QKeySequence, QShortcut
        except ImportError:
            return
        for sc in self._qt_shortcuts:
            try:
                sc.setEnabled(False)
                sc.deleteLater()
            except Exception:
                pass
        self._qt_shortcuts.clear()
        if not getattr(config, "HOTKEYS_ENABLED", True):
            return
        for action_name, chord in self.get_bindings().items():
            seq = format_qt_key_sequence(chord)
            if not seq or action_name not in self.callbacks:
                continue
            shortcut = QShortcut(QKeySequence(seq), parent_widget)
            shortcut.setContext(Qt.ApplicationShortcut)
            shortcut.activated.connect(
                lambda act=action_name: self.trigger(act)
            )
            self._qt_shortcuts.append(shortcut)

    def reload_from_config(self) -> None:
        """Apply edited chords. The Win32 poller re-reads config each tick; Qt shortcuts need rebinding."""
        if self._qt_parent is not None:
            self.bind_qt_shortcuts(self._qt_parent)

    def start(self) -> None:
        """Start background Windows key-state polling if running on Windows."""
        if os.name != "nt" or self._thread is not None:
            return
        user32 = getattr(ctypes, "windll", None)
        if user32 is None or not hasattr(user32, "user32"):
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._poll_loop_win32, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._thread = None

    def _poll_loop_win32(self) -> None:
        user32 = ctypes.windll.user32
        while not self._stop_event.is_set():
            if not getattr(config, "HOTKEYS_ENABLED", True):
                time.sleep(0.1)
                continue
            bindings = self.get_bindings()
            currently_down: set[str] = set()
            for action_name, chord in bindings.items():
                vks = parse_hotkey_chord(chord)
                if not vks:
                    continue
                if all((user32.GetAsyncKeyState(vk) & 0x8000) != 0 for vk in vks):
                    currently_down.add(action_name)
                    if action_name not in self._pressed_last:
                        try:
                            self.trigger(action_name)
                        except Exception as e:
                            startup_logging.warn_once("hotkey action", e)
            self._pressed_last = currently_down
            time.sleep(0.04)
