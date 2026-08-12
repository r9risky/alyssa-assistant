"""
Timers & Stopwatch plugin for Alyssa.

Gives Alyssa abilities to:
- Set countdown timers with proactive spoken notifications ('start_timer')
- Check active timers ('list_timers')
- Cancel running timers ('cancel_timer')
- Start and stop a stopwatch ('start_stopwatch', 'stop_stopwatch')

Includes a check_watch() background watcher so when a timer elapses, Alyssa
spontaneously alerts the user: "Timer finished! Your 5 minute timer is up."
"""
import time

WATCH_INTERVAL_SECONDS = 5

_active_timers = []
_stopwatch_start = None
_timer_counter = 0


def start_timer(duration_minutes: float = 0.0, duration_seconds: float = 0.0, label: str = "") -> str:
    """Starts a countdown timer."""
    global _timer_counter
    total_secs = (float(duration_minutes or 0) * 60) + float(duration_seconds or 0)
    if total_secs <= 0:
        return "Please specify a valid duration for the timer (e.g. 5 minutes or 30 seconds)."

    _timer_counter += 1
    timer_id = _timer_counter
    end_time = time.time() + total_secs

    # Format human description
    parts = []
    mins = int(total_secs // 60)
    secs = int(total_secs % 60)
    if mins > 0:
        parts.append(f"{mins} minute{'s' if mins != 1 else ''}")
    if secs > 0:
        parts.append(f"{secs} second{'s' if secs != 1 else ''}")
    duration_str = " ".join(parts) if parts else f"{total_secs:.0f} seconds"

    clean_label = (label or "").strip()
    timer_obj = {
        "id": timer_id,
        "end_time": end_time,
        "duration_str": duration_str,
        "label": clean_label,
        "notified": False,
    }
    _active_timers.append(timer_obj)

    msg = f"Timer set for {duration_str}"
    if clean_label:
        msg += f" for '{clean_label}'"
    return msg + "."


def list_timers() -> str:
    """Lists all active countdown timers."""
    now = time.time()
    active = [t for t in _active_timers if t["end_time"] > now and not t["notified"]]
    if not active:
        return "You don't have any active timers."

    lines = [f"You have {len(active)} active timer(s):"]
    for t in active:
        rem = int(t["end_time"] - now)
        rem_mins = rem // 60
        rem_secs = rem % 60
        rem_str = []
        if rem_mins > 0:
            rem_str.append(f"{rem_mins}m")
        rem_str.append(f"{rem_secs}s")
        time_left = " ".join(rem_str)
        lbl = f" ('{t['label']}')" if t['label'] else ""
        lines.append(f"- Timer for {t['duration_str']}{lbl}: {time_left} remaining")

    return "\n".join(lines)


def cancel_timer(label: str = "") -> str:
    """Cancels active timers."""
    global _active_timers
    if not _active_timers:
        return "There are no active timers to cancel."

    clean_label = (label or "").strip().lower()
    if not clean_label:
        count = len(_active_timers)
        _active_timers.clear()
        return f"Cancelled all {count} active timer(s)."

    matching = [t for t in _active_timers if clean_label in t["label"].lower()]
    if not matching:
        return f"Couldn't find any timer matching '{label}'."

    _active_timers = [t for t in _active_timers if t not in matching]
    return f"Cancelled timer for '{matching[0]['label'] or matching[0]['duration_str']}'."


def start_stopwatch() -> str:
    """Starts or resets the stopwatch."""
    global _stopwatch_start
    _stopwatch_start = time.time()
    return "Stopwatch started!"


def stop_stopwatch() -> str:
    """Stops the stopwatch and reports elapsed time."""
    global _stopwatch_start
    if _stopwatch_start is None:
        return "The stopwatch hasn't been started yet. Say 'start stopwatch'."

    elapsed = time.time() - _stopwatch_start
    _stopwatch_start = None

    mins = int(elapsed // 60)
    secs = int(elapsed % 60)
    millis = int((elapsed - int(elapsed)) * 100)

    parts = []
    if mins > 0:
        parts.append(f"{mins} minute{'s' if mins != 1 else ''}")
    parts.append(f"{secs}.{millis:02d} seconds")

    return f"Stopwatch stopped at {' '.join(parts)}."


def check_watch() -> str | None:
    """Background watcher that announces completed timers."""
    global _active_timers
    now = time.time()
    expired = [t for t in _active_timers if now >= t["end_time"] and not t["notified"]]

    if not expired:
        return None

    # Mark notified and remove
    messages = []
    for t in expired:
        t["notified"] = True
        lbl = f" for '{t['label']}'" if t["label"] else ""
        messages.append(f"Timer finished! Your {t['duration_str']} timer{lbl} is up.")

    _active_timers = [t for t in _active_timers if not t["notified"]]
    return " ".join(messages)


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "start_timer",
            "description": "Sets a countdown timer, e.g. 'set a timer for 5 minutes', 'set a 30 second timer for boiling eggs'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "duration_minutes": {"type": "number", "description": "Duration in minutes (e.g. 5 or 0.5)."},
                    "duration_seconds": {"type": "number", "description": "Duration in seconds (e.g. 30)."},
                    "label": {"type": "string", "description": "Optional label or description (e.g. 'boiling eggs', 'pizza')."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_timers",
            "description": "Lists all active running countdown timers and their remaining time.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_timer",
            "description": "Cancels active countdown timers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "description": "Optional label of the timer to cancel. If omitted, cancels all timers."}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "start_stopwatch",
            "description": "Starts or resets a stopwatch timer.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stop_stopwatch",
            "description": "Stops the stopwatch and reports the total elapsed time.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

FUNCTIONS = {
    "start_timer": start_timer,
    "list_timers": list_timers,
    "cancel_timer": cancel_timer,
    "start_stopwatch": start_stopwatch,
    "stop_stopwatch": stop_stopwatch,
}
