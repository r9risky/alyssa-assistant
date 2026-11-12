import os
import tempfile
import unittest
from unittest.mock import patch

import actions
import brain
import plugin_loader
from brain import dialogue


PLUGIN = '''
def roll_die(sides: int = 6) -> str:
    return "You rolled a 4."

def handle_intent(cleaned, text=""):
    return "Rolled!" if cleaned == "roll a die" else None

INSTANT_REPLY_TOOLS = {"roll_die"}
FUNCTIONS = {"roll_die": roll_die}
TOOLS = []
'''


class PluginHookTests(unittest.TestCase):
    def tearDown(self):
        brain.clear_conversation_history()

    def _load(self):
        with tempfile.TemporaryDirectory() as folder:
            with open(os.path.join(folder, "dice.py"), "w") as f:
                f.write(PLUGIN)
            with (
                patch.object(plugin_loader, "PLUGINS_DIR", folder),
                patch.object(plugin_loader.plugin_permissions, "needs_approval", return_value=None),
                patch.object(plugin_loader.plugin_permissions, "grandfather_existing"),
            ):
                return plugin_loader.load_plugins()

    def test_loader_collects_intents_and_instant_replies(self):
        try:
            functions, _ = self._load()
            self.assertTrue(functions["roll_die"]._alyssa_instant_reply)
            self.assertEqual(len(plugin_loader.get_intent_handlers()), 1)
            with patch.object(dialogue, "_call_model_with_error_handling") as model:
                self.assertEqual(dialogue._handle_pre_model_intent("Hey Alyssa, roll a die"), "Rolled!")
            model.assert_not_called()
        finally:
            actions.reload_plugins()

    def test_instant_reply_plugin_tool_skips_second_model_call(self):
        def roll_die():
            return "You rolled a 4."
        roll_die._alyssa_instant_reply = True
        turn = {"message": {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "function": {"name": "roll_die", "arguments": {}}}]}}
        with (
            patch.dict(actions.FUNCTIONS, {"roll_die": roll_die}),
            patch.object(dialogue, "_call_model_with_error_handling", side_effect=[(turn, None)]) as model,
        ):
            self.assertEqual(brain.handle_command("roll a die for me"), "You rolled a 4.")
        self.assertEqual(model.call_count, 1)

    def test_call_mode_phrases_live_in_the_call_plugin(self):
        from plugins import call_mode

        with patch.object(call_mode, "leave_call", return_value="Left the call.") as leave:
            self.assertEqual(call_mode.handle_intent("hang up"), "Left the call.")
        leave.assert_called_once()
        self.assertIsNone(call_mode.handle_intent("what's the weather"))


if __name__ == "__main__":
    unittest.main()
