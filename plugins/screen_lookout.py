"""Proactive Screen Watching, Visual Lookout (`watch_for`), and Check-ins (`check-ins.md`).

Inspired by Capri Desktop's `Commentary.cs`, `Lookout.cs`, and `Care.cs`:
- `watch_for` tool: lets the user ask Alyssa to keep an eye out for something on screen
  (e.g., "tell me when the download finishes", "let me know every time my health is low").
- Periodic screen watching (`SCREEN_WATCH_ENABLED`): captures screen descriptions into the
  3-hour rolling visual memory buffer and offers brief, non-repetitive observations.
- Periodic pop-in check-ins (`CHECK_IN_ENABLED`): friendly water, stretch, eye-rest, and
  late-night check-ins from `check-ins.md`.
"""

from __future__ import annotations

import datetime
import re
import threading
import time

import actions
import config
import memory

TAB_TITLE = "Screen & Check-ins"
WATCH_INTERVAL_SECONDS = 20

_MAX_WATCHES = 5
_lock = threading.RLock()
_active_watches: list[dict] = []  # [{"text": str, "repeat": bool, "added_at": float}]
_last_screen_watch_time: float = 0.0
_last_screen_summary: str = ""
_last_check_in_time: float = 0.0
_check_in_categories = ["Water", "Stretch", "Look away", "Meals"]
_check_in_index: int = 0

# Each look is a vision call, so skip it while the screen hasn't changed. A small change (a
# progress bar) can hide under the threshold, so watches still re-check this often anyway.
WATCH_RECHECK_SECONDS = 60
_CHANGE_THRESHOLD = 2.0  # mean grey-level difference on a 32x18 thumbnail
_last_watch_look: tuple[bytes | None, float] = (None, 0.0)
_last_commentary_look: bytes | None = None


def _screen_fingerprint() -> bytes | None:
    """Tiny greyscale thumbnail of the screen; None if it can't be captured."""
    try:
        from PIL import ImageGrab

        return ImageGrab.grab().convert("L").resize((32, 18)).tobytes()
    except Exception:
        return None


def _changed(before: bytes | None, now: bytes | None) -> bool:
    if before is None or now is None or len(before) != len(now):
        return True
    return sum(abs(a - b) for a, b in zip(before, now)) / len(now) > _CHANGE_THRESHOLD


def _normalize_watch_text(text: str) -> str:
    cleaned = " ".join((text or "").strip().split())
    cleaned = re.sub(
        r"^(?:when|whenever|once|as soon as|if|whether|until|every time|each time)\s+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    return cleaned.strip(" .!?")


def get_active_watches() -> list[dict]:
    """Return a copy of currently active visual watches."""
    with _lock:
        return [dict(item) for item in _active_watches]


def clear_all_watches() -> int:
    """Clear all active visual watches and return how many were removed."""
    with _lock:
        count = len(_active_watches)
        _active_watches.clear()
        return count


def watch_for(
    condition: str = "",
    action: str = "watch",
    repeat: bool = False,
) -> str:
    """Watch the screen for a visual condition, list active watches, or stop watching."""
    act = (action or "watch").strip().lower()
    raw = (condition or "").strip()

    with _lock:
        if act == "list":
            if not _active_watches:
                return "I'm not watching for anything on your screen right now."
            items = [
                f"{item['text']} ({'repeating' if item.get('repeat') else 'once'})"
                for item in _active_watches
            ]
            return "Watching your screen for: " + "; ".join(items) + "."

        if act in ("stop", "clear", "remove", "cancel"):
            if not _active_watches:
                return "I wasn't watching for anything on your screen."
            if not raw or raw.lower() in ("all", "everything"):
                _active_watches.clear()
                return "Stopped watching your screen for those triggers."
            norm = _normalize_watch_text(raw).lower()
            kept = [
                w for w in _active_watches
                if norm not in w["text"].lower() and w["text"].lower() not in norm
            ]
            removed = len(_active_watches) - len(kept)
            if removed == 0:
                return f"I wasn't watching for '{raw}'."
            _active_watches[:] = kept
            return f"Stopped watching for '{raw}'."

        norm_text = _normalize_watch_text(raw)
        if not norm_text:
            return "What should I keep an eye out for?"

        for existing in _active_watches:
            if existing["text"].lower() == norm_text.lower():
                existing["repeat"] = bool(repeat or existing.get("repeat"))
                return f"I'm already keeping an eye out for: {existing['text']}."

        if len(_active_watches) >= _MAX_WATCHES:
            return f"I'm already watching for {_MAX_WATCHES} things on screen. Stop one first before adding more."

        _active_watches.append(
            {
                "text": norm_text,
                "repeat": bool(repeat),
                "added_at": time.time(),
            }
        )
        suffix = " every time it happens" if repeat else ""
        return f"Got it — I'll keep an eye out for '{norm_text}'{suffix}."


def evaluate_watches_against_description(screen_description: str) -> str | None:
    """Check if any active watch condition matches `screen_description`."""
    desc = (screen_description or "").strip()
    if not desc or desc.startswith("Couldn't"):
        return None
    desc_lower = desc.lower()
    with _lock:
        if not _active_watches:
            return None
        triggered_index = None
        for idx, item in enumerate(_active_watches):
            target = item["text"].lower()
            words = [w for w in re.findall(r"[a-z0-9]+", target) if len(w) > 2]
            if target in desc_lower or (words and all(w in desc_lower for w in words)):
                triggered_index = idx
                break
        if triggered_index is None:
            return None
        matched = _active_watches[triggered_index]
        if not matched.get("repeat"):
            _active_watches.pop(triggered_index)
        return f"Heads up — I spotted '{matched['text']}' on your screen."


def check_watch() -> str | None:
    """Background watcher tick for visual lookouts, screen commentary, and check-ins."""
    global _last_screen_watch_time, _last_screen_summary, _last_check_in_time, _check_in_index
    global _last_watch_look, _last_commentary_look

    if getattr(config, "QUIET_MODE", False):
        return None

    now = time.time()

    # 1. Active `watch_for` visual triggers
    watches = get_active_watches()
    fingerprint = None
    if watches:
        fingerprint = _screen_fingerprint()
        last_print, last_time = _last_watch_look
        if not _changed(last_print, fingerprint) and now - last_time < WATCH_RECHECK_SECONDS:
            watches = []  # same screen as last look: nothing new to spot
        else:
            _last_watch_look = (fingerprint, now)
    if watches:
        numbered = "; ".join(f"{i + 1}) {w['text']}" for i, w in enumerate(watches))
        question = (
            "Briefly describe what is on screen and state whether any of these conditions "
            f"is clearly visible right now: {numbered}"
        )
        screen_desc = actions.bridges.describe_screen(question)
        if screen_desc and not screen_desc.startswith("Couldn't"):
            memory.record_screen_observation(screen_desc)
            alert = evaluate_watches_against_description(screen_desc)
            if alert:
                return alert

    # 2. Periodic "Watch my screen" commentary + 3h visual memory buffer
    if getattr(config, "SCREEN_WATCH_ENABLED", False):
        interval = max(30, int(getattr(config, "SCREEN_WATCH_INTERVAL_SECONDS", 120)))
        if now - _last_screen_watch_time >= interval:
            _last_screen_watch_time = now
            fingerprint = fingerprint or _screen_fingerprint()
            summary = None
            if _changed(_last_commentary_look, fingerprint):
                _last_commentary_look = fingerprint
                summary = actions.bridges.describe_screen(
                    "In one short sentence, what is the user currently doing on screen?"
                )
            if summary and not summary.startswith("Couldn't"):
                memory.record_screen_observation(summary)
                normalized = summary.strip().lower()
                if normalized != _last_screen_summary:
                    _last_screen_summary = normalized
                    return summary.strip()

    # 3. Periodic "Check in on me" pop-ins (`check-ins.md`)
    if getattr(config, "CHECK_IN_ENABLED", False):
        check_interval = max(5, int(getattr(config, "CHECK_IN_INTERVAL_MINUTES", 45))) * 60.0
        if _last_check_in_time == 0.0:
            _last_check_in_time = now
        elif now - _last_check_in_time >= check_interval:
            _last_check_in_time = now
            local_hour = datetime.datetime.now().hour
            if local_hour >= 23 or local_hour < 4:
                category = "Sleep nudge"
            else:
                category = _check_in_categories[_check_in_index % len(_check_in_categories)]
                _check_in_index += 1
            line = memory.pick_check_in_line(category)
            if line:
                return line

    return None


def build_gui(parent=None):
    """Build the Settings tab widget for Screen Watching, Visual Lookout, and Check-ins."""
    from PySide6.QtWidgets import (
        QCheckBox,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QListWidget,
        QPlainTextEdit,
        QPushButton,
        QSpinBox,
        QVBoxLayout,
        QWidget,
    )

    panel = QWidget(parent)
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(12, 12, 12, 12)
    layout.setSpacing(10)

    quiet_check = QCheckBox("Quiet Mode (silence proactive screen commentary & check-ins)")
    quiet_check.setChecked(bool(getattr(config, "QUIET_MODE", False)))
    quiet_check.toggled.connect(lambda checked: setattr(config, "QUIET_MODE", bool(checked)))
    layout.addWidget(quiet_check)

    watch_check = QCheckBox("Watch my screen (periodic commentary & 3h visual memory)")
    watch_check.setChecked(bool(getattr(config, "SCREEN_WATCH_ENABLED", False)))
    watch_check.toggled.connect(lambda checked: setattr(config, "SCREEN_WATCH_ENABLED", bool(checked)))
    layout.addWidget(watch_check)

    interval_row = QHBoxLayout()
    interval_row.addWidget(QLabel("Screen glance interval (seconds):"))
    interval_spin = QSpinBox()
    interval_spin.setRange(30, 3600)
    interval_spin.setValue(int(getattr(config, "SCREEN_WATCH_INTERVAL_SECONDS", 120)))
    interval_spin.valueChanged.connect(
        lambda val: setattr(config, "SCREEN_WATCH_INTERVAL_SECONDS", int(val))
    )
    interval_row.addWidget(interval_spin)
    interval_row.addStretch(1)
    layout.addLayout(interval_row)

    caught_up_check = QCheckBox("Keep caught up (include last 3 hours of screen glances in context)")
    caught_up_check.setChecked(bool(getattr(config, "SCREEN_WATCH_KEEP_CAUGHT_UP", True)))
    caught_up_check.toggled.connect(
        lambda checked: setattr(config, "SCREEN_WATCH_KEEP_CAUGHT_UP", bool(checked))
    )
    layout.addWidget(caught_up_check)

    checkin_check = QCheckBox("Check in on me (periodic water, stretch, eye-rest & sleep nudges)")
    checkin_check.setChecked(bool(getattr(config, "CHECK_IN_ENABLED", False)))
    checkin_check.toggled.connect(lambda checked: setattr(config, "CHECK_IN_ENABLED", bool(checked)))
    layout.addWidget(checkin_check)

    checkin_row = QHBoxLayout()
    checkin_row.addWidget(QLabel("Check-in interval (minutes):"))
    checkin_spin = QSpinBox()
    checkin_spin.setRange(5, 240)
    checkin_spin.setValue(int(getattr(config, "CHECK_IN_INTERVAL_MINUTES", 45)))
    checkin_spin.valueChanged.connect(
        lambda val: setattr(config, "CHECK_IN_INTERVAL_MINUTES", int(val))
    )
    checkin_row.addWidget(checkin_spin)
    checkin_row.addStretch(1)
    layout.addLayout(checkin_row)

    layout.addWidget(QLabel("Active Visual Lookouts (`watch_for`):"))
    watch_list = QListWidget()
    watch_list.setMaximumHeight(90)
    layout.addWidget(watch_list)

    def _refresh_watches():
        watch_list.clear()
        for item in get_active_watches():
            mode = "repeat" if item.get("repeat") else "once"
            watch_list.addItem(f"{item['text']} [{mode}]")

    add_row = QHBoxLayout()
    watch_input = QLineEdit()
    watch_input.setPlaceholderText("e.g., download finishes, build succeeds...")
    add_btn = QPushButton("Add Watch")
    clear_btn = QPushButton("Clear All")
    add_row.addWidget(watch_input, 1)
    add_row.addWidget(add_btn)
    add_row.addWidget(clear_btn)
    layout.addLayout(add_row)

    def _on_add():
        txt = watch_input.text().strip()
        if txt:
            watch_for(condition=txt, action="watch")
            watch_input.clear()
            _refresh_watches()

    def _on_clear():
        clear_all_watches()
        _refresh_watches()

    add_btn.clicked.connect(_on_add)
    clear_btn.clicked.connect(_on_clear)
    _refresh_watches()

    layout.addWidget(QLabel("Custom Check-in Lines (`check-ins.md`):"))
    checkins_editor = QPlainTextEdit()
    checkins_editor.setPlainText(memory.load_check_in_lines())
    layout.addWidget(checkins_editor, 1)

    save_checkins_btn = QPushButton("Save Check-in Lines")
    save_checkins_btn.clicked.connect(
        lambda: memory.save_check_in_lines(checkins_editor.toPlainText())
    )
    layout.addWidget(save_checkins_btn)

    return panel


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "watch_for",
            "description": (
                "Keep an eye on the user's screen and alert them when a visual condition "
                "appears (e.g. 'tell me when the download finishes', 'let me know every time "
                "my health is low'), list active screen watches, or stop watching."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "condition": {
                        "type": "string",
                        "description": "What visual state or event to watch for on the screen.",
                    },
                    "action": {
                        "type": "string",
                        "description": "One of: 'watch' (default), 'list', or 'stop'.",
                        "enum": ["watch", "list", "stop"],
                    },
                    "repeat": {
                        "type": "boolean",
                        "description": "True if Alyssa should alert every time it happens instead of just once.",
                    },
                },
                "required": [],
            },
        },
    }
]

FUNCTIONS = {
    "watch_for": watch_for,
}
