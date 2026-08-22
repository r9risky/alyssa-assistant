import unittest
from unittest.mock import patch

import config
from brain import dialogue, providers


class LlmRoutingTests(unittest.TestCase):
    def test_auto_tier_keeps_simple_intents_fast(self):
        with patch.object(config, "LLM_REASONING_TIER", "auto"):
            for request in (
                "Set a timer for five minutes",
                "What's the weather?",
                "Pause the music",
            ):
                with self.subTest(request=request):
                    self.assertEqual(dialogue._reasoning_tier(request), "fast")

    def test_auto_tier_defaults_planning_and_unknown_intents_to_strong(self):
        with patch.object(config, "LLM_REASONING_TIER", "auto"):
            self.assertEqual(
                dialogue._reasoning_tier("Check the weather and plan my afternoon"),
                "strong",
            )
            self.assertEqual(dialogue._reasoning_tier("Draft a project plan"), "strong")

    def test_config_can_force_either_tier(self):
        with patch.object(config, "LLM_REASONING_TIER", "strong"):
            self.assertEqual(dialogue._reasoning_tier("Pause the music"), "strong")
        with patch.object(config, "LLM_REASONING_TIER", "fast"):
            self.assertEqual(dialogue._reasoning_tier("Draft a project plan"), "fast")

    def test_strong_tier_uses_configured_provider_when_key_exists(self):
        with (
            patch.object(config, "LLM_PROVIDER", "gemini"),
            patch.object(config, "LLM_STRONG_PROVIDER", "anthropic"),
            patch.object(config, "ANTHROPIC_API_KEY", "configured"),
        ):
            self.assertEqual(providers._provider_for_tier("strong"), ("anthropic", None))

    def test_strong_tier_falls_back_when_key_is_missing(self):
        with (
            patch.object(config, "LLM_PROVIDER", "gemini"),
            patch.object(config, "LLM_STRONG_PROVIDER", "anthropic"),
            patch.object(config, "ANTHROPIC_API_KEY", ""),
        ):
            self.assertEqual(
                providers._provider_for_tier("strong"),
                ("gemini", "anthropic"),
            )

    def test_fallback_notice_is_logged_once_and_not_delivered(self):
        dialogue._fallback_notice_logged = False
        self.addCleanup(dialogue.clear_conversation_history)
        self.addCleanup(setattr, dialogue, "_fallback_notice_logged", False)
        partial_replies = []
        result = {"message": {"content": "Fully operational.", "tool_calls": []}}
        with (
            patch("brain.providers._provider_for_tier", return_value=("gemini", "anthropic")),
            patch.object(
                dialogue, "_call_model_with_error_handling", return_value=(result, None)
            ),
            patch.object(dialogue.telemetry, "log") as log,
        ):
            dialogue.handle_command("First request", on_partial_reply=partial_replies.append)
            dialogue.handle_command("Second request", on_partial_reply=partial_replies.append)

        self.assertEqual(partial_replies, [])
        log.assert_called_once_with(
            "[routing] Anthropic isn't configured, so I'm using Gemini for this request."
        )


if __name__ == "__main__":
    unittest.main()
