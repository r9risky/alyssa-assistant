"""Discord bot: audio conversion, picking the channel, utterance cutting and the token gate."""
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

import call_ears
import discord_bot
from brain.providers import openai as openai_provider


def test_pcm_conversion_to_discord_and_back():
    tone = (np.sin(np.linspace(0, 200, 24000)) * 10000).astype(np.int16)  # 1 s, 24 kHz mono
    out = discord_bot.to_discord_pcm(tone.tobytes(), 24000, 1)
    assert len(out) == 48000 * 4  # 1 s of 48 kHz stereo s16
    back = discord_bot.to_whisper_audio(out)
    assert back.dtype == np.float32 and len(back) == 16000
    assert 0.1 < float(np.abs(back).max()) <= 1.0


def _voice_channel(name, members=()):
    return SimpleNamespace(name=name, voice_states={m: object() for m in members})


def test_find_channel_prefers_where_the_owner_is():
    lobby, gaming = _voice_channel("Lobby"), _voice_channel("Gaming", members=[42])
    guilds = [SimpleNamespace(name="Friends", voice_channels=[lobby, gaming])]
    assert discord_bot.find_channel(guilds, 42) is gaming
    assert discord_bot.find_channel(guilds, 7, channel="lobby") is lobby
    assert discord_bot.find_channel(guilds, 7) is None


def test_listener_cuts_utterances_on_silence():
    got = []
    listener = discord_bot.FriendListener(handle=lambda name, pcm: got.append(name))
    listener.stop()
    friend = SimpleNamespace(id=5, bot=False, display_name="Jake")
    with patch.object(discord_bot, "owner_id", return_value=1):
        for _ in range(40):  # 40 x 20 ms = 0.8 s of audio
            listener.on_audio(friend, SimpleNamespace(pcm=b"\1\0" * 1920))
        listener.on_audio(SimpleNamespace(id=1, bot=False, display_name="Roi"), SimpleNamespace(pcm=b"\1\0" * 1920))
    assert listener.finished(now=0) == []  # still talking
    done = listener.finished(now=10**9)
    assert [name for name, _ in done] == ["Jake"]  # the owner's own voice is skipped


def test_only_lines_with_her_name_cost_an_ai_call():
    calls, said = [], []
    reply_fn = lambda who, text, ctx: calls.append((who, text, ctx)) or "Hi Jake."
    session = call_ears.get_call_session()
    session.start_call("Discord", "general")
    try:
        assert call_ears.handle_friend_line("Jake", "anyone up for ranked", reply_fn=reply_fn, speak_fn=said.append, now=100) is None
        assert calls == [] and said == []
        reply = call_ears.handle_friend_line("Jake", "Alyssa what's up", reply_fn=reply_fn, speak_fn=said.append, now=200)
        assert reply == "Hi Jake." and said == ["Hi Jake."]
        assert calls[0][2][-1] == "Jake: anyone up for ranked"  # recent chat goes along as context
    finally:
        session.end_call()


def test_friend_requests_wait_for_the_owner():
    said = []
    session = call_ears.get_call_session()
    session.start_call("Discord", "general")
    try:
        reply = call_ears.handle_friend_line(
            "Mia", "Alyssa launch Minecraft", reply_fn=lambda *a: "unused", speak_fn=said.append, now=300
        )
        assert "Mia wants me to open Minecraft" in reply
        assert session.pending_request is not None and session.pending_request.who == "Mia"
        import brain.dialogue as dialogue
        with patch("actions.FUNCTIONS", {"open_app": lambda app_name: f"Opened {app_name}"}):
            answer = dialogue._handle_pre_model_intent("yes")
        assert "Opened Minecraft" in answer
    finally:
        session.end_call()


def test_chat_only_requests_send_no_tools():
    captured = {}

    class _Resp:
        status_code = 200
        def raise_for_status(self): pass
        def iter_lines(self): return iter([b"data: [DONE]"])
        def close(self): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass

    def fake_post(url, json=None, **kwargs):
        captured.update(json)
        return _Resp()

    with patch.object(openai_provider._HTTP_SESSION, "post", side_effect=fake_post):
        try:
            openai_provider._call_openai_compatible(
                [{"role": "user", "content": "hi"}], "https://example.invalid/v1", "key", "m", "OpenAI", tools=[]
            )
        except Exception:
            pass
    assert captured and "tools" not in captured and "tool_choice" not in captured


def test_energy_segmenter_cuts_speech_and_ignores_her_voice():
    seg = discord_bot.EnergySegmenter()
    loud = np.full(1600, 0.1, dtype=np.float32)
    quiet = np.zeros(1600, dtype=np.float32)
    out = []
    for _ in range(10):  # 1 s of talking
        out += seg.feed(loud)
    for _ in range(9):  # 0.9 s pause ends it
        out += seg.feed(quiet)
    assert len(out) == 1 and len(out[0]) >= 16000
    out = []
    for _ in range(10):  # her own voice coming back through Discord: dropped
        out += seg.feed(loud, muted=True)
    for _ in range(9):
        out += seg.feed(quiet)
    assert out == []
    for _ in range(3):  # a 0.3 s blip is too short
        out += seg.feed(loud)
    for _ in range(9):
        out += seg.feed(quiet)
    assert out == []


def test_falls_back_only_when_the_pc_hears_talk_and_the_bot_hears_nothing():
    now = 1000.0
    talking = [now - 20, now - 10, now - 2]
    assert discord_bot.should_fall_back(talking, last_bot_packet=now - 60, now=now)
    assert not discord_bot.should_fall_back(talking, last_bot_packet=now - 5, now=now)  # bot is fine
    assert not discord_bot.should_fall_back([now - 2], last_bot_packet=now - 60, now=now)  # quiet call


def test_if_discord_wont_play_her_voice_goes_to_the_speakers():
    connected_vc = SimpleNamespace(is_connected=lambda: True, is_playing=lambda: False)
    fake_client = SimpleNamespace(voice_clients=[connected_vc])

    def fail(coro, timeout=20.0):
        coro.close()
        raise RuntimeError("Opus missing")

    with patch.object(discord_bot, "_client", fake_client), patch.object(discord_bot, "_run", side_effect=fail):
        try:
            assert discord_bot.can_speak()
            assert discord_bot.play_pcm(b"\0" * 3840) is False
            assert not discord_bot.can_speak() and not discord_bot.busy()
            import voice_playback
            assert not voice_playback._discord_bot_connected()  # speech now plays locally
        finally:
            discord_bot._cant_speak = ""
