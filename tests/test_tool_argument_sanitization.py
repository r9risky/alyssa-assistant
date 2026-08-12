"""Regression test for the confirmation-bypass fix in brain.py.

Ensures a model-supplied tool call cannot self-approve a
confirmation-gated action by including "confirmed": true in its own
arguments JSON. See plans/001-strip-confirmed-from-llm-tool-arguments.md.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import brain  # noqa: E402


class TestSanitizeToolArguments(unittest.TestCase):
    def test_strips_confirmed_key(self):
        arguments = {"command": "del important_file.txt", "confirmed": True}
        sanitized = brain._sanitize_tool_arguments(arguments)
        self.assertNotIn("confirmed", sanitized)
        self.assertEqual(sanitized["command"], "del important_file.txt")

    def test_strips_confirmed_key_when_false(self):
        # Even an explicit confirmed: false should not leak through - the
        # key itself is never legitimate model-supplied input.
        arguments = {"path": "C:\\file.txt", "confirmed": False}
        sanitized = brain._sanitize_tool_arguments(arguments)
        self.assertNotIn("confirmed", sanitized)

    def test_leaves_arguments_without_confirmed_untouched(self):
        arguments = {"path": "C:\\file.txt"}
        sanitized = brain._sanitize_tool_arguments(arguments)
        self.assertEqual(sanitized, {"path": "C:\\file.txt"})

    def test_does_not_mutate_the_original_dict(self):
        # func(**arguments) call sites and logging (print(f"[tool] ...")
        # elsewhere in handle_command) should still see a dict if the
        # caller kept a reference to the pre-sanitized version - the helper
        # must not mutate its input in place.
        arguments = {"command": "dir", "confirmed": True}
        brain._sanitize_tool_arguments(arguments)
        self.assertIn("confirmed", arguments)


if __name__ == "__main__":
    unittest.main()
