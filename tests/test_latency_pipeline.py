import json
import os
import queue
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import brain
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
        with patch.object(brain._HTTP_SESSION, "post", return_value=response):
            result = brain._call_openai_compatible(
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

        speaker.feed("Chrome is ready. The rest is still generating")

        self.assertEqual(speaker._chunks.get_nowait(), "Chrome is ready.")
        self.assertEqual(speaker._pending, "The rest is still generating")

    def test_history_slides_by_character_budget_in_whole_turns(self):
        brain.clear_conversation_history()
        with (
            patch.object(brain.config, "CONVERSATION_MEMORY_TURNS", 10),
            patch.object(brain.config, "CONVERSATION_MEMORY_CHARACTERS", 20),
        ):
            brain._remember_turn("first long user", "first long answer")
            brain._remember_turn("latest", "answer")

        self.assertEqual(
            brain._conversation_history,
            [
                {"role": "user", "content": "latest"},
                {"role": "assistant", "content": "answer"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
