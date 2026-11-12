"""Regressions for "she stops responding after a while" and "her voice plays twice"."""
import threading
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

import actions
import brain
import discord_bot
import main
import recorder
import voice_playback
from brain import dialogue


# ---------------------------------------------------------------- mic recovery

def test_portaudio_is_never_reset_while_a_mic_stream_is_open():
    with (
        patch.object(recorder, "_active_streams", 1),
        patch.object(recorder.sd, "_terminate") as terminate,
        patch.object(recorder.sd, "_initialize"),
    ):
        assert recorder._rescan_portaudio_if_idle(force=True) is False
    terminate.assert_not_called()


def test_portaudio_reset_is_rate_limited_unless_forced():
    with (
        patch.object(recorder, "_active_streams", 0),
        patch.object(recorder, "_last_rescan", 0.0),
        patch.object(recorder.sd, "_terminate") as terminate,
        patch.object(recorder.sd, "_initialize"),
    ):
        assert recorder._rescan_portaudio_if_idle() is True
        assert recorder._rescan_portaudio_if_idle() is False
        assert recorder._rescan_portaudio_if_idle(force=True) is True
    assert terminate.call_count == 2


def test_missing_mic_lookup_from_barge_in_never_resets_portaudio():
    with (
        patch.object(recorder.config, "MICROPHONE_DEVICE", "Gone Mic", create=True),
        patch.object(recorder, "_find_configured_device", return_value=None),
        patch.object(recorder, "_startup_device_wait_used", True),
        patch.object(recorder, "_rescan_portaudio_if_idle") as rescan,
    ):
        assert recorder._configured_input_device(allow_rescan=False) is None
    rescan.assert_not_called()


def test_repeated_mic_errors_reset_the_audio_system():
    def failing_worker(result_q, *_args):
        result_q.put((None, RuntimeError("Invalid stream pointer")))

    with (
        patch.object(recorder, "_record_command_blocking", failing_worker),
        patch.object(recorder, "_MIC_ERROR_BACKOFF_SECONDS", 0),
        patch.object(recorder, "_consecutive_mic_errors", 0),
        patch.object(recorder, "_rescan_portaudio_if_idle", return_value=True) as rescan,
    ):
        assert recorder.record_command() is None
        rescan.assert_not_called()
        assert recorder.record_command() is None
        rescan.assert_called_once_with(force=True)


class _SpeechStream:
    """Fake mic that always hears speech and marks her speech finished after a few reads."""

    def __init__(self, done_event, done_after):
        self.reads = 0
        self._done_event, self._done_after = done_event, done_after

    def read(self, frames):
        self.reads += 1
        if self.reads >= self._done_after:
            self._done_event.set()
        return np.full((frames, 1), 3000, dtype=np.int16), False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass


def test_barge_in_listener_stops_when_she_finishes_talking():
    done = threading.Event()
    stream = _SpeechStream(done, done_after=8)
    always_speech = SimpleNamespace(engine="webrtc", is_speech=lambda _c: True, reset=lambda: None)
    with (
        patch.object(recorder.config, "ALLOW_INTERRUPTIONS", True),
        patch.object(recorder.config, "BARGE_IN_REQUIRE_NAME", True),
        patch.object(recorder, "VoiceActivityDetector", return_value=always_speech),
        patch.object(recorder, "_open_stream", return_value=stream),
        patch.object(recorder.transcribe, "start_streaming", return_value=None),
        patch.object(recorder.transcribe, "transcribe") as transcribe,
    ):
        assert recorder.listen_for_barge_in(threading.Event(), done) is None
    # It used to keep recording her (up to MAX_RECORD_SECONDS) and run Whisper on it.
    assert stream.reads < 12
    transcribe.assert_not_called()


def test_reply_waits_for_the_words_you_said_over_her():
    joins = []
    thread = SimpleNamespace(join=lambda timeout: joins.append(timeout))
    main._join_listener(thread, threading.Event())
    interrupted = threading.Event()
    interrupted.set()
    main._join_listener(thread, interrupted)
    assert joins[0] == 1.0
    assert joins[1] >= main.config.MAX_RECORD_SECONDS


# ---------------------------------------------------------------- stuck playback

def test_discord_busy_is_false_once_she_is_out_of_the_channel():
    discord_bot._audio.extend(b"\0" * 100)
    with patch.object(discord_bot, "_voice_client", return_value=None):
        assert discord_bot.busy() is False
    assert not discord_bot._audio


def test_discord_playback_wait_gives_up_when_the_player_stalls():
    stalled_vc = SimpleNamespace(is_playing=lambda: True)
    with (
        patch.object(discord_bot, "_voice_client", return_value=stalled_vc),
        patch.object(discord_bot, "PLAYBACK_SLACK_SECONDS", 0.05),
    ):
        discord_bot.wait_for_playback(0.0)  # returns instead of looping forever


def test_speaker_playback_gives_up_when_the_output_device_stalls():
    class FrozenMusic:
        def load(self, path): pass
        def set_volume(self, value): pass
        def play(self): pass
        def get_busy(self): return True
        def get_pos(self): return 1234  # clock never moves
        def stop(self): pass
        def unload(self): pass

    with (
        patch.object(voice_playback, "_ensure_mixer"),
        patch.object(voice_playback, "_discord_bot_connected", return_value=False),
        patch.object(voice_playback, "_configured_virtual_mic_device", return_value=None),
        patch.object(voice_playback, "_PLAYBACK_STALL_SECONDS", 0.05),
        patch.object(voice_playback.pygame.mixer, "music", FrozenMusic()),
    ):
        assert voice_playback._play("speech.mp3") is True
    assert not voice_playback.is_playing()


# ---------------------------------------------------------------- doubled voice

def test_final_reply_is_not_spoken_again_when_only_spacing_differs():
    assert main._already_spoken("Sure!  Here's\nthe plan.", "Here's the plan.")
    assert main._already_spoken('Done. {"name": "x"}', "Done.")
    assert not main._already_spoken("Let me check.", "It's 5 pm.")


def test_no_extra_announcement_when_the_model_already_said_it():
    first = {"message": {"role": "assistant", "content": "Sure, opening it!", "tool_calls": [
        {"id": "a", "function": {"name": "fake_tool", "arguments": {}}},
    ]}}
    final = {"message": {"role": "assistant", "content": "Done.", "tool_calls": []}}
    replies = iter([(first, None), (final, None)])

    def fake_model(messages, provider, label, force_tools=False, on_text_delta=None, **_kw):
        result = next(replies)
        if on_text_delta is not None:
            on_text_delta(result[0]["message"]["content"])
        return result

    spoken = []
    try:
        with (
            patch.dict(actions.FUNCTIONS, {"fake_tool": lambda: "ok"}),
            patch.object(dialogue, "_call_model_with_error_handling", side_effect=fake_model),
            patch.object(dialogue, "_natural_announce_reply", return_value="Opening it..."),
            patch.object(dialogue.ui_config, "CONFIRM_BEFORE_ACTIONS", False, create=True),
        ):
            brain.handle_command(
                "Open it.",
                on_partial_reply=lambda text: spoken.append(("announce", text)),
                on_text_delta=lambda delta: spoken.append(("model", delta)),
            )
    finally:
        brain.clear_conversation_history()

    assert ("announce", "Opening it...") not in spoken
    assert spoken == [("model", "Sure, opening it!"), ("model", "Done.")]


def test_realtime_voice_failure_mid_reply_does_not_restart_the_reply():
    speaker = voice_playback.StreamingSpeaker.__new__(voice_playback.StreamingSpeaker)
    speaker.stop_event = threading.Event()
    speaker.text = "Hello there. How are you?"
    speaker._playback_started = True
    speaker._audio_error = None
    speaker._audio = voice_playback.queue.Queue()
    speaker._audio_done = threading.Event()
    speaker._chunks = voice_playback.queue.Queue()
    with (
        patch.object(speaker, "_play_pcm"),
        patch.object(voice_playback._eleven_realtime_tts, "start", side_effect=ConnectionError("dropped")),
        patch.object(voice_playback, "_speak_one") as replay,
    ):
        speaker._run_eleven_realtime()
    replay.assert_not_called()


def test_pc_ears_mute_while_her_voice_plays_on_the_speakers():
    with patch.object(voice_playback, "_playing", 1):
        assert discord_bot._speaking_on_pc() is True
    assert discord_bot._speaking_on_pc() is False
