"""Unit tests for memory.py's pure scoring/compaction logic and its
file-backed remember/forget/relevant_memories functions. See
plans/004-establish-test-baseline-pure-logic-modules.md.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import memory  # noqa: E402


class TestTokenizeAndScore(unittest.TestCase):
    def test_tokenize_strips_stopwords_and_stems_plurals(self):
        tokens = memory._tokenize("What is my favorite programming languages")
        # stopwords ("what", "is", "my") removed. Note: _tokenize's suffix
        # stripping is naive single-pass, so "programming" -> "programm"
        # (the "ing" suffix is stripped from the whole word, not just a
        # plural ending) and "languages" -> "languag" ("es" stripped) -
        # this is real, existing behavior confirmed against the live code,
        # not the more conservative stemming the plan draft assumed.
        self.assertIn("favorite", tokens)
        self.assertIn("programm", tokens)
        self.assertIn("languag", tokens)
        self.assertNotIn("what", tokens)
        self.assertNotIn("is", tokens)
        self.assertNotIn("my", tokens)

    def test_score_counts_shared_distinct_tokens(self):
        query_tokens = memory._tokenize("play my music")
        fact_tokens = memory._tokenize("prefers Spotify for music")
        self.assertEqual(memory._score(query_tokens, fact_tokens), 1)  # "music"

    def test_score_zero_when_no_overlap(self):
        query_tokens = memory._tokenize("what is the weather")
        fact_tokens = memory._tokenize("the golden retriever is named Max")
        self.assertEqual(memory._score(query_tokens, fact_tokens), 0)


class TestCleanAndCompact(unittest.TestCase):
    def test_clean_fact_normalizes_whitespace(self):
        self.assertEqual(memory._clean_fact("  hello   world  \n"), "hello world")

    def test_clean_fact_respects_configured_length_limit(self):
        long_fact = "x" * 500
        cleaned = memory._clean_fact(long_fact)
        self.assertLessEqual(len(cleaned), 400)  # config.MAX_MEMORY_FACT_CHARACTERS default

    def test_compact_dedupes_case_insensitively(self):
        result = memory._compact(["likes cats", "Likes Cats", "likes dogs"])
        self.assertEqual(result, ["likes cats", "likes dogs"])

    def test_compact_bounds_to_max_saved_memories(self):
        many = [f"fact number {i}" for i in range(200)]
        result = memory._compact(many)
        self.assertLessEqual(len(result), 75)  # config.MAX_SAVED_MEMORIES default
        self.assertEqual(result[-1], "fact number 199")  # keeps the newest


class TestFileBackedOperations(unittest.TestCase):
    def setUp(self):
        # memory.py caches in module globals - reset them and point
        # MEMORY_FILE at a fresh temp file so tests don't touch the real
        # memory.json or leak state between tests.
        self._tmpdir = tempfile.TemporaryDirectory()
        self._original_memory_file = memory.MEMORY_FILE
        memory.MEMORY_FILE = os.path.join(self._tmpdir.name, "memory.json")
        memory._MEMORIES_CACHE = None
        memory._MEMORIES_CACHE_MTIME = None

    def tearDown(self):
        memory.MEMORY_FILE = self._original_memory_file
        memory._MEMORIES_CACHE = None
        memory._MEMORIES_CACHE_MTIME = None
        self._tmpdir.cleanup()

    def test_remember_then_load(self):
        memory.remember("likes iced coffee")
        self.assertIn("likes iced coffee", memory.load_memories())

    def test_remember_does_not_duplicate(self):
        memory.remember("likes iced coffee")
        memory.remember("Likes Iced Coffee")  # different case, same fact
        self.assertEqual(memory.load_memories().count("likes iced coffee"), 1)

    def test_forget_removes_matching_fact(self):
        memory.remember("prefers Spotify for music")
        result = memory.forget("spotify")
        self.assertIn("prefers Spotify for music", result)
        self.assertNotIn("prefers Spotify for music", memory.load_memories())

    def test_forget_no_match_returns_message_without_error(self):
        result = memory.forget("something never saved")
        self.assertIn("couldn't find", result.lower())

    def test_relevant_memories_ranks_by_overlap(self):
        memory.remember("prefers Spotify for music")
        memory.remember("the golden retriever is named Max")
        results = memory.relevant_memories("play my music", limit=5)
        self.assertIn("prefers Spotify for music", results)


if __name__ == "__main__":
    unittest.main()
