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

    def test_every_step_in_the_first_round_needs_no_second_call(self):
        both = {"message": {"role": "assistant", "content": "", "tool_calls": [
            {"id": "a", "function": {"name": "volume_up", "arguments": {}}},
            {"id": "b", "function": {"name": "media_next_track", "arguments": {}}},
        ]}}
        with (
            patch.dict(actions.FUNCTIONS, {"volume_up": lambda: "Up.", "media_next_track": lambda: "Next."}),
            patch.object(dialogue, "_call_model_with_error_handling",
                         side_effect=[(both, None)]) as model,
        ):
            brain.handle_command("Turn it up and skip this song")
        self.assertEqual(model.call_count, 1)

    def test_step_joins(self):
        self.assertEqual(dialogue._step_joins("Hey Alyssa, open Spotify"), 0)
        self.assertEqual(dialogue._step_joins("open Discord and Chrome"), 1)
        self.assertEqual(dialogue._step_joins("open Notepad, type hi and save it"), 2)

    def test_huge_tool_output_is_cut_before_the_model_sees_it(self):
        final = {"message": {"role": "assistant", "content": "Here.", "tool_calls": []}}
        with (
            patch.dict(actions.FUNCTIONS, {"big_plugin_tool": lambda: "x" * 50000}),
            patch.object(dialogue, "_call_model_with_error_handling",
                         side_effect=[(_tool_turn("big_plugin_tool", {}), None), (final, None)]) as model,
        ):
            brain.handle_command("Run the big tool")
        tool_message = [m for m in model.call_args_list[1].args[0] if m.get("role") == "tool"][0]
        self.assertLess(len(tool_message["content"]), 9000)

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

    def test_gemini_counts_thinking_tokens(self):
        from brain.providers import gemini
        before = usage.today()["output"]
        gemini._record_usage({"promptTokenCount": 10, "candidatesTokenCount": 4, "thoughtsTokenCount": 30})
        self.assertEqual(usage.today()["output"] - before, 34)

    def test_gemini_remembers_a_rejected_thinking_config(self):
        from unittest.mock import MagicMock
        from brain.providers import gemini
        rejected, ok = MagicMock(status_code=400), MagicMock(status_code=200)
        with (
            patch.object(gemini, "_thinking_rejected", False),
            patch.object(gemini._HTTP_SESSION, "post", side_effect=[rejected, ok, ok]) as post,
        ):
            body = {"generationConfig": {"thinkingConfig": {"thinkingLevel": "minimal"}}}
            gemini._post_with_thinking_fallback("u", body)
            body = {"generationConfig": {"thinkingConfig": {"thinkingLevel": "minimal"}}}
            gemini._post_with_thinking_fallback("u", body)
        self.assertEqual(post.call_count, 3)  # the second call skips the doomed first try
        self.assertNotIn("thinkingConfig", post.call_args.kwargs["json"]["generationConfig"])


if __name__ == "__main__":
    unittest.main()
