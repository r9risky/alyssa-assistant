"""
Reminders plugin - gives Alyssa "add_reminder" / "list_reminders" /
"complete_reminder" / "delete_reminder" abilities, e.g. "Alyssa, remind me
to call the dentist tomorrow at 3pm" or "Alyssa, what's on my list?".

Stored locally in reminders.json (next to this file), same plain-JSON,
atomic-write approach as memory.py - no calendar account, no OAuth, no sync.

Note: Alyssa only checks reminders when asked (or when the system prompt
naturally surfaces due ones - see list_reminders) - she has no background
scheduler, so she won't interrupt you unprompted the moment something
becomes due. Ask "what's on my list" / "anything due" and she'll tell you.
"""
import json
import os
import re
import sys
import threading
from datetime import datetime, timedelta

import config

if getattr(sys, "frozen", False):
    _BASE_DIR = os.path.dirname(sys.executable)
else:
    _BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _format_time(value: datetime) -> str:
    """Portable 12-hour time without a leading zero."""
    return value.strftime("%I:%M %p").lstrip("0")

REMINDERS_FILE = os.path.join(_BASE_DIR, "reminders.json")
_lock = threading.RLock()

# Very small natural-language time parser - covers the common spoken cases
# ("tomorrow", "tomorrow at 3pm", "in 2 hours", "at 5pm", "friday") without
# pulling in a heavy NLP dependency. Falls back to "no specific time" (a
# plain to-do with no due date) if nothing matches, which still works fine.
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _parse_time_of_day(text: str, base: datetime) -> datetime | None:
    match = re.search(r"\b(\d{1,2})(:(\d{2}))?\s*(am|pm)?\b", text)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(3) or 0)
    meridiem = match.group(4)
    if meridiem == "pm" and hour != 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    elif meridiem is None and hour < 8:
        # Bare small numbers ("at 3") without am/pm almost always mean
        # afternoon in casual speech - nudge to PM rather than 3am.
        hour += 12
    if not (0 <= hour <= 23) or not (0 <= minute <= 59):
        return None
    return base.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _parse_due(when: str, now: datetime = None) -> datetime | None:
    """Best-effort parse of a spoken due time into a datetime, or None if
    *when* is empty/unparseable (reminder is then just an undated to-do)."""
    now = now or datetime.now()
    text = (when or "").strip().lower()
    if not text:
        return None

    in_match = re.search(r"in\s+(\d+)\s*(minute|hour|day|week)s?", text)
    if in_match:
        amount, unit = int(in_match.group(1)), in_match.group(2)
        delta = {
            "minute": timedelta(minutes=amount),
            "hour": timedelta(hours=amount),
            "day": timedelta(days=amount),
            "week": timedelta(weeks=amount),
        }[unit]
        return now + delta

    base = now
    if "tomorrow" in text:
        base = now + timedelta(days=1)
    elif "today" in text or "tonight" in text:
        base = now
    else:
        for i, day in enumerate(_WEEKDAYS):
            if day in text:
                days_ahead = (i - now.weekday()) % 7
                days_ahead = days_ahead or 7  # "friday" on a Friday means next Friday
                base = now + timedelta(days=days_ahead)
                break

    at_time = _parse_time_of_day(text, base)
    if at_time:
        return at_time
    if base.date() != now.date():
        # A day was named but no clock time - default to 9am that day.
        return base.replace(hour=9, minute=0, second=0, microsecond=0)
    return None


def _read_file() -> list:
    if not os.path.exists(REMINDERS_FILE):
        return []
    try:
        with open(REMINDERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"(couldn't read reminders.json, starting fresh: {e})")
        return []
    return data if isinstance(data, list) else []


def _write_file(reminders: list):
    tmp_path = REMINDERS_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(reminders, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, REMINDERS_FILE)


def _load() -> list:
    with _lock:
        return _read_file()


def _save(reminders: list):
    with _lock:
        _write_file(reminders)


def add_reminder(task: str, when: str = "") -> str:
    task = (task or "").strip()
    if not task:
        return "I need to know what to remind you about."

    due = _parse_due(when)
    reminders = _load()
    entry = {
        "id": (max((r.get("id", 0) for r in reminders), default=0) + 1),
        "task": task,
        "due": due.isoformat() if due else None,
        "done": False,
        "created": datetime.now().isoformat(),
    }
    reminders.append(entry)
    _save(reminders)

    if due:
        day = due.strftime("%A") if due.date() != datetime.now().date() else "today"
        when_spoken = f"{day} at {_format_time(due)}"
        return f"Got it - I'll have '{task}' on your list for {when_spoken}."
    return f"Added '{task}' to your list."


def list_reminders(only_due: bool = False) -> str:
    reminders = [r for r in _load() if not r.get("done")]
    if not reminders:
        return "Your list is empty."

    now = datetime.now()
    window = timedelta(hours=max(0, int(getattr(config, "REMINDER_UPCOMING_WINDOW_HOURS", 24))))

    def is_relevant(r):
        if not only_due:
            return True
        if not r.get("due"):
            return False
        try:
            due = datetime.fromisoformat(r["due"])
        except ValueError:
            return False
        return due <= now + window

    relevant = [r for r in reminders if is_relevant(r)]
    if only_due and not relevant:
        return "Nothing due or coming up soon."

    lines = []
    for r in sorted(relevant, key=lambda r: r.get("due") or "9999"):
        label = r["task"]
        if r.get("due"):
            try:
                due = datetime.fromisoformat(r["due"])
                overdue = due < now
                when_spoken = f"{due.strftime('%A')} {_format_time(due)}"
                label += f" ({'overdue - was due' if overdue else 'due'} {when_spoken})"
            except ValueError:
                pass
        lines.append(f"#{r['id']}: {label}")

    prefix = "Due or coming up: " if only_due else "On your list: "
    return prefix + "; ".join(lines) + "."


def complete_reminder(task_or_id: str) -> str:
    reminders = _load()
    match = _find(reminders, task_or_id)
    if not match:
        return f"I couldn't find a reminder matching '{task_or_id}'."
    match["done"] = True
    _save(reminders)
    return f"Marked '{match['task']}' as done."


def delete_reminder(task_or_id: str) -> str:
    reminders = _load()
    match = _find(reminders, task_or_id)
    if not match:
        return f"I couldn't find a reminder matching '{task_or_id}'."
    reminders = [r for r in reminders if r is not match]
    _save(reminders)
    return f"Deleted '{match['task']}' from your list."


def _find(reminders: list, task_or_id: str):
    task_or_id = (task_or_id or "").strip()
    if task_or_id.lstrip("#").isdigit():
        target_id = int(task_or_id.lstrip("#"))
        for r in reminders:
            if r.get("id") == target_id and not r.get("done"):
                return r
        return None
    needle = task_or_id.lower()
    candidates = [r for r in reminders if not r.get("done") and needle in r["task"].lower()]
    return candidates[0] if candidates else None


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "add_reminder",
            "description": (
                "Adds something to the user's reminder list / to-do list, "
                "optionally with a due time - e.g. 'remind me to call the "
                "dentist tomorrow at 3pm', 'add milk to my list', 'remind "
                "me in an hour to check the oven'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "What to be reminded about, e.g. 'call the dentist'.",
                    },
                    "when": {
                        "type": "string",
                        "description": (
                            "When it's due, in the user's own words if they "
                            "gave one, e.g. 'tomorrow at 3pm', 'in 2 hours', "
                            "'friday'. Leave blank for an undated to-do."
                        ),
                    },
                },
                "required": ["task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_reminders",
            "description": (
                "Lists the user's reminders/to-dos - e.g. 'what's on my "
                "list', 'what do I have to do', 'any reminders?'. Set "
                "only_due to true for 'what's due' / 'anything coming up' "
                "specifically, rather than the full list."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "only_due": {
                        "type": "boolean",
                        "description": "True to show only overdue/upcoming items instead of everything.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_reminder",
            "description": (
                "Marks a reminder/to-do as done - e.g. 'I called the "
                "dentist, take that off my list', 'mark the milk one done'. "
                "Identify it by its spoken description or #id from a "
                "previous list_reminders result."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_or_id": {
                        "type": "string",
                        "description": "The task's description (or part of it) or its #id.",
                    },
                },
                "required": ["task_or_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_reminder",
            "description": "Removes a reminder/to-do entirely - e.g. 'delete the dentist reminder', 'never mind about the milk'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_or_id": {
                        "type": "string",
                        "description": "The task's description (or part of it) or its #id.",
                    },
                },
                "required": ["task_or_id"],
            },
        },
    },
]

FUNCTIONS = {
    "add_reminder": add_reminder,
    "list_reminders": list_reminders,
    "complete_reminder": complete_reminder,
    "delete_reminder": delete_reminder,
}
