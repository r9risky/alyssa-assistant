"""Unit tests for brain.py's pure message-format converters and
text-cleanup helpers - no network or OS calls involved. See
plans/004-establish-test-baseline-pure-logic-modules.md.

Precondition: requirements.txt must be installed (`pip install -r
requirements.txt`) for `import brain` to succeed, since brain.py imports
actions.py, which requires pyautogui/pyperclip/requests/send2trash.
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from brain import dialogue, text_utils  # noqa: E402
from brain.providers import anthropic, gemini, openai  # noqa: E402


class TestMessagesToGemini(unittest.TestCase):
    def test_system_message_becomes_system_text(self):
        system_text, contents = gemini._messages_to_gemini(
            [{"role": "system", "content": "You are Alyssa."}]
        )
        self.assertEqual(system_text, "You are Alyssa.")
        self.assertEqual(contents, [])

    def test_user_message_becomes_user_content(self):
        _, contents = gemini._messages_to_gemini([{"role": "user", "content": "hi"}])
        self.assertEqual(contents, [{"role": "user", "parts": [{"text": "hi"}]}])

    def test_assistant_tool_call_becomes_function_call_part(self):
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"function": {"name": "open_app", "arguments": {"app_name": "Chrome"}}}
                ],
            }
        ]
        _, contents = gemini._messages_to_gemini(messages)
        self.assertEqual(contents[0]["role"], "model")
        self.assertEqual(
            contents[0]["parts"][0]["functionCall"],
            {"name": "open_app", "args": {"app_name": "Chrome"}},
        )

    def test_tool_result_uses_function_response_shape(self):
        messages = [{"role": "tool", "name": "open_app", "content": "Opened Chrome.", "id": "call_1"}]
        _, contents = gemini._messages_to_gemini(messages)
        self.assertEqual(contents[0]["role"], "user")
        response = contents[0]["parts"][0]["functionResponse"]
        self.assertEqual(response["name"], "open_app")
        self.assertEqual(response["response"], {"result": "Opened Chrome."})
        self.assertEqual(response["id"], "call_1")


class TestMessagesToOpenAI(unittest.TestCase):
    def test_tool_call_arguments_are_json_encoded_strings(self):
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"function": {"name": "open_app", "arguments": {"app_name": "Chrome"}}}
                ],
            }
        ]
        out = openai._messages_to_openai(messages)
        args = out[0]["tool_calls"][0]["function"]["arguments"]
        self.assertIsInstance(args, str)
        self.assertIn("Chrome", args)

    def test_tool_result_uses_tool_call_id(self):
        messages = [{"role": "tool", "id": "call_1", "content": "Opened Chrome."}]
        out = openai._messages_to_openai(messages)
        self.assertEqual(out[0], {"role": "tool", "tool_call_id": "call_1", "content": "Opened Chrome."})


class TestMessagesToAnthropic(unittest.TestCase):
    def test_tool_call_becomes_tool_use_block(self):
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"function": {"name": "open_app", "arguments": {"app_name": "Chrome"}}, "id": "call_1"}
                ],
            }
        ]
        _, out = anthropic._messages_to_anthropic(messages)
        block = out[0]["content"][0]
        self.assertEqual(block["type"], "tool_use")
        self.assertEqual(block["name"], "open_app")
        self.assertEqual(block["input"], {"app_name": "Chrome"})

    def test_tool_result_becomes_tool_result_block(self):
        messages = [{"role": "tool", "id": "call_1", "content": "Opened Chrome."}]
        _, out = anthropic._messages_to_anthropic(messages)
        block = out[0]["content"][0]
        self.assertEqual(block["type"], "tool_result")
        self.assertEqual(block["tool_use_id"], "call_1")
        self.assertEqual(block["content"], "Opened Chrome.")


class TestTextCleanupHelpers(unittest.TestCase):
    def test_strip_fake_tool_call_removes_json_looking_block(self):
        text = 'Sure! {"name": "open_app", "parameters": {"app_name": "Chrome"}} Done.'
        self.assertEqual(text_utils._strip_fake_tool_call(text), "Sure!  Done.")

    def test_strip_fake_tool_call_leaves_plain_text_alone(self):
        text = "It's currently 3:45 PM."
        self.assertEqual(text_utils._strip_fake_tool_call(text), text)

    def test_is_degenerate_reply_true_for_empty_or_punctuation_only(self):
        self.assertTrue(text_utils._is_degenerate_reply(""))
        self.assertTrue(text_utils._is_degenerate_reply("[]"))
        self.assertTrue(text_utils._is_degenerate_reply("  {}  "))

    def test_is_degenerate_reply_false_for_real_sentence(self):
        self.assertFalse(text_utils._is_degenerate_reply("It's currently 3:45 PM."))

    def test_looks_like_lazy_dodge_true_for_stock_ack(self):
        self.assertTrue(text_utils._looks_like_lazy_dodge("Sure!"))
        self.assertTrue(text_utils._looks_like_lazy_dodge("On it"))

    def test_looks_like_lazy_dodge_false_for_real_answer(self):
        self.assertFalse(text_utils._looks_like_lazy_dodge("It's Tuesday."))

    def test_summarize_for_speech_single_short_line(self):
        self.assertEqual(dialogue._summarize_for_speech("Done."), "Done.")

    def test_summarize_for_speech_multi_line_adds_count(self):
        output = "line one\nline two\nline three"
        self.assertEqual(dialogue._summarize_for_speech(output), "line one (+2 more lines)")

    def test_summarize_for_speech_truncates_long_first_line(self):
        long_line = "x" * 200
        result = dialogue._summarize_for_speech(long_line, max_chars=140)
        self.assertTrue(result.endswith("..."))
        self.assertLessEqual(len(result), 143)  # 140 chars + "..."

    def test_strip_leading_filler_uses_current_assistant_name(self):
        dialogue._strip_leading_filler("Hey Alyssa, say hello")
        with patch.object(dialogue.config, "ASSISTANT_NAME", "Nova"):
            self.assertEqual(dialogue._strip_leading_filler("Hey Nova, say hello"), "say hello")


if __name__ == "__main__":
    unittest.main()
