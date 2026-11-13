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

    def test_multi_tool_command_announces_progress_once(self):
        self.addCleanup(dialogue.clear_conversation_history)
        partial_replies = []
        tool_turn = {"message": {"content": "", "tool_calls": [
            {"function": {"name": "open_app", "arguments": {"app_name": "chrome"}}},
            {"function": {"name": "open_url", "arguments": {"url": "https://youtube.com"}}},
        ]}}
        final_turn = {"message": {"content": "", "tool_calls": []}}
        tools = {"open_app": lambda **_: "Opened chrome.", "open_url": lambda **_: "Opened YouTube for you."}
        with (
            patch("brain.providers._provider_for_tier", return_value=("gemini", None)),
            patch.object(
                dialogue, "_call_model_with_error_handling",
                side_effect=[(tool_turn, None), (final_turn, None)],
            ),
            patch.dict(dialogue.actions.FUNCTIONS, tools),
        ):
            dialogue.handle_command("open chrome and youtube", on_partial_reply=partial_replies.append)

        progress = [r for r in partial_replies if r.endswith("...")]
        self.assertEqual(len(progress), 1, partial_replies)

    def test_auto_tier_sends_coding_to_the_heavy_model(self):
        with patch.object(config, "LLM_REASONING_TIER", "auto"):
            self.assertEqual(dialogue._reasoning_tier("Write me a python script"), "strong")
            self.assertEqual(dialogue._reasoning_tier("How are you today?"), "fast")

    def test_heavy_model_only_applies_to_heavy_tasks_on_its_own_service(self):
        with (
            patch.object(config, "LLM_STRONG_PROVIDER", "gemini"),
            patch.object(config, "LLM_STRONG_MODEL", "gemini-4-hydrogen"),
        ):
            self.assertEqual(providers._model_for_tier("strong", "gemini"), "gemini-4-hydrogen")
            self.assertIsNone(providers._model_for_tier("fast", "gemini"))
            # Heavy service had no key, so it fell back to the main one: keep the main chat model.
            self.assertIsNone(providers._model_for_tier("strong", "openai"))
        with patch.object(config, "LLM_STRONG_MODEL", ""):
            self.assertIsNone(providers._model_for_tier("strong", config.LLM_STRONG_PROVIDER))

    def test_same_service_routes_chat_and_research_to_different_models(self):
        self.addCleanup(dialogue.clear_conversation_history)
        reply = {"message": {"content": "Penguins mate for life, which is sweet.", "tool_calls": []}}
        with (
            patch.object(config, "LLM_PROVIDER", "gemini"),
            patch.object(config, "GEMINI_API_KEY", "key"),
            patch.object(config, "GEMINI_MODEL", "gemini-3.5-flash-lite"),
            patch.object(config, "LLM_REASONING_TIER", "auto"),
            patch.object(config, "LLM_STRONG_PROVIDER", "gemini"),
            patch.object(config, "LLM_STRONG_MODEL", "gemini-4-hydrogen"),
            patch.object(providers, "_call_gemini", return_value=reply) as gemini,
        ):
            dialogue.handle_command("Tell me a joke")
            dialogue.handle_command("Research the best budget laptops")

        self.assertEqual([c.kwargs["model"] for c in gemini.call_args_list], [None, "gemini-4-hydrogen"])


if __name__ == "__main__":
    unittest.main()
