import unittest
from unittest.mock import patch

import actions
import brain
from brain import dialogue


class ToolChainingTests(unittest.TestCase):
    def tearDown(self):
        brain.clear_conversation_history()

    def test_dependent_tools_chain_across_model_turns(self):
        calls = []
        first = {"message": {"role": "assistant", "content": "", "tool_calls": [
            {"id": "read_1", "function": {"name": "read_value", "arguments": {}}}
        ]}}
        second = {"message": {"role": "assistant", "content": "", "tool_calls": [
            {"id": "use_1", "function": {
                "name": "use_value", "arguments": {"value": "12"}
            }}
        ]}}
        final = {"message": {"role": "assistant", "content": "Both steps complete.", "tool_calls": []}}
        functions = {
            "read_value": lambda: calls.append("read") or "12",
            "use_value": lambda value: calls.append(("use", value)) or "done",
        }

        with (
            patch.dict(actions.FUNCTIONS, functions),
            patch.object(dialogue.config, "FAST_TOOL_RESPONSES", True),
            patch.object(dialogue, "_call_model_with_error_handling",
                         side_effect=[(first, None), (second, None), (final, None)]) as model,
        ):
            reply = brain.handle_command("Read the value, then use it.")

        self.assertEqual(reply, "Both steps complete.")
        self.assertEqual(calls, ["read", ("use", "12")])
        self.assertEqual(model.call_count, 3)
        messages = model.call_args_list[1].args[0]
        tool_index = next(i for i, message in enumerate(messages) if message["role"] == "tool")
        self.assertEqual([m["role"] for m in messages[tool_index - 1:tool_index + 1]], ["assistant", "tool"])
        self.assertEqual(messages[tool_index]["content"], "12")
        self.assertIn("multiple tools in sequence", messages[0]["content"])
