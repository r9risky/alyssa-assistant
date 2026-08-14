"""Unit tests for nameutil.py's name-detection logic. See
plans/004-establish-test-baseline-pure-logic-modules.md.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import nameutil  # noqa: E402


class TestContainsName(unittest.TestCase):
    def test_detects_exact_name(self):
        self.assertTrue(nameutil.contains_name("Alyssa, open notepad"))

    def test_detects_configured_alias(self):
        # "alissa" is one of config.ASSISTANT_NAME_ALIASES
        self.assertTrue(nameutil.contains_name("hey alissa what time is it"))

    def test_case_insensitive(self):
        self.assertTrue(nameutil.contains_name("ALYSSA open chrome"))

    def test_no_match_on_unrelated_text(self):
        self.assertFalse(nameutil.contains_name("what's the weather today"))

    def test_whole_word_only_no_partial_match(self):
        # "Alyssa" must not match inside an unrelated longer word
        self.assertFalse(nameutil.contains_name("melissandra is a name"))

    def test_empty_and_none_text(self):
        self.assertFalse(nameutil.contains_name(""))
        self.assertFalse(nameutil.contains_name(None))


class TestStopRequest(unittest.TestCase):
    def test_accepts_name_then_stop(self):
        self.assertTrue(nameutil.is_stop_request("Alyssa, stop"))

    def test_accepts_stop_then_name(self):
        self.assertTrue(nameutil.is_stop_request("stop Alyssa"))

    def test_requires_both_words(self):
        self.assertFalse(nameutil.is_stop_request("Alyssa, keep going"))
        self.assertFalse(nameutil.is_stop_request("please stop"))

    def test_stop_must_be_a_whole_word(self):
        self.assertFalse(nameutil.is_stop_request("Alyssa, unstoppable"))


class TestStripNameAtSpan(unittest.TestCase):
    def test_strips_leading_name(self):
        text = "Alyssa, open notepad"
        span = nameutil.find_name_span(text)
        self.assertEqual(nameutil.strip_name_at_span(text, span), "open notepad")

    def test_strips_trailing_name(self):
        text = "open notepad, Alyssa"
        span = nameutil.find_name_span(text)
        self.assertEqual(nameutil.strip_name_at_span(text, span), "open notepad")

    def test_name_only_returns_empty_string(self):
        text = "Alyssa"
        span = nameutil.find_name_span(text)
        self.assertEqual(nameutil.strip_name_at_span(text, span), "")


if __name__ == "__main__":
    unittest.main()
