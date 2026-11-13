import pytest

import config
from plugins import focus_timer


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(focus_timer.time, "time", lambda: now[0])
    monkeypatch.setattr(focus_timer, "_state", None)
    monkeypatch.setattr(config, "QUIET_MODE", False)
    return now


def test_focus_break_cycle_and_quiet_mode(clock):
    assert "25 minutes" in focus_timer.handle_intent("start focus")
    assert config.QUIET_MODE is True
    assert focus_timer.check_watch() is None
    clock[0] += 25 * 60
    assert focus_timer.check_watch().startswith("Break time!")
    assert config.QUIET_MODE is False
    clock[0] += 5 * 60
    assert "Round 2 of 4" in focus_timer.check_watch() and config.QUIET_MODE is True
    assert "left of focus, round 2 of 4" in focus_timer.handle_intent("how long left on focus")
    assert focus_timer.handle_intent("stop focus") == "Focus session stopped."
    assert config.QUIET_MODE is False


def test_last_round_finishes_and_restores_quiet_mode(clock):
    config.QUIET_MODE = True  # was already quiet before focusing
    focus_timer.start_focus(minutes=10, rounds=1)
    clock[0] += 10 * 60
    assert "rounds done" in focus_timer.check_watch()
    assert config.QUIET_MODE is True and focus_timer._state is None


def test_phrases(clock):
    assert "50 minutes" in focus_timer.handle_intent("focus for 50 minutes")
    assert focus_timer.handle_intent("what's the focus of this essay") is None
    assert focus_timer.handle_intent("stop pomodoro") == "Focus session stopped."
    assert "no focus session" in focus_timer.handle_intent("stop focus").lower()


def test_quiet_mode_still_lets_timers_speak():
    import plugin_loader

    plugin_loader.load_plugins()
    always = {w["name"]: w["always"] for w in plugin_loader.get_watchers()}
    assert always["timers.py"] and always["focus_timer.py"] and not always["system_watch.py"]
