"""Call mode overhaul: the app-wide freeze, hearing friends, and how she talks in a call."""
import asyncio
import logging
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

import brain.dialogue as dialogue
import call_ears
import discord_bot
import plugins.call_mode as call_mode
import startup_logging
import transcribe
import voice_synthesis


class _BrokenHandler(logging.Handler):
    def emit(self, record):
        try:
            raise PermissionError(32, "alyssa.log is in use by another process")
        except Exception:
            self.handleError(record)


def test_a_failing_log_write_never_freezes_the_app():
    # A refused log rotation made logging report the error to stderr from inside the
    # stdout tee; that deadlocked, and every thread that printed afterwards froze.
    logger = logging.getLogger("test.broken-log")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(_BrokenHandler())
    out = startup_logging._Tee(None, logger, logging.INFO)
    err = startup_logging._Tee(None, logger, logging.ERROR)
    with patch.object(sys, "stderr", err):
        worker = threading.Thread(target=lambda: [out.write("hello\n") for _ in range(3)], daemon=True)
        worker.start()
        worker.join(3)
    assert not worker.is_alive()


def test_log_keeps_writing_when_windows_refuses_rotation(tmp_path):
    handler = startup_logging._SafeRotatingFileHandler(tmp_path / "alyssa.log", maxBytes=50, backupCount=2)

    def refuse(src, dst):
        raise PermissionError(32, "in use")

    handler.rotator = refuse
    logger = logging.getLogger("test.rotation")
    logger.propagate = False
    logger.addHandler(handler)
    try:
        for i in range(5):
            logger.warning("line %d %s", i, "x" * 30)
    finally:
        logger.removeHandler(handler)
        handler.close()
    assert "line 4" in (tmp_path / "alyssa.log").read_text()


def test_stalled_voice_server_cannot_hold_her_voice_forever():
    with patch.object(voice_synthesis, "SYNTHESIS_TIMEOUT_SECONDS", 0.2):
        start = time.monotonic()
        with pytest.raises(TimeoutError):
            voice_synthesis._run_on_loop(asyncio.sleep(30))
    assert time.monotonic() - start < 5


def test_call_audio_gives_way_to_your_own_speech():
    held, release = threading.Event(), threading.Event()

    def hold_model():
        with transcribe._model_lock:
            held.set()
            release.wait(5)

    threading.Thread(target=hold_model, daemon=True).start()
    held.wait(2)
    try:
        with patch.object(transcribe, "CALL_LOCK_WAIT_SECONDS", 0.1), \
                patch.object(transcribe, "_transcribe_impl_locked") as whisper:
            assert transcribe.transcribe_call_audio(np.zeros(16000, np.float32)) == ""
        whisper.assert_not_called()
    finally:
        release.set()


def test_call_audio_is_one_quick_pass_with_the_names_as_hints():
    with patch.object(transcribe, "_transcribe_impl_locked", return_value="hey Alyssa") as whisper:
        assert transcribe.transcribe_call_audio(np.zeros(16000, np.float32), ["Sam"]) == "hey Alyssa"
    assert whisper.call_args.kwargs["quick"] is True
    assert "Sam" in whisper.call_args.kwargs["initial_prompt"]


def test_pc_ears_queue_keeps_the_newest_speech_instead_of_dropping_it():
    import queue

    ears = discord_bot.PCListener.__new__(discord_bot.PCListener)
    ears._pending = queue.Queue(maxsize=discord_bot.PCListener.BACKLOG)
    for i in range(5):
        ears.queue_speech(i)
    assert [ears._pending.get_nowait() for _ in range(3)] == [2, 3, 4]


def test_join_greets_the_call_herself():
    session = call_ears.get_call_session()
    with patch.object(discord_bot, "ready", return_value=True), \
            patch.object(discord_bot, "join", return_value="I'm in #Lobby on Friends."), \
            patch.object(discord_bot, "connected", return_value=True), \
            patch.object(discord_bot, "channel_members", return_value=["Sam", "Jo"]), \
            patch.object(call_mode.config, "USER_NICKNAME", "Riya"):
        reply = call_mode.join_call({})
    session.end_call()
    assert " Sam and Jo! " in reply
    # She greets them herself: never "Just say 'Hey everyone!'" or "You should announce...".
    assert "word for word" not in reply and "you should" not in reply.lower()
    with patch.object(call_mode.config, "USER_NICKNAME", "Riya"), \
            patch.object(call_ears.random, "choice", lambda options: options[0]):
        assert call_ears.greeting([], "on in 15.") == "Hey everyone! Riya asked me to tell you: on in 15."


def test_call_tools_are_her_whole_reply_without_a_second_model_call():
    turns = []
    tool_turn = {"message": {"tool_calls": [{"function": {"name": "relay_to_call", "arguments": {"message": "be there in five"}}}]}}

    def model_turn(*args, **kwargs):
        turns.append(1)
        return tool_turn, None

    session = call_ears.get_call_session()
    session.start_call("Discord", "general")
    try:
        with patch.object(dialogue, "_build_model_request", return_value=([], "openai", "OpenAI", [], None)), \
                patch.object(dialogue, "_invoke_model_turn", side_effect=model_turn):
            reply = dialogue.handle_command("could you let my friends know I will be there in five")
    finally:
        session.end_call()
    assert reply == "be there in five" and len(turns) == 1


def test_friends_can_answer_her_without_saying_her_name():
    said, asked = [], []
    reply_fn = lambda who, text, ctx: asked.append(text) or "Nice."
    session = call_ears.get_call_session()
    session.start_call("Discord", "general")
    try:
        call_ears._last_reply_at = 0.0
        call_ears.open_follow_up(now=1000)  # she just asked "what are we playing?"
        assert call_ears.handle_friend_line("Someone", "yeah", reply_fn=reply_fn, speak_fn=said.append, now=1001) is None
        assert call_ears.handle_friend_line("Someone", "we're playing valorant tonight", reply_fn=reply_fn, speak_fn=said.append, now=1004) == "Nice."
        assert call_ears.handle_friend_line("Someone", "you any good at it", reply_fn=reply_fn, speak_fn=said.append, now=1010) == "Nice."
        # Her reply opened the window again, but it closes once nobody talks to her for a bit.
        assert call_ears.handle_friend_line("Someone", "anyway back to the match", reply_fn=reply_fn, speak_fn=said.append, now=1100) is None
    finally:
        session.end_call()
        call_ears._last_reply_at = call_ears._follow_up_until = 0.0
    assert asked == ["we're playing valorant tonight", "you any good at it"]


def test_people_who_join_get_a_hello_once():
    said = []
    call_ears._greeted.clear()
    assert call_ears.greet_joiner("Sam", speak_fn=said.append, now=0) is not None
    assert call_ears.greet_joiner("Sam", speak_fn=said.append, now=60) is None
    assert call_ears.greet_joiner("Sam", speak_fn=said.append, now=700) is not None
    assert len(said) == 2 and all("Sam" in line for line in said)


def test_she_leaves_a_call_everyone_left():
    left = []

    async def disconnect():
        left.append(True)

    vc = SimpleNamespace(channel=SimpleNamespace(members=[SimpleNamespace(bot=True, id=9)]), disconnect=disconnect)
    session = call_ears.get_call_session()
    session.start_call("Discord", "general")
    with patch.object(discord_bot, "LEAVE_EMPTY_CALL_AFTER_SECONDS", 0), \
            patch.object(discord_bot, "_voice_client", return_value=vc):
        asyncio.run(discord_bot._leave_if_still_alone())
    assert left and not session.enabled


def test_she_stays_while_someone_is_still_there():
    left = []

    async def disconnect():
        left.append(True)

    vc = SimpleNamespace(channel=SimpleNamespace(members=[SimpleNamespace(bot=False, id=1)]), disconnect=disconnect)
    with patch.object(discord_bot, "LEAVE_EMPTY_CALL_AFTER_SECONDS", 0), \
            patch.object(discord_bot, "_voice_client", return_value=vc):
        asyncio.run(discord_bot._leave_if_still_alone())
    assert not left
