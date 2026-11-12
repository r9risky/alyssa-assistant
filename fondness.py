"""How close Alyssa feels to the user: a 0-100 score that grows as you talk.

- Each conversation turn adds a point, kind words add a little more, with a
  daily cap so spamming her doesn't max it out. Rudeness takes points away.
- Going quiet for a while lets it drift down slowly.
- The level shapes her tone (prompt_line) and shows as a heart on the hover pill.
"""
import datetime
import json
import os
import re
import sys
import threading

START_SCORE = 30  # "getting to know each other": warm, close to her old default tone
POINTS_PER_TURN = 1
KIND_BONUS = 2
RUDE_PENALTY = 3
DAILY_GAIN_CAP = 8
GRACE_DAYS = 2  # days away before it starts drifting down
DECAY_PER_DAY = 1

LEVELS = (  # (minimum score, name, how she talks)
    (80, "best friends", "You and the user are best friends: openly fond of them, playful, inside jokes, very casual."),
    (60, "close friends", "You and the user are close friends: affectionate and playful, happy to reference things you've done together."),
    (40, "friends", "You and the user are friends: relaxed and casual; light teasing is fine."),
    (20, "getting to know each other", "You're still getting to know the user: warm and curious about them."),
    (0, "just met", "You've only just met the user: friendly but a little polite and reserved."),
)

_KIND_RE = re.compile(
    r"\b(?:thanks|thank you|thank u|ty|love you|appreciate|good job|nice work|well done|"
    r"you'?re (?:the best|awesome|amazing|great|cute|sweet)|good girl)\b",
    re.IGNORECASE,
)
_RUDE_RE = re.compile(  # aimed at her, not "this stupid printer"
    r"\b(?:shut up|hate you|you(?:'re| are) (?:so |such an? )?(?:stupid|dumb|useless|an idiot|idiot|annoying|worthless))\b",
    re.IGNORECASE,
)

if getattr(sys, "frozen", False):
    _BASE_DIR = os.path.dirname(sys.executable)
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(_BASE_DIR, "fondness.json")
_lock = threading.Lock()


def _load() -> dict:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
        return {
            "score": max(0, min(100, int(state.get("score", START_SCORE)))),
            "last_day": str(state.get("last_day", "")),
            "gained_today": int(state.get("gained_today", 0)),
        }
    except (OSError, ValueError, TypeError, AttributeError):
        return {"score": START_SCORE, "last_day": "", "gained_today": 0}


def _save(state: dict) -> None:
    try:
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(tmp, STATE_FILE)
    except OSError as e:
        print(f"(couldn't save fondness: {e})")


def _decayed(state: dict, today: datetime.date) -> int:
    try:
        away = (today - datetime.date.fromisoformat(state["last_day"])).days
    except ValueError:
        return state["score"]
    return max(0, state["score"] - max(0, away - GRACE_DAYS) * DECAY_PER_DAY)


def score(today: datetime.date | None = None) -> int:
    return _decayed(_load(), today or datetime.date.today())


def level(value: int | None = None) -> tuple[str, str]:
    """(name, tone instruction) for a score (default: the current one)."""
    value = score() if value is None else value
    for minimum, name, tone in LEVELS:
        if value >= minimum:
            return name, tone
    return LEVELS[-1][1], LEVELS[-1][2]


def record_turn(user_text: str, today: datetime.date | None = None) -> int:
    """Count one exchange with the user; returns the new score."""
    today = today or datetime.date.today()
    with _lock:
        state = _load()
        current = _decayed(state, today)
        gained = state["gained_today"] if state["last_day"] == today.isoformat() else 0
        text = user_text or ""
        if _RUDE_RE.search(text):
            current -= RUDE_PENALTY
        else:
            gain = POINTS_PER_TURN + (KIND_BONUS if _KIND_RE.search(text) else 0)
            gain = max(0, min(gain, DAILY_GAIN_CAP - gained))
            current += gain
            gained += gain
        current = max(0, min(100, current))
        _save({"score": current, "last_day": today.isoformat(), "gained_today": gained})
        return current


def prompt_line() -> str:
    return "\n\nRelationship: " + level()[1]


def describe() -> str:
    value = score()
    return f"We're {level(value)[0]} - {value} out of 100."
