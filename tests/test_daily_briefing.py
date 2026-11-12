import datetime
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugins"))

import config
from plugins import daily_briefing as db


def _at(hour, minute=0):
    return datetime.datetime(2026, 11, 12, hour, minute)


def test_fires_once_a_day_inside_the_window(tmp_path):
    with (
        patch.object(db, "STATE_FILE", str(tmp_path / "state.json")),
        patch.object(config, "BRIEFING_ENABLED", True, create=True),
        patch.object(config, "BRIEFING_TIME", "08:30", create=True),
        patch.object(db, "give_briefing", return_value="Good morning!") as brief,
    ):
        assert db.check_watch(_at(8, 0)) is None          # too early
        assert db.check_watch(_at(8, 45)) == "Good morning!"
        assert db.check_watch(_at(9, 0)) is None          # already done today
        assert brief.call_count == 1


def test_skips_when_the_pc_starts_hours_late_or_disabled(tmp_path):
    with (
        patch.object(db, "STATE_FILE", str(tmp_path / "state.json")),
        patch.object(config, "BRIEFING_TIME", "08:00", create=True),
        patch.object(db, "give_briefing", return_value="hi"),
    ):
        with patch.object(config, "BRIEFING_ENABLED", True, create=True):
            assert db.check_watch(_at(22, 0)) is None
        with patch.object(config, "BRIEFING_ENABLED", False, create=True):
            assert db.check_watch(_at(8, 5)) is None


def test_briefing_leaves_out_parts_that_are_not_set_up():
    with (
        patch.object(db.weather, "get_weather", return_value="Manila: clear sky, 31°C."),
        patch.object(db.reminders, "list_reminders", return_value="Nothing due or coming up soon."),
        patch.object(db, "_calendar_part", return_value=""),
        patch.object(config, "BRIEFING_NEWS_TOPICS", "", create=True),
    ):
        text = db.give_briefing()
    assert "Manila: clear sky" in text
    assert "Nothing due" not in text

    with (
        patch.object(db.weather, "get_weather", return_value="I couldn't reach the weather service - x"),
        patch.object(db.reminders, "list_reminders", return_value="Your list is empty."),
        patch.object(db, "_calendar_part", return_value=""),
        patch.object(config, "BRIEFING_NEWS_TOPICS", "", create=True),
    ):
        assert "Settings" in db.give_briefing()
