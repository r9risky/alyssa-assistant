import json
import threading
from unittest import mock

import actions
import plugins.game_autoplay as autoplay


def _reply(**kw):
    base = {"thought": "", "say": "", "done": False, "actions": []}
    base.update(kw)
    return json.dumps(base)


def test_parse_decision_keeps_safe_bounded_actions():
    raw = "Sure!\n```json\n" + _reply(
        thought="my move",
        actions=[
            {"key": "w", "hold": 9},
            {"keys": "win+r"},
            {"keys": "alt+f4"},
            {"keys": "ctrl+s"},
            {"click": [150, -3], "button": "right"},
            {"type": "wait", "wait": 30},
            {"type": "gg"},
            {"look": [5000, -20]},
        ],
    ) + "\n```"
    d = autoplay.parse_decision(raw)
    assert d["thought"] == "my move"
    # Forbidden combos are dropped; only the first MAX_ACTIONS_PER_STEP raw entries are considered.
    assert d["actions"] == [
        {"keys": ["w"], "hold": 3.0},
        {"keys": ["ctrl", "s"], "hold": 0.05},
        {"click": (100.0, 0.0), "right": True},
        {"wait": 5.0},
    ]


def test_parse_decision_rejects_garbage():
    assert autoplay.parse_decision("I can't see anything") is None
    assert autoplay.parse_decision("{not json}") is None


def test_blocks_anti_cheat_games():
    with mock.patch.object(autoplay, "_IS_WINDOWS", True), \
         mock.patch.object(autoplay, "detect_active_game", return_value=None):
        for game in ("Valorant", "Fortnite", "League of Legends", "CS2", "Roblox"):
            assert "won't play" in autoplay.play_game(game=game)


def test_loop_plays_until_done_and_only_into_the_game_window():
    pressed = []
    replies = iter([
        _reply(thought="open menu", actions=[{"key": "e"}, {"click": [50, 50]}]),
        _reply(thought="won", say="Got it!", done=True),
    ])
    session = autoplay._Session("Chess", "win", 1, hwnd=42, genre="")
    with mock.patch.object(autoplay, "_foreground", return_value=42), \
         mock.patch.object(autoplay, "_client_rect", return_value=(0, 0, 200, 100)), \
         mock.patch.object(autoplay, "_capture", side_effect=lambda r: (b"jpg", bytes([len(pressed)]) * 576)), \
         mock.patch.object(autoplay, "_vk_for", side_effect=lambda k: ord(k.upper())), \
         mock.patch.object(autoplay, "_press", side_effect=lambda vks, hold: pressed.append(vks)), \
         mock.patch.object(autoplay, "_click", side_effect=lambda x, y, right=False: pressed.append((x, y))), \
         mock.patch.object(autoplay, "_hud"), \
         mock.patch.object(actions.bridges, "ask_vision", side_effect=lambda img, prompt: next(replies)):
        autoplay._play_loop(session)

    assert pressed == [[ord("E")], (100, 50)]
    assert session.steps == 2
    assert session.end_reason == "Got it!"
    assert session.stop.is_set()
    assert autoplay.check_watch() == "I stopped playing Chess: Got it!."


def test_loop_sends_nothing_while_game_is_not_focused():
    session = autoplay._Session("Chess", "win", 1, hwnd=42, genre="")
    vision = mock.Mock()
    threading.Timer(0.3, session.stop.set).start()
    with mock.patch.object(autoplay, "_foreground", return_value=7), \
         mock.patch.object(autoplay, "_hud"), \
         mock.patch.object(actions.bridges, "ask_vision", vision):
        autoplay._play_loop(session)
    vision.assert_not_called()
    assert session.steps == 0


def test_unchanged_screen_while_waiting_skips_model_calls():
    session = autoplay._Session("Chess", "win", 1, hwnd=42, genre="")
    vision = mock.Mock(return_value=_reply(thought="their turn", actions=[{"wait": 0}]))
    threading.Timer(1.5, session.stop.set).start()
    with mock.patch.object(autoplay, "_foreground", return_value=42), \
         mock.patch.object(autoplay, "_client_rect", return_value=(0, 0, 200, 100)), \
         mock.patch.object(autoplay, "_capture", return_value=(b"jpg", bytes(576))), \
         mock.patch.object(autoplay, "_hud"), \
         mock.patch.object(actions.bridges, "ask_vision", vision):
        autoplay._play_loop(session)
    assert vision.call_count == 1  # the opponent never moved, so she never asked again
    assert session.skipped >= 1
