"""The speech bubble types her reply out in step with her voice."""
from unittest.mock import patch

import pytest


@pytest.fixture
def bubble():
    from PySide6.QtWidgets import QApplication
    from overlay.widgets import SpeechBubble

    _app = QApplication.instance() or QApplication([])  # noqa: F841 (keeps Qt alive)

    with patch("voice_dsp.play_typing_blip"):
        yield SpeechBubble(None, {"bubble_seconds": 5})


def _tick(bubble, seconds):
    bubble._type_clock -= seconds
    bubble._on_type_tick()


def test_text_waits_for_her_voice_then_types_sentence_by_sentence(bubble):
    bubble.show_thinking()
    bubble.on_reply_pending()
    bubble.show_message("Hey there. It's sunny today.")
    _tick(bubble, 5)
    assert bubble._revealed() == ""  # voice hasn't started: the dots stay
    assert bubble.label.text().startswith("Thinking")

    bubble.on_talk_start()
    bubble.on_chunk_spoken("Hey there.")
    _tick(bubble, 5)
    assert bubble._revealed() == "Hey there."  # never ahead of what she's saying

    bubble.on_chunk_spoken("It's sunny today.")
    _tick(bubble, 0.25)
    assert "Hey there." in bubble._revealed()
    assert not bubble._typing_done()  # still typing the sentence she's saying

    bubble.on_talk_end()
    assert bubble._typing_done()
    assert "sunny today." in bubble.label.text()


def test_streamed_growth_keeps_what_is_typed(bubble):
    bubble.show_message("Hello")
    bubble.finish_typing()
    bubble.show_message("Hello there, friend.")
    assert bubble._revealed() == "Hello"
    bubble.show_message("Something else entirely.")
    assert bubble._revealed() == ""


def test_unspoken_message_types_quickly_and_completes(bubble):
    bubble.show_message("Got my popcorn!")
    _tick(bubble, 1)
    assert bubble._typing_done()
    assert bubble.label.text() == "Got my popcorn!"
