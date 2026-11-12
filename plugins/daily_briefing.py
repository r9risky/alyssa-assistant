"""
Daily briefing: once a day at BRIEFING_TIME she sums up the weather, today's
calendar, due reminders and news on BRIEFING_NEWS_TOPICS. Also on demand
("brief me", "what's my day look like").

Every part is optional: one that isn't set up or fails is left out.
"""
import datetime
import json
import os
import sys

import config

# plugins/ is on sys.path (plugin_loader); sibling imports are OK.
import news_digest
import reminders
import weather

WATCH_INTERVAL_SECONDS = 60
LATE_WINDOW_HOURS = 3  # turning the PC on later than this skips today's scheduled briefing
UNTRUSTED_OUTPUTS = {"give_briefing"}  # the news part comes from web search

if getattr(sys, "frozen", False):
    _BASE_DIR = os.path.dirname(sys.executable)
else:
    _BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_FILE = os.path.join(_BASE_DIR, "briefing_state.json")


def _briefing_time() -> datetime.time:
    try:
        return datetime.time.fromisoformat(str(getattr(config, "BRIEFING_TIME", "08:00")))
    except ValueError:
        return datetime.time(8, 0)


def _topics() -> str:
    return str(getattr(config, "BRIEFING_NEWS_TOPICS", "") or "")


def _calendar_part() -> str:
    try:
        import calendar_gmail
        # Only when already signed in: an unprompted briefing must never pop a sign-in window.
        if calendar_gmail._availability_check() or calendar_gmail._load_token_info() is None:
            return ""
        return calendar_gmail.get_todays_schedule()
    except Exception:
        return ""


def _weather_part() -> str:
    try:
        text = weather.get_weather()
    except Exception:
        return ""
    return "" if text.startswith("I ") else text  # her failure messages all start with "I"


def _reminders_part() -> str:
    try:
        text = reminders.list_reminders(only_due=True)
    except Exception:
        return ""
    return "" if text in ("Nothing due or coming up soon.", "Your list is empty.") else text


def _news_part() -> str:
    topics = _topics()
    if not topics.strip():
        return ""
    try:
        return news_digest.get_news_digest(topics).removeprefix("Here's your digest. ")
    except Exception:
        return ""


def give_briefing() -> str:
    hour = datetime.datetime.now().hour
    greeting = "Good morning!" if hour < 12 else "Good afternoon!" if hour < 18 else "Good evening!"
    parts = [p for p in (_weather_part(), _calendar_part(), _reminders_part(), _news_part()) if p]
    if not parts:
        return (
            f"{greeting} I don't have much to brief you on yet - set a weather location, "
            "add reminders, or pick news topics in Settings."
        )
    return f"{greeting} Here's your briefing. " + " ".join(parts)


def _last_briefing_date() -> str:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return str(json.load(f).get("last_date", ""))
    except (OSError, ValueError, AttributeError):
        return ""


def _save_briefing_date(day: datetime.date) -> None:
    try:
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"last_date": day.isoformat()}, f)
        os.replace(tmp, STATE_FILE)
    except OSError as e:
        print(f"(couldn't save briefing state: {e})")


def check_watch(now: datetime.datetime | None = None):
    """The day's briefing once, between BRIEFING_TIME and LATE_WINDOW_HOURS after it."""
    if not getattr(config, "BRIEFING_ENABLED", True):
        return None
    now = now or datetime.datetime.now()
    due = datetime.datetime.combine(now.date(), _briefing_time())
    if not due <= now < due + datetime.timedelta(hours=LATE_WINDOW_HOURS):
        return None
    if _last_briefing_date() == now.date().isoformat():
        return None  # survives restarts, so rebooting doesn't repeat it
    _save_briefing_date(now.date())
    return give_briefing()


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "give_briefing",
            "description": (
                "Gives the user's daily briefing: weather, today's calendar, due "
                "reminders and their news topics - e.g. 'brief me', 'morning briefing', "
                "'what does my day look like'."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

FUNCTIONS = {
    "give_briefing": give_briefing,
}
