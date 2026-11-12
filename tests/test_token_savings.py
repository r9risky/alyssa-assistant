import unittest
from unittest.mock import patch

import actions
import brain
import config
from brain import dialogue, usage


def _tool_turn(name, arguments):
    return {"message": {"role": "assistant", "content": "", "tool_calls": [
        {"id": "call_1", "function": {"name": name, "arguments": arguments}}
    ]}}


class SelfPhrasedToolTests(unittest.TestCase):
    def tearDown(self):
        brain.clear_conversation_history()

    def test_simple_command_costs_one_model_call(self):
        with (
            patch.dict(actions.FUNCTIONS, {"volume_up": lambda: "Turned the volume up."}),
            patch.object(dialogue, "_call_model_with_error_handling",
                         side_effect=[(_tool_turn("volume_up", {}), None)]) as model,
        ):
            reply = brain.handle_command("Turn it up")
        self.assertEqual(model.call_count, 1)
        self.assertIn(reply, {"Turned it up.", "Volume's up.", "Louder now."})

    def test_multi_step_request_still_gets_a_second_turn(self):
        final = {"message": {"role": "assistant", "content": "Done both.", "tool_calls": []}}
        with (
            patch.dict(actions.FUNCTIONS, {"volume_up": lambda: "Turned the volume up."}),
            patch.object(dialogue, "_call_model_with_error_handling",
                         side_effect=[(_tool_turn("volume_up", {}), None), (final, None)]) as model,
        ):
            reply = brain.handle_command("Turn it up and then skip this song")
        self.assertEqual(model.call_count, 2)
        self.assertEqual(reply, "Done both.")

    def test_failed_tool_goes_back_to_the_model(self):
        final = {"message": {"role": "assistant", "content": "Couldn't, sorry.", "tool_calls": []}}
        with (
            patch.dict(actions.FUNCTIONS, {"volume_up": lambda: "Error: no audio device."}),
            patch.object(dialogue, "_call_model_with_error_handling",
                         side_effect=[(_tool_turn("volume_up", {}), None), (final, None)]) as model,
        ):
            brain.handle_command("Turn it up")
        self.assertEqual(model.call_count, 2)


class ChatRoutingTests(unittest.TestCase):
    def test_default_keeps_everything_on_the_main_model(self):
        self.assertEqual(config.LLM_REASONING_TIER, "fast")

    def test_auto_keeps_chit_chat_fast(self):
        with patch.object(config, "LLM_REASONING_TIER", "auto"):
            self.assertEqual(dialogue._reasoning_tier("how was your day?"), "fast")
            self.assertEqual(dialogue._reasoning_tier("open spotify and discord"), "fast")
            self.assertEqual(dialogue._reasoning_tier("research cheap flights to Tokyo"), "strong")


class UsageTests(unittest.TestCase):
    def test_records_add_up(self):
        before = usage.today()
        usage.record("gemini", 100, 20)
        usage.record("gemini", None, 5)
        after = usage.today()
        self.assertEqual(after["input"] - before["input"], 100)
        self.assertEqual(after["output"] - before["output"], 25)
        self.assertEqual(after["calls"] - before["calls"], 2)


if __name__ == "__main__":
    unittest.main()
