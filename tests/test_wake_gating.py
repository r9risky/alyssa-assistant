"""Wake-word false triggers and early transcription (v1.10.0)."""
import threading
from unittest.mock import patch

import config
import main
import nameutil
import transcribe


def test_other_peoples_names_are_not_her_name():
    with patch.object(config, "ASSISTANT_NAME_ALIASES", ["alissa", "lisa", "Melissa"]):
        assert not nameutil.contains_name("Lisa is coming over")
        assert not nameutil.contains_name("melissa said hi")
        assert nameutil.contains_name("alissa open chrome")


def test_only_addressing_her_counts():
    assert main._extract_command("hey Alyssa, open chrome") == "open chrome"
    assert main._extract_command("okay hey Alyssa what time is it") == "what time is it"
    assert main._extract_command("open chrome, Alyssa") == "open chrome"
    assert main._extract_command("so yeah um Alyssa is broken again") is None


def test_prompt_echo_is_dropped_but_real_commands_are_kept():
    prompt = transcribe.build_contextual_prompt()
    assert transcribe._is_prompt_echo("Alyssa, open Chrome, open Notepad, open Spotify.", prompt)
    assert not transcribe._is_prompt_echo("Alyssa, open Chrome.", prompt)
    assert not transcribe._is_prompt_echo("Alyssa, open Chrome and play music", prompt)


def test_fuzzy_corrections_never_invent_her_name():
    with patch.object(config, "VOCABULARY_CORRECTIONS", [("Alyssa", ["eliza"])]):
        assert transcribe._apply_vocabulary_corrections("a lissa thing") == "a lissa thing"
        assert transcribe._apply_vocabulary_corrections("eliza open chrome") == "Alyssa open chrome"
        assert transcribe._apply_vocabulary_corrections("elizabeth called") == "elizabeth called"


def test_transcribe_uses_early_transcript_instead_of_starting_over():
    result = ["open chrome"]
    thread = threading.Thread(target=lambda: None)
    thread.start()
    transcribe.set_early_transcript(thread, result)
    with patch.object(transcribe, "_transcribe_impl", side_effect=AssertionError("re-transcribed")):
        assert transcribe.transcribe(None) == "open chrome"
    # Consumed: the next call transcribes normally.
    with patch.object(transcribe, "_transcribe_impl", return_value="next"):
        assert transcribe.transcribe(None) == "next"
