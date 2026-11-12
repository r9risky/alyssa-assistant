import json

import pytest

import actions
from plugins import routines


@pytest.fixture
def calls(tmp_path, monkeypatch):
    monkeypatch.setattr(routines, "ROUTINES_FILE", str(tmp_path / "routines.json"))
    log = []
    fake = {name: (lambda name: lambda **kw: log.append((name, kw)) or f"did {name}")(name)
            for name in ("open_app", "close_window", "play_music", "set_volume_level",
                         "set_quiet_mode", "press_keys", "open_url")}
    monkeypatch.setattr(actions, "FUNCTIONS", fake)
    monkeypatch.setattr(routines.time, "sleep", lambda s: log.append(("sleep", s)))
    return log


def test_settings_text_round_trips(calls):
    text = "Gaming Mode: open discord; open steam; quiet mode on\nbad line\nempty:\n"
    parsed = routines.parse_text(text)
    assert parsed == {"gaming mode": ["open discord", "open steam", "quiet mode on"]}
    assert routines.parse_text(routines.format_text(parsed)) == parsed


def test_saying_the_name_runs_every_step_without_a_model(calls):
    routines.create_routine("gaming mode", ["open Discord", "volume 140", "quiet mode on",
                                            "open site example.com", "wait 99", "say GG", "dance"])
    reply = routines.handle_intent("start gaming mode", "Start gaming mode")
    assert calls[:4] == [
        ("open_app", {"app_name": "Discord"}),
        ("set_volume_level", {"percent": 100}),
        ("set_quiet_mode", {"enabled": True}),
        ("open_url", {"url": "https://example.com"}),
    ]
    assert calls[4] == ("sleep", routines.MAX_WAIT_SECONDS)
    assert reply.startswith("Gaming mode: did open_app.") and "GG." in reply and 'skipped "dance"' in reply
    assert routines.handle_intent("gaming mode on") and routines.handle_intent("the gaming mode routine")


def test_other_phrases_fall_through(calls):
    routines.create_routine("movie night", "close window; play lofi")
    assert routines.handle_intent("what's the weather") is None
    assert routines.handle_intent("movie") is None


def test_create_list_delete(calls):
    assert "needs a name" in routines.create_routine("x", [])
    routines.create_routine("Focus!", ["quiet mode on"])
    assert json.load(open(routines.ROUTINES_FILE)) == {"focus": ["quiet mode on"]}
    assert "focus (1 step)" in routines.list_routines()
    assert "Deleted" in routines.delete_routine("focus")
    assert "no routine" in routines.delete_routine("focus")
    assert routines.list_routines() == "You don't have any routines yet."


def test_corrupt_file_reads_as_empty(calls):
    open(routines.ROUTINES_FILE, "w").write("{nope")
    assert routines.load() == {}
