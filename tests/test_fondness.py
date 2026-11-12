import datetime

import pytest

import fondness

DAY = datetime.date(2026, 11, 12)


@pytest.fixture(autouse=True)
def state_file(tmp_path, monkeypatch):
    monkeypatch.setattr(fondness, "STATE_FILE", str(tmp_path / "fondness.json"))


def test_starts_warm_and_grows_with_kind_words():
    assert fondness.score(DAY) == fondness.START_SCORE
    assert fondness.record_turn("what's the weather", DAY) == 31
    assert fondness.record_turn("thank you!", DAY) == 34
    assert fondness.record_turn("this stupid printer jammed", DAY) == 35  # not aimed at her


def test_daily_cap_and_rudeness():
    for _ in range(20):
        fondness.record_turn("thanks", DAY)
    assert fondness.score(DAY) == fondness.START_SCORE + fondness.DAILY_GAIN_CAP
    assert fondness.record_turn("you're so useless", DAY) == fondness.START_SCORE + fondness.DAILY_GAIN_CAP - fondness.RUDE_PENALTY
    next_day = DAY + datetime.timedelta(days=1)
    assert fondness.record_turn("hi", next_day) == fondness.START_SCORE + fondness.DAILY_GAIN_CAP - fondness.RUDE_PENALTY + 1


def test_drifts_down_after_a_long_absence():
    fondness.record_turn("hi", DAY)
    later = DAY + datetime.timedelta(days=fondness.GRACE_DAYS + 5)
    assert fondness.score(later) == 31 - 5


def test_levels_and_prompt_line():
    assert fondness.level(0)[0] == "just met"
    assert fondness.level(45)[0] == "friends"
    assert fondness.level(100)[0] == "best friends"
    assert fondness.prompt_line().startswith("\n\nRelationship: ")
