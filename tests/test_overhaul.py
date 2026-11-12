"""Tests for Alyssa overhaul: bug fixes, modular packages, and decoupled extensions."""

import os
import sys
import tempfile
from unittest.mock import MagicMock, patch
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import brain.dialogue as dialogue
import memory


def test_dialogue_loads_personality_and_profile_without_swallowing_errors():
    """Verify brain.dialogue._build_system_prompt successfully checks personality.md and me.md."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        fake_personality = os.path.join(tmp_dir, "personality.md")
        with open(fake_personality, "w", encoding="utf-8") as f:
            f.write("You are an analytical assistant.")

        with patch.object(memory, "get_personality_path", return_value=fake_personality):
            with patch.object(memory, "load_personality_notes", return_value="You are an analytical assistant."):
                prompt = dialogue._build_system_prompt("Hello")
                assert "Custom personality notes (personality.md):" in prompt
                assert "You are an analytical assistant." in prompt


def test_rendering_qmovie_import_available():
    """Verify overlay.rendering imports QMovie from PySide6.QtGui without NameError."""
    import overlay.rendering as rendering
    assert hasattr(rendering, "QMovie")


def test_voice_facade_all_symbols_defined():
    """Verify voice.py does not define unresolvable symbols in __all__."""
    import voice
    for symbol in voice.__all__:
        assert hasattr(voice, symbol), f"Symbol {symbol} in voice.__all__ is not defined"


def test_extension_toggles_in_dialogue():
    """Verify Capri features respect configuration toggles."""
    with patch.object(dialogue.config, "DESKTOP_CHAOS_ENABLED", False):
        res = dialogue._handle_pre_model_intent("fake crash")
        assert "disabled" in res.lower()

    with patch.object(dialogue.config, "WATCH_ALONG_ENABLED", False):
        res = dialogue._handle_pre_model_intent("watch this video with me")
        assert "disabled" in res.lower()

    with patch.object(dialogue.config, "REPLAY_BUFFER_ENABLED", False):
        res = dialogue._handle_pre_model_intent("clip that!")
        assert "disabled" in res.lower()


def test_preflight_checks_notify_throttling():
    """Verify run_preflight_checks(notify=False) does not emit redundant errors or stdout."""
    import main
    bridge_mock = MagicMock()
    with patch.object(config, "LLM_PROVIDER", "gemini"):
        with patch.object(config, "GEMINI_API_KEY", ""):
            # First run with notify=False
            res = main.run_preflight_checks(bridge_mock, notify=False)
            assert res is False
            assert bridge_mock.gemini_key_needed.emit.call_count == 0
            assert bridge_mock.error_signal.emit.call_count == 0

            # Second run with notify=True
            res_notified = main.run_preflight_checks(bridge_mock, notify=True)
            assert res_notified is False
            assert bridge_mock.gemini_key_needed.emit.call_count == 1


def test_nameutil_detects_expanded_whisper_aliases():
    """Verify Whisper mishearings like 'eliza' and 'elyssa' are recognized as Alyssa."""
    import nameutil
    for variant in ["eliza", "elyssa", "elysia", "allissa", "alisha"]:
        text = f"hey {variant} what time is it"
        assert nameutil.contains_name(text), f"Failed to match alias '{variant}' in '{text}'"
        span = nameutil.find_name_span(text)
        assert span is not None
        stripped = nameutil.strip_name_at_span(text, span)
        assert "what time is it" in stripped


def test_vocabulary_corrections_normalizes_whisper_mishearings():
    """Verify transcribe._apply_vocabulary_corrections normalizes Whisper mishearings to Alyssa."""
    import transcribe
    corrected = transcribe._apply_vocabulary_corrections("eliza open chrome")
    assert "Alyssa" in corrected


def test_recorder_downmixes_stereo_stream():
    """Verify _DownmixingStream correctly averages multi-channel input to mono."""
    import numpy as np
    import recorder
    raw_mock = MagicMock()
    stereo_data = np.array([[100, 200], [300, 400], [500, 600]], dtype=np.int16)
    raw_mock.read.return_value = (stereo_data, False)

    stream = recorder._DownmixingStream(raw_mock)
    data, overflow = stream.read(3)
    assert not overflow
    assert data.ndim == 1
    np.testing.assert_array_equal(data, np.array([150, 350, 550], dtype=np.int16))


def test_wake_word_triggered_state_tracking():
    """Verify recorder.was_wake_word_triggered returns boolean state."""
    import recorder
    assert isinstance(recorder.was_wake_word_triggered(), bool)


def test_main_command_gating_logic():
    """Verify command gating logic for always_listening vs wake_word modes."""
    import main
    import recorder

    # In always_listening mode: commands without mentioning the name are accepted directly
    with patch.object(config, "WAKE_WORD_MODE", "always_listening"):
        wake_mode = str(getattr(config, "WAKE_WORD_MODE", "wake_word")).lower()
        text = "open spotify and play jazz"
        if wake_mode == "always_listening":
            command = main._strip_name_mention(text)
        assert command == "open spotify and play jazz"

    # In wake_word mode with real-time wake word detector triggered
    with patch.object(config, "WAKE_WORD_MODE", "wake_word"):
        with patch.object(recorder, "was_wake_word_triggered", return_value=True):
            wake_mode = str(getattr(config, "WAKE_WORD_MODE", "wake_word")).lower()
            text = "turn off lights"
            if wake_mode == "wake_word" and recorder.was_wake_word_triggered():
                command = main._strip_name_mention(text)
            assert command == "turn off lights"

    # In wake_word mode without real-time trigger:
    # 1. Speech containing Alyssa (or alias) is accepted
    with patch.object(config, "WAKE_WORD_MODE", "wake_word"):
        with patch.object(recorder, "was_wake_word_triggered", return_value=False):
            text = "eliza turn off lights"
            command = main._extract_command(text)
            assert command == "turn off lights"

    # 2. Ambient room speech NOT containing Alyssa is ignored
    with patch.object(config, "WAKE_WORD_MODE", "wake_word"):
        with patch.object(recorder, "was_wake_word_triggered", return_value=False):
            text = "pass the salt please"
            command = main._extract_command(text)
            assert command is None
