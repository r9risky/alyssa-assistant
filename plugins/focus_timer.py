"""Focus timer: focus blocks with short breaks in between (25/5 by default).

She turns quiet mode on while you focus (no check-ins or chatter), calls the
break, and calls you back. Say "start focus", "focus for 50 minutes",
"how long left on focus" or "stop focus": no model call needed.
"""
import re
import time

import config

WATCH_INTERVAL_SECONDS = 10
ANNOUNCE_IN_QUIET_MODE = True  # her own break calls still speak while quiet mode is on

_state = None


def _minutes(value, default):
    try:
        return max(1.0, min(240.0, float(value)))
    except (TypeError, ValueError):
        return default


def _left(seconds: float) -> str:
    mins = max(0, round(seconds / 60))
    return f"{mins} minute{'s' if mins != 1 else ''}" if mins else "less than a minute"


def start_focus(minutes: float = 25, break_minutes: float = 5, rounds: int = 4, **kwargs) -> str:
    global _state
    focus, rest = _minutes(minutes, 25.0), _minutes(break_minutes, 5.0)
    try:
        rounds = max(1, min(12, int(rounds)))
    except (TypeError, ValueError):
        rounds = 4
    was_quiet = _state["was_quiet"] if _state else bool(getattr(config, "QUIET_MODE", False))
    _state = {"phase": "focus", "ends": time.time() + focus * 60, "round": 1, "rounds": rounds,
              "focus": focus, "break": rest, "was_quiet": was_quiet}
    config.QUIET_MODE = True
    return f"Focus on: {focus:g} minutes, then a {rest:g} minute break, {rounds} rounds. I'll keep quiet till then."


def stop_focus(**kwargs) -> str:
    global _state
    if _state is None:
        return "There's no focus session running."
    config.QUIET_MODE = _state["was_quiet"]
    _state = None
    return "Focus session stopped."


def focus_status(**kwargs) -> str:
    if _state is None:
        return "No focus session right now. Say \"start focus\" to begin one."
    left = _left(_state["ends"] - time.time())
    what = "focus" if _state["phase"] == "focus" else "your break"
    return f"{left.capitalize()} left of {what}, round {_state['round']} of {_state['rounds']}."


def check_watch():
    """Called every few seconds: switches between focus and break when the time is up."""
    global _state
    if _state is None or time.time() < _state["ends"]:
        return None
    s = _state
    if s["phase"] == "focus":
        if s["round"] >= s["rounds"]:
            config.QUIET_MODE = s["was_quiet"]
            _state = None
            return f"That's all {s['rounds']} focus rounds done. Nice work!"
        s.update(phase="break", ends=time.time() + s["break"] * 60)
        config.QUIET_MODE = s["was_quiet"]
        return f"Break time! Take {s['break']:g} minutes, I'll call you back."
    s.update(phase="focus", ends=time.time() + s["focus"] * 60, round=s["round"] + 1)
    config.QUIET_MODE = True
    return f"Break's over. Round {s['round']} of {s['rounds']}, back to it!"


_START_RE = re.compile(
    r"^(?:start|begin|let's|lets)?\s*(?:a\s+)?(?:focus|pomodoro)(?:\s+(?:session|mode|timer))?"
    r"(?:\s+(?:for\s+)?(\d{1,3})\s*(?:min|mins|minutes))?$"
)
_STOP_RE = re.compile(r"^(?:stop|end|cancel|quit)\s+(?:the\s+)?(?:focus|pomodoro)(?:\s+(?:session|mode|timer))?$")
_STATUS_RE = re.compile(r"^how (?:much time|long)(?: is)? left (?:on|in|of) (?:my |the )?(?:focus|pomodoro|break)")


def handle_intent(cleaned: str, text: str = ""):
    phrase = " ".join(re.sub(r"[^\w\s']", " ", (cleaned or "").lower()).split())
    if m := _START_RE.match(phrase):
        return start_focus(minutes=m.group(1) or 25)
    if _STOP_RE.match(phrase):
        return stop_focus()
    if _STATUS_RE.match(phrase):
        return focus_status()
    return None


INSTANT_REPLY_TOOLS = {"start_focus", "stop_focus", "focus_status"}
FUNCTIONS = {"start_focus": start_focus, "stop_focus": stop_focus, "focus_status": focus_status}
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "start_focus",
            "description": "Start a focus (pomodoro) session: focus blocks with short breaks; she stays quiet while the user focuses.",
            "parameters": {
                "type": "object",
                "properties": {
                    "minutes": {"type": "number", "description": "Focus block length, default 25"},
                    "break_minutes": {"type": "number", "description": "Break length, default 5"},
                    "rounds": {"type": "integer", "description": "How many focus blocks, default 4"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stop_focus",
            "description": "Stop the running focus (pomodoro) session.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "focus_status",
            "description": "How long is left in the current focus session or break.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]
