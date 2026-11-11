"""Tests for audio device matching, voice DSP processing, typing sound effects, and virtual mic streaming."""

import io
import os
import tempfile
import wave
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

import config
import voice_dsp
import voice_playback
import recorder
from PySide6.QtWidgets import QApplication


@pytest.fixture(autouse=True)
def ensure_qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def dummy_wav():
    """Create a temporary 1-second 24kHz mono sine wave WAV file."""
    t = np.linspace(0, 1.0, 24000, dtype=np.float32)
    sine = 0.4 * np.sin(2.0 * np.pi * 440.0 * t)
    pcm = (sine * 32767.0).astype(np.int16).tobytes()
    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(24000)
        wf.writeframes(pcm)
    yield path
    if os.path.exists(path):
        os.remove(path)


@pytest.fixture
def dummy_mp3(dummy_wav):
    """Create a temporary MP3 file encoded from dummy_wav via PyAV."""
    import av
    fd, path = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)
    inp = av.open(dummy_wav)
    out = av.open(path, "w")
    stream = out.add_stream("mp3", rate=24000)
    for frame in inp.decode(audio=0):
        for packet in stream.encode(frame):
            out.mux(packet)
    for packet in stream.encode():
        out.mux(packet)
    inp.close()
    out.close()
    yield path
    if os.path.exists(path):
        os.remove(path)


class TestVoiceDSPProcessing:
    def test_process_audio_file_disabled_by_default(self, dummy_wav):
        with patch.object(config, "VOICE_DSP_ENABLED", False):
            out_path = voice_dsp.process_audio_file(dummy_wav)
            assert out_path is None

    def test_process_audio_file_wav(self, dummy_wav):
        with patch.object(config, "VOICE_DSP_ENABLED", True), \
             patch.object(config, "VOICE_PITCH_SEMITONES", 3.0), \
             patch.object(config, "VOICE_SPEED_FACTOR", 1.1), \
             patch.object(config, "VOICE_ROBOT_AMOUNT", 20), \
             patch.object(config, "VOICE_GLITCH_AMOUNT", 10), \
             patch.object(config, "VOICE_LAYERS_ENABLED", True):
            out_path = voice_dsp.process_audio_file(dummy_wav)
            assert out_path is not None
            assert os.path.isfile(out_path)
            assert out_path.endswith(".wav")
            with wave.open(out_path, "rb") as wf:
                assert wf.getnchannels() == 1
                assert wf.getsampwidth() == 2
                assert wf.getframerate() == 24000
                assert wf.getnframes() > 1000
            os.remove(out_path)

    def test_process_audio_file_mp3(self, dummy_mp3):
        with patch.object(config, "VOICE_DSP_ENABLED", True), \
             patch.object(config, "VOICE_PITCH_SEMITONES", -2.5):
            out_path = voice_dsp.process_audio_file(dummy_mp3)
            assert out_path is not None
            assert os.path.isfile(out_path)
            assert out_path.endswith(".wav")
            with wave.open(out_path, "rb") as wf:
                assert wf.getnchannels() == 1
                assert wf.getsampwidth() == 2
                assert wf.getframerate() == 24000
                assert wf.getnframes() > 1000
            os.remove(out_path)

    def test_process_audio_file_nonexistent(self):
        with patch.object(config, "VOICE_DSP_ENABLED", True):
            out_path = voice_dsp.process_audio_file("/nonexistent/audio/file.wav")
            assert out_path is None


class TestTypingSoundBlip:
    def test_play_typing_blip_disabled(self):
        with patch.object(config, "TYPING_SOUND_ENABLED", False):
            with patch("voice_dsp.synthesize_typing_blip") as mock_synth:
                voice_dsp.play_typing_blip()
                mock_synth.assert_not_called()

    def test_play_typing_blip_enabled(self):
        import pygame
        if not pygame.mixer.get_init():
            pygame.mixer.init()
        with patch.object(config, "TYPING_SOUND_ENABLED", True):
            # Synthesizes blip and plays through pygame.mixer.Sound
            voice_dsp.play_typing_blip()
            assert voice_dsp._typing_blip_sound is not None


class TestAudioOutputDeviceMatching:
    def test_default_returns_none(self):
        with patch.object(config, "AUDIO_OUTPUT_DEVICE", "default"):
            assert voice_playback._configured_output_device_name() is None
            assert voice_playback._configured_output_device() is None

    def test_sdl2_matching_device_name(self):
        fake_sdl_devices = ["System audio output device", "Realtek High Definition Audio (Speakers)"]
        with patch.object(config, "AUDIO_OUTPUT_DEVICE", "Realtek"):
            with patch("pygame._sdl2.audio.get_audio_device_names", return_value=fake_sdl_devices):
                matched = voice_playback._configured_output_device_name()
                assert matched == "Realtek High Definition Audio (Speakers)"

    def test_sounddevice_fallback_matching(self):
        fake_sd_devices = [
            {"name": "Speakers (USB Audio Device)", "max_output_channels": 2},
            {"name": "Headphones (USB Audio Device)", "max_output_channels": 2},
        ]
        with patch.object(config, "AUDIO_OUTPUT_DEVICE", "Headphones"):
            with patch("pygame._sdl2.audio.get_audio_device_names", side_effect=Exception("no sdl2")):
                with patch("voice_playback.sd.query_devices", return_value=fake_sd_devices):
                    matched = voice_playback._configured_output_device_name()
                    assert matched == "Headphones (USB Audio Device)"


class TestCallModeVirtualMic:
    def test_vmic_disabled_returns_none(self):
        with patch.object(config, "CALL_MODE_ENABLED", False):
            assert voice_playback._configured_virtual_mic_device() is None

    def test_vmic_streams_mp3(self, dummy_mp3):
        mock_stream = MagicMock()
        with patch.object(config, "CALL_MODE_ENABLED", True), \
             patch.object(config, "CALL_VIRTUAL_MIC_DEVICE", "CABLE Input"), \
             patch("voice_playback._configured_virtual_mic_device", return_value=2), \
             patch("voice_playback.sd.RawOutputStream", return_value=mock_stream):
            voice_playback._play_file_to_virtual_mic(dummy_mp3)
            assert mock_stream.start.called
            assert mock_stream.write.called
            assert mock_stream.stop.called
            assert mock_stream.close.called


class TestMicrophoneMatchingAndFallback:
    def test_find_configured_device_mono_success(self):
        fake_devices = [
            {"name": "Realtek Microphone", "max_input_channels": 1},
        ]
        with patch("recorder.sd.query_devices", return_value=fake_devices):
            with patch("recorder.sd.check_input_settings", return_value=True):
                idx = recorder._find_configured_device("realtek")
                assert idx == 0

    def test_find_configured_device_stereo_fallback(self):
        fake_devices = [
            {"name": "USB Condenser Mic", "max_input_channels": 2},
        ]
        def fake_check(device, channels, dtype, samplerate):
            if channels == 1:
                raise ValueError("Channel count 1 unsupported")
            return True

        with patch("recorder.sd.query_devices", return_value=fake_devices):
            with patch("recorder.sd.check_input_settings", side_effect=fake_check):
                idx = recorder._find_configured_device("condenser")
                assert idx == 0

    def test_find_configured_device_native_samplerate_fallback(self):
        fake_devices = [
            {"name": "Bluetooth Hands-Free Mic", "max_input_channels": 1, "default_samplerate": 48000.0},
        ]
        def fake_check(device, channels, dtype, samplerate):
            if samplerate != 48000:
                raise ValueError("Only 48kHz supported natively")
            return True

        with patch("recorder.sd.query_devices", return_value=fake_devices):
            with patch("recorder.sd.check_input_settings", side_effect=fake_check):
                idx = recorder._find_configured_device("bluetooth")
                assert idx == 0

    def test_resampling_stream_downsampling(self):
        class MockRaw:
            def __init__(self):
                self.sr = 48000
            def read(self, frames):
                data = np.full((frames, 2), 1000, dtype=np.int16)
                return data, False
            def __enter__(self): return self
            def __exit__(self, *args): pass

        stream = recorder._ResamplingStream(MockRaw(), in_rate=48000, out_rate=16000)
        with stream as s:
            data, overflow = s.read(512)
            assert len(data) == 512
            assert data.dtype == np.int16
            assert not overflow

    def test_record_command_graceful_recovery_on_error(self):
        """Worker thread error must be caught and return None without crashing."""
        with patch("recorder._talk_request.is_set", return_value=False), \
             patch("recorder._record_command_blocking") as mock_blocking:
            def fake_blocking(result_q, *args, **kwargs):
                result_q.put((None, RuntimeError("Device disconnected")))
            mock_blocking.side_effect = fake_blocking
            res = recorder.record_command()
            assert res is None

    def test_configured_input_device_fallback_on_missing(self):
        """When configured mic is not plugged in, must fall back to None (system default) instead of crashing."""
        with patch.object(config, "MICROPHONE_DEVICE", "Missing USB Headset Mic"), \
             patch("recorder._find_configured_device", return_value=None), \
             patch("recorder._startup_device_wait_used", True):
            idx = recorder._configured_input_device()
            assert idx is None  # Safe fallback to system default


class TestSpeechBubbleSoundEffects:
    def test_bubble_triggers_typing_blip(self):
        from overlay.widgets import SpeechBubble
        bubble = SpeechBubble(None, {"bubble_seconds": 5})
        with patch("voice_dsp.play_typing_blip") as mock_blip:
            bubble.show_thinking()
            assert mock_blip.called

        with patch("voice_dsp.play_typing_blip") as mock_blip:
            bubble.show_message("Line 1\nLine 2\nLine 3")
            assert mock_blip.called

        with patch("voice_dsp.play_typing_blip") as mock_blip:
            bubble._pages = ["Page 1", "Page 2"]
            bubble._page_index = 0
            bubble.next_page()
            assert mock_blip.called

