import unittest
from unittest.mock import patch

import actions
import brain
from brain import dialogue, providers, tool_registry
from brain.providers import anthropic, gemini
from brain.tool_catalog import BASE_TOOLS


def _schema(name, description):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": {}},
        },
    }


def _names(tools):
    return {tool["function"]["name"] for tool in tools}


class ToolFilteringTests(unittest.TestCase):
    def tearDown(self):
        brain.clear_conversation_history()
        gemini._gemini_tools_cache = None
        anthropic._anthropic_tools_cache = None

    def test_core_floor_is_always_advertised(self):
        with patch.object(tool_registry, "TOOLS", list(BASE_TOOLS)):
            selected_names = _names(tool_registry.select_tools("Tell me a joke."))

        self.assertTrue(tool_registry._CORE_TOOL_NAMES <= selected_names)

    def test_unusual_tools_are_selected_when_relevant(self):
        calendar = _schema(
            "get_next_meeting",
            "Return the next upcoming calendar meeting.",
        )
        camera = _schema(
            "enable_security_camera",
            "Enable webcam security camera motion monitoring.",
        )
        catalog = [*BASE_TOOLS, calendar, camera]

        with patch.object(tool_registry, "TOOLS", catalog):
            calendar_names = _names(tool_registry.select_tools("What's my next calendar meeting?"))
            camera_names = _names(tool_registry.select_tools("Enable the security camera."))

        self.assertIn("get_next_meeting", calendar_names)
        self.assertIn("enable_security_camera", camera_names)
        self.assertLess(len(calendar_names), len(catalog))
        self.assertLess(len(camera_names), len(catalog))

    def test_converted_schema_caches_are_keyed_by_subset(self):
        first_subset = [_schema("calendar_tool", "Calendar")]
        second_subset = [_schema("camera_tool", "Camera")]

        gemini_first = gemini._tools_to_gemini_declarations(first_subset)
        self.assertIs(gemini_first, gemini._tools_to_gemini_declarations(first_subset))
        self.assertEqual(
            ["camera_tool"],
            [item["name"] for item in gemini._tools_to_gemini_declarations(second_subset)],
        )

        anthropic_first = anthropic._tools_to_anthropic(first_subset)
        self.assertIs(anthropic_first, anthropic._tools_to_anthropic(first_subset))
        self.assertEqual(
            ["camera_tool"],
            [item["name"] for item in anthropic._tools_to_anthropic(second_subset)],
        )

    def test_subset_reaches_every_provider_adapter(self):
        subset = [_schema("calendar_tool", "Calendar")]
        adapters = {
            "gemini": "_call_gemini",
            "openai": "_call_openai",
            "anthropic": "_call_anthropic",
            "custom_openai": "_call_custom_openai",
            "ollama": "_call_ollama",
        }

        for provider_name, adapter_name in adapters.items():
            with self.subTest(provider=provider_name), patch.object(
                providers, adapter_name, return_value={}
            ) as adapter:
                providers._call_model([], provider=provider_name, tools=subset)
                self.assertIs(subset, adapter.call_args.args[-1])

    def test_filtered_out_tool_remains_dispatchable(self):
        edge_tool = _schema("calculate_math", "Evaluate a mathematical expression.")
        calls = []
        first = {
            "message": {
                "content": "",
                "tool_calls": [
                    {"id": "edge_1", "function": {"name": "calculate_math", "arguments": {}}}
                ],
            }
        }
        final = {"message": {"content": "Finished.", "tool_calls": []}}

        with (
            patch.object(tool_registry, "TOOLS", [*BASE_TOOLS, edge_tool]),
            patch.dict(actions.FUNCTIONS, {"calculate_math": lambda: calls.append("ran") or "4"}),
            patch("brain.providers._provider_for_tier", return_value=("gemini", None)),
            patch.object(
                dialogue,
                "_call_model_with_error_handling",
                side_effect=[(first, None), (final, None)],
            ) as model,
        ):
            reply = brain.handle_command("Open Chrome.")

        advertised_names = _names(model.call_args_list[0].kwargs["tools"])
        self.assertNotIn("calculate_math", advertised_names)
        self.assertEqual(["ran"], calls)
        self.assertEqual("Finished.", reply)


if __name__ == "__main__":
    unittest.main()
