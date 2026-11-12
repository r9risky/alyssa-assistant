import json
import os
import queue
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import brain
from brain import dialogue
from brain.providers import anthropic, openai
import voice


class _Response:
    status_code = 200

    def __init__(self, events):
        self.events = events

    def raise_for_status(self):
        pass

    def iter_lines(self, decode_unicode=False):
        for event in self.events:
            yield f"data: {json.dumps(event)}"
        yield "data: [DONE]"

    def close(self):
        pass


class LatencyPipelineTests(unittest.TestCase):
    def test_anthropic_stream_uses_shared_sse_parser(self):
        response = _Response(
            [{"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Ready."}}]
        )
        with (
            patch.object(anthropic.config, "ANTHROPIC_API_KEY", "key"),
            patch.object(anthropic._HTTP_SESSION, "post", return_value=response),
        ):
            result = anthropic._call_anthropic([])

        self.assertEqual(result["message"]["content"], "Ready.")

    def test_openai_stream_emits_text_and_reassembles_tool_arguments(self):
        response = _Response(
            [
                {"choices": [{"delta": {"content": "Ready. "}}]},
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_1",
                                        "function": {
                                            "name": "open_app",
                                            "arguments": '{"app_',
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "function": {"arguments": 'name":"Chrome"}'},
                                    }
                                ]
                            }
                        }
                    ]
                },
            ]
        )
        deltas = []
        with patch.object(openai._HTTP_SESSION, "post", return_value=response):
            result = openai._call_openai_compatible(
                [], "https://example.test/v1", "key", "model", "Test", deltas.append
            )

        self.assertEqual(deltas, ["Ready. "])
        self.assertEqual(result["message"]["content"], "Ready. ")
        self.assertEqual(
            result["message"]["tool_calls"][0]["function"]["arguments"],
            {"app_name": "Chrome"},
        )

    def test_streaming_speaker_releases_early_punctuation_boundary(self):
        speaker = object.__new__(voice.StreamingSpeaker)
        speaker.stop_event = threading.Event()
        speaker.text = ""
        speaker._pending = ""
        speaker._chunks = queue.Queue()
        speaker._chunks_emitted = 0
        speaker._realtime = False

        speaker.feed("Chrome is ready. The rest is still generating")

        self.assertEqual(speaker._chunks.get_nowait(), "Chrome is ready.")
        self.assertEqual(speaker._pending, "The rest is still generating")

    def test_streaming_speaker_keeps_short_soft_clause_buffered(self):
        speaker = object.__new__(voice.StreamingSpeaker)
        speaker.stop_event = threading.Event()
        speaker.text = ""
        speaker._pending = ""
        speaker._chunks = queue.Queue()
        speaker._chunks_emitted = 0
        speaker._realtime = False

        speaker.feed("Well, still thinking")

        self.assertTrue(speaker._chunks.empty())
        self.assertEqual(speaker._pending, "Well, still thinking")

    def test_file_tts_splits_only_first_chunk_at_commas(self):
        speaker = object.__new__(voice.StreamingSpeaker)
        speaker.stop_event = threading.Event()
        speaker.text = ""
        speaker._pending = ""
        speaker._chunks = queue.Queue()
        speaker._chunks_emitted = 0
        speaker._realtime = False

        speaker.feed("Sure, I opened Chrome for you just now, and YouTube, and then ")

        self.assertEqual(speaker._chunks.get_nowait(), "Sure, I opened Chrome for you just now,")
        self.assertTrue(speaker._chunks.empty())
        speaker.feed("some more. Next")
        self.assertEqual(speaker._chunks.get_nowait(), "and YouTube, and then some more.")

    def test_speech_text_flattens_ellipsis_and_groups_sentences(self):
        self.assertEqual(voice._smooth_speech_text("Opening chrome...  Done\u2026"), "Opening chrome. Done.")
        self.assertEqual(
            voice._group_sentences(["One.", "Two.", "Three."]),
            ["One.", "Two. Three."],
        )

    def test_file_pipeline_merges_queued_chunks_and_plays_in_order(self):
        speaker = object.__new__(voice.StreamingSpeaker)
        speaker.stop_event = threading.Event()
        speaker._chunks = queue.Queue()
        speaker._playback_started = False
        speaker.on_playback_start = None
        for chunk in ("Opening chrome.", "Done, it's up.", "Opened YouTube.", None):
            speaker._chunks.put(chunk)
        synthesized, played = [], []

        def fake_synth(text):
            synthesized.append(text)
            return f"path{len(synthesized)}"

        with (
            patch("voice_playback._synthesize_to_temp_file", side_effect=fake_synth),
            patch("voice_playback._play", side_effect=lambda p, e: played.append(p) or True),
            patch("voice_playback._remove_quietly"),
        ):
            speaker._run_file_pipeline()

        self.assertEqual(synthesized, ["Opening chrome. Done, it's up. Opened YouTube."])
        self.assertEqual(played, ["path1"])

    def test_history_slides_by_character_budget_in_whole_turns(self):
        brain.clear_conversation_history()
        with (
            patch.object(dialogue.config, "CONVERSATION_MEMORY_TURNS", 10),
            patch.object(dialogue.config, "CONVERSATION_MEMORY_CHARACTERS", 20),
        ):
            dialogue._remember_turn("first long user", "first long answer")
            dialogue._remember_turn("latest", "answer")

        self.assertEqual(
            dialogue._conversation_history,
            [
                {"role": "user", "content": "latest"},
                {"role": "assistant", "content": "answer"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
