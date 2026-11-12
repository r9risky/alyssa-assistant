import os
import sys
import types
import wave

import numpy as np

import config
import voice_synthesis


class FakeKokoro:
    def __init__(self, model_path, voices_path):
        self.paths = (model_path, voices_path)

    def create(self, text, voice, speed, lang):
        assert voice == "af_bella"
        return np.full(2400, 0.25, dtype=np.float32), 24000


def test_kokoro_downloads_models_once_and_writes_wav(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "TTS_PROVIDER", "kokoro")
    monkeypatch.setattr(config, "KOKORO_VOICE", "af_bella")
    monkeypatch.setattr(voice_synthesis, "_kokoro", None)
    monkeypatch.setattr(voice_synthesis, "kokoro_models_dir", lambda: str(tmp_path))
    monkeypatch.setitem(sys.modules, "kokoro_onnx", types.SimpleNamespace(Kokoro=FakeKokoro))
    downloads = []

    def fake_download(url, dest):
        downloads.append(url)
        with open(dest, "wb") as f:
            f.write(b"model")

    monkeypatch.setattr(voice_synthesis, "_download_file", fake_download)

    first = voice_synthesis._synthesize_to_temp_file("Hello there.")
    second = voice_synthesis._synthesize_to_temp_file("Again.")
    try:
        assert first.endswith(".wav")
        with wave.open(first, "rb") as wf:
            assert (wf.getnchannels(), wf.getsampwidth(), wf.getframerate()) == (1, 2, 24000)
            assert wf.getnframes() == 2400
        assert len(downloads) == 2  # model + voices, only on first use
        assert sorted(os.listdir(tmp_path)) == sorted(voice_synthesis.KOKORO_FILES)
    finally:
        os.remove(first)
        os.remove(second)
