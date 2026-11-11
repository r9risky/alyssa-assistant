"""Regression tests for microphone listening (VAD + push-to-talk)."""

import time

import numpy as np

import recorder


class _FakeSession:
    def __init__(self, prob=0.9):
        self.prob = prob
        self.inputs = []

    def run(self, _outputs, feeds):
        self.inputs.append(feeds["input"].copy())
        return np.array([[self.prob]], dtype=np.float32), feeds["state"]


def _silero_vad(session):
    vad = recorder.VoiceActivityDetector(sample_rate=16000)
    vad._session = session
    vad.engine = "silero"
    return vad


def test_silero_receives_64_sample_context_from_previous_window():
    """Silero v5 scores real speech ~0 without the 64-sample context prefix."""
    session = _FakeSession()
    vad = _silero_vad(session)

    first = (np.arange(512) % 100).astype(np.int16)
    second = (np.arange(512) % 50).astype(np.int16)
    assert vad.is_speech(first)
    assert vad.is_speech(second)

    assert session.inputs[0].shape == (1, 576)
    assert np.all(session.inputs[0][0, :64] == 0)
    expected_ctx = first[-64:].astype(np.float32) / 32768.0
    assert np.allclose(session.inputs[1][0, :64], expected_ctx)


def test_silero_reset_clears_context():
    session = _FakeSession()
    vad = _silero_vad(session)
    vad.is_speech(np.full(512, 1000, dtype=np.int16))
    vad.reset()
    vad.is_speech(np.full(512, 1000, dtype=np.int16))
    assert np.all(session.inputs[-1][0, :64] == 0)


def test_silero_threshold_respected():
    vad = _silero_vad(_FakeSession(prob=0.1))
    assert not vad.is_speech(np.zeros(512, dtype=np.int16))


def test_downmix_does_not_overflow_int16():
    class _Raw:
        def read(self, frames):
            return np.full((frames, 2), 30000, dtype=np.int16), False

    data, _ = recorder._DownmixingStream(_Raw()).read(4)
    assert data.dtype == np.int16
    assert np.all(data == 30000)


def test_push_to_talk_waits_for_hotkey_instead_of_spinning(monkeypatch):
    real_config = recorder.config

    class _PttConfig:
        WAKE_WORD_MODE = "push_to_talk"

        def __getattr__(self, name):
            return getattr(real_config, name)

    monkeypatch.setattr(recorder, "config", _PttConfig())
    recorder._talk_request.clear()
    calls = []

    def fake_blocking(result_q, text_queue=None, bypass_wake_word=False):
        calls.append(bypass_wake_word)
        result_q.put((None, None))

    monkeypatch.setattr(recorder, "_record_command_blocking", fake_blocking)

    start = time.monotonic()
    assert recorder.record_command() is None
    assert time.monotonic() - start >= recorder._PUSH_TO_TALK_POLL_SECONDS * 0.8
    assert calls == []  # mic never opened without the hotkey

    recorder.request_push_to_talk()
    recorder.record_command()
    assert calls == [True]
    assert not recorder._talk_request.is_set()
