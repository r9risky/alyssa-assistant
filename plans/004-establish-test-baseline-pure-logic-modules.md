# Plan 004: Establish a pytest baseline and cover the pure-logic modules

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: This repository has no git history (verified
> during the audit — `git rev-parse --short HEAD` fails with "not a git
> repository"), so no commit-based drift check is possible. Instead, open
> the files cited under "Current state" below at the given line numbers and
> confirm the code matches the quoted excerpts before writing tests against
> them — this plan is unusually sensitive to drift since it pins down exact
> current behavior; a mismatch means the excerpt is stale and the
> corresponding test needs to be rewritten against the real code, not the
> plan's version of it.
>
> **Run this plan after Plan 001 and Plan 003 if executing in order** — not
> because it technically depends on them, but because both of those plans
> create `tests/__init__.py` and add their own test files under `tests/`,
> and this plan adds the shared `pytest.ini` those files benefit from too.
> If `tests/__init__.py` already exists when you start, that's expected —
> do not recreate it.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: LOW
- **Depends on**: none (see the ordering note above — soft dependency only)
- **Category**: tests
- **Planned at**: no git repository (repo has no `.git` directory) — N/A
- **Issue**: (none — not published)

## Why this matters

There are zero automated tests anywhere in this ~13,800-line codebase
(confirmed during the audit: no `test_*.py`, `*_test.py`, or `tests/`
directory exists). That means every change — including the fixes in Plans
001–003 — currently has no way to be checked for regressions except
running the full voice assistant by hand on a Windows machine with a
microphone, an LLM provider configured, and a screen to watch. For a
project this size, with this much branching logic (five different LLM
wire formats normalized in `brain.py` alone, a 1,735-line action library,
a keyword-overlap memory-matching algorithm), that's a real risk for any
future refactor: nothing catches a broken message-format conversion, a
memory-scoring regression, or a name-detection false negative until a
person notices Alyssa behaving oddly in actual use.

This plan doesn't try to test everything — most of the codebase (audio
capture, TTS playback, the Qt GUI, Windows-specific `ctypes`/`winreg`
calls, actual LLM HTTP calls) either requires hardware/network access or
is Windows-only and awkward to characterize cheaply. Instead it establishes
the *pattern* (a `pytest.ini` plus a `tests/` convention already seeded by
Plans 001/003) and covers the highest-value, easiest-to-test slice: the
pure-logic modules and functions that have no I/O, no OS dependency, and
no network call — `memory.py`'s scoring/compaction logic, `nameutil.py`'s
name-detection regex, and `brain.py`'s provider-format converters and
text-cleanup helpers. These are exactly the functions most likely to be
touched by a future "add a 6th LLM provider" or "change how memory
matching works" change, and the ones where a silent regression is hardest
to notice by eye.

## Current state

- No `tests/` directory, `pytest.ini`, `pyproject.toml`, or any CI config
  exists in this repo (confirmed via `find` during recon).
- `pytest` is not in `requirements.txt` — it needs to be installed
  separately for this plan (it is a dev-only dependency, deliberately kept
  out of `requirements.txt`, which lists only what the running assistant
  itself needs).
- The functions this plan adds coverage for, and why each is safe to test
  without mocking hardware/network/OS:

  - `memory.py` — the whole module is pure functions plus simple file I/O
    against a path in `MEMORY_FILE` (`memory.py:19`), which can be
    monkeypatched to a temp file:
    - `_tokenize` (`memory.py:32-45`), `_score` (`memory.py:48-52`),
      `_clean_fact` (`memory.py:55-58`), `_compact` (`memory.py:61-74`) —
      pure functions, no I/O at all.
    - `remember` (`memory.py:174-183`), `forget` (`memory.py:186-194`),
      `relevant_memories` (`memory.py:136-171`) — go through
      `load_memories()`/`save_memories()`, which read/write `MEMORY_FILE`
      and cache in the module-level globals `_MEMORIES_CACHE` /
      `_MEMORIES_CACHE_MTIME` (`memory.py:22-23`). Tests must reset both
      globals and point `MEMORY_FILE` at a temp file per test (see Step 2).

  - `nameutil.py` — every function is pure and already reviewed in full
    during the audit (the whole file is 52 lines): `contains_name`,
    `find_name_span`, `strip_name_at_span`, both built on
    `functools.cache`d `name_variants()`/`name_pattern()` that read
    `config.ASSISTANT_NAME`/`config.ASSISTANT_NAME_ALIASES`.

  - `brain.py` — the message-format converters and text-cleanup helpers
    are pure functions with no network/OS calls (verified by reading each
    one in full during the audit):
    - `_messages_to_gemini` (`brain.py:1196-1245`)
    - `_messages_to_openai` (`brain.py:1341-1378`)
    - `_messages_to_anthropic` (`brain.py:1481-1529`)
    - `_strip_fake_tool_call` (`brain.py:1966-1999`)
    - `_is_degenerate_reply` (`brain.py:2002-2010`)
    - `_looks_like_lazy_dodge` (`brain.py:2027-2030`)
    - `_summarize_for_speech` (`brain.py:930-946`)

    Importing `brain.py` transitively imports `actions.py`, which requires
    `pyautogui`, `pyperclip`, `requests`, and `send2trash` to be installed
    (confirmed during the audit: on a machine with none of
    `requirements.txt` installed, `import brain` fails with
    `ModuleNotFoundError: No module named 'pyautogui'`). This is expected
    and not a bug to fix — just a precondition: `pip install -r
    requirements.txt` must be run before `brain.py`'s tests can import it.
    None of `actions.py`'s Windows-specific `ctypes.windll`/`winreg` calls
    execute at import time on a non-Windows machine (they're guarded by
    `if os.name == "nt"`), so `import brain` itself succeeds cross-platform
    once the pip dependencies are present — confirmed during the audit on
    a Linux sandbox.

- Explicitly **not** covered by this plan (see Scope): `actions.py`'s path
  helpers (`_resolve_placeholder_user_path`, `_friendly_file_name`, etc.)
  build and compare Windows-style backslash paths and call
  `os.path.normpath`/`winreg`, whose behavior differs between Windows and
  POSIX — testing them meaningfully needs either a Windows runner or
  careful platform-conditional assertions, which is more than this plan's
  effort budget covers. Flagged as a good follow-up (see Maintenance
  notes).

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Install pytest (dev-only, not added to `requirements.txt`) | `pip install pytest` | exit 0 |
| Install the app's own runtime deps (needed for `brain.py` tests to import) | `pip install -r requirements.txt` (from inside `alyssa_assistant/`) | exit 0 |
| Run the full test suite | `pytest -v` (from inside `alyssa_assistant/`) | all tests pass, including the ones added by Plans 001 and 003 if already present |
| Run just this plan's new tests | `pytest tests/test_memory.py tests/test_nameutil.py tests/test_brain_message_conversion.py -v` | all pass |

## Scope

**In scope** (the only files you should modify or create):
- `pytest.ini` (new file, repo root i.e. `alyssa_assistant/pytest.ini`)
- `tests/__init__.py` — create only if it doesn't already exist (Plan 001
  or Plan 003 may have created it first — check before creating)
- `tests/test_memory.py` (new)
- `tests/test_nameutil.py` (new)
- `tests/test_brain_message_conversion.py` (new)

**Out of scope** (do NOT touch, even though they look related):
- `memory.py`, `nameutil.py`, `brain.py` themselves — this plan adds tests
  only, no production code changes (if a test reveals an actual bug,
  STOP and report it rather than fixing it inline — see STOP conditions).
- `actions.py`'s path-resolution functions (`_resolve_placeholder_user_path`,
  `_friendly_file_name`, `_friendly_site_name`, `_desktop_folder`) — see
  "Current state" above for why these are deliberately excluded.
- Any GUI (`overlay.py`), audio (`recorder.py`, `transcribe.py`,
  `voice.py`, `voice_id.py`), or live-network code (`_call_ollama`,
  `_call_gemini`, `_call_openai_compatible`, `_call_anthropic` — the
  functions that actually make HTTP requests, as opposed to the pure
  `_messages_to_*` converters this plan does cover).
- `requirements.txt` — `pytest` is a dev-only tool for running these
  tests, not a runtime dependency of the assistant; do not add it there.

## Git workflow

This repository has no `.git` directory (confirmed during recon). Do not
attempt to create one, commit, or branch — just make the file changes
directly.

## Steps

### Step 1: Add `pytest.ini`

Create `alyssa_assistant/pytest.ini`:

```ini
[pytest]
testpaths = tests
```

This is intentionally minimal — it just points `pytest` (run from the
repo root) at the `tests/` directory, matching where Plans 001 and 003
already place their test files.

**Verify**: `pytest --collect-only` (from inside `alyssa_assistant/`) →
exits 0 and lists any test files already present under `tests/` (may be
empty/zero if this plan runs before Plans 001/003).

### Step 2: Write `tests/test_memory.py`

Create `tests/__init__.py` (empty file) first, only if it doesn't already
exist.

Create `tests/test_memory.py`:

```python
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
        # stopwords ("what", "is", "my") removed; "languages" stemmed to "language"
        self.assertIn("favorite", tokens)
        self.assertIn("programming", tokens)
        self.assertIn("language", tokens)
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
```

**Verify**: `pytest tests/test_memory.py -v` (from inside
`alyssa_assistant/`) → all tests pass.

### Step 3: Write `tests/test_nameutil.py`

Create `tests/test_nameutil.py`:

```python
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
```

**Verify**: `pytest tests/test_nameutil.py -v` → all tests pass.

### Step 4: Write `tests/test_brain_message_conversion.py`

Create `tests/test_brain_message_conversion.py`:

```python
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

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import brain  # noqa: E402


class TestMessagesToGemini(unittest.TestCase):
    def test_system_message_becomes_system_text(self):
        system_text, contents = brain._messages_to_gemini(
            [{"role": "system", "content": "You are Alyssa."}]
        )
        self.assertEqual(system_text, "You are Alyssa.")
        self.assertEqual(contents, [])

    def test_user_message_becomes_user_content(self):
        _, contents = brain._messages_to_gemini([{"role": "user", "content": "hi"}])
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
        _, contents = brain._messages_to_gemini(messages)
        self.assertEqual(contents[0]["role"], "model")
        self.assertEqual(
            contents[0]["parts"][0]["functionCall"],
            {"name": "open_app", "args": {"app_name": "Chrome"}},
        )

    def test_tool_result_uses_function_response_shape(self):
        messages = [{"role": "tool", "name": "open_app", "content": "Opened Chrome.", "id": "call_1"}]
        _, contents = brain._messages_to_gemini(messages)
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
        out = brain._messages_to_openai(messages)
        args = out[0]["tool_calls"][0]["function"]["arguments"]
        self.assertIsInstance(args, str)
        self.assertIn("Chrome", args)

    def test_tool_result_uses_tool_call_id(self):
        messages = [{"role": "tool", "id": "call_1", "content": "Opened Chrome."}]
        out = brain._messages_to_openai(messages)
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
        _, out = brain._messages_to_anthropic(messages)
        block = out[0]["content"][0]
        self.assertEqual(block["type"], "tool_use")
        self.assertEqual(block["name"], "open_app")
        self.assertEqual(block["input"], {"app_name": "Chrome"})

    def test_tool_result_becomes_tool_result_block(self):
        messages = [{"role": "tool", "id": "call_1", "content": "Opened Chrome."}]
        _, out = brain._messages_to_anthropic(messages)
        block = out[0]["content"][0]
        self.assertEqual(block["type"], "tool_result")
        self.assertEqual(block["tool_use_id"], "call_1")
        self.assertEqual(block["content"], "Opened Chrome.")


class TestTextCleanupHelpers(unittest.TestCase):
    def test_strip_fake_tool_call_removes_json_looking_block(self):
        text = 'Sure! {"name": "open_app", "parameters": {"app_name": "Chrome"}} Done.'
        self.assertEqual(brain._strip_fake_tool_call(text), "Sure!  Done.")

    def test_strip_fake_tool_call_leaves_plain_text_alone(self):
        text = "It's currently 3:45 PM."
        self.assertEqual(brain._strip_fake_tool_call(text), text)

    def test_is_degenerate_reply_true_for_empty_or_punctuation_only(self):
        self.assertTrue(brain._is_degenerate_reply(""))
        self.assertTrue(brain._is_degenerate_reply("[]"))
        self.assertTrue(brain._is_degenerate_reply("  {}  "))

    def test_is_degenerate_reply_false_for_real_sentence(self):
        self.assertFalse(brain._is_degenerate_reply("It's currently 3:45 PM."))

    def test_looks_like_lazy_dodge_true_for_stock_ack(self):
        self.assertTrue(brain._looks_like_lazy_dodge("Sure!"))
        self.assertTrue(brain._looks_like_lazy_dodge("On it"))

    def test_looks_like_lazy_dodge_false_for_real_answer(self):
        self.assertFalse(brain._looks_like_lazy_dodge("It's Tuesday."))

    def test_summarize_for_speech_single_short_line(self):
        self.assertEqual(brain._summarize_for_speech("Done."), "Done.")

    def test_summarize_for_speech_multi_line_adds_count(self):
        output = "line one\nline two\nline three"
        self.assertEqual(brain._summarize_for_speech(output), "line one (+2 more lines)")

    def test_summarize_for_speech_truncates_long_first_line(self):
        long_line = "x" * 200
        result = brain._summarize_for_speech(long_line, max_chars=140)
        self.assertTrue(result.endswith("..."))
        self.assertLessEqual(len(result), 143)  # 140 chars + "..."


if __name__ == "__main__":
    unittest.main()
```

**Verify**: `pytest tests/test_brain_message_conversion.py -v` (from
inside `alyssa_assistant/`, with `requirements.txt` installed) → all
tests pass.

## Test plan

This plan's "steps" *are* the test plan — three new test files, described
step by step above:

- `tests/test_memory.py` — 4 pure-function tests (`_tokenize`, `_score`,
  `_clean_fact`, `_compact`) plus 5 file-backed tests (`remember`,
  `forget`, `relevant_memories`) using a temp file and reset module
  globals.
- `tests/test_nameutil.py` — 6 tests for `contains_name`/`find_name_span`/
  `strip_name_at_span`, covering exact match, configured alias, case
  insensitivity, no-match, whole-word boundary, and empty/None input.
- `tests/test_brain_message_conversion.py` — 14 tests across the three
  provider converters and the four text-cleanup helpers.
- No existing test file to model new tests after (this plan, together
  with Plans 001 and 003, is the first test suite in the repo) — the style
  used here (stdlib `unittest.TestCase`, one class per function/concern,
  `sys.path.insert` for the flat non-package layout) is what future test
  files in this repo should follow, since it's now the established
  pattern.
- Verification: `pytest -v` (from inside `alyssa_assistant/`) → all tests
  across the whole `tests/` directory pass, including any added by Plans
  001/003.

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `pytest.ini` exists with `testpaths = tests`
- [ ] `pytest tests/test_memory.py -v` exits 0, all tests pass
- [ ] `pytest tests/test_nameutil.py -v` exits 0, all tests pass
- [ ] `pytest tests/test_brain_message_conversion.py -v` exits 0, all
      tests pass (after `pip install -r requirements.txt`)
- [ ] `pytest -v` (whole suite) exits 0
- [ ] No files outside the in-scope list are modified
- [ ] `plans/README.md` status row for 004 updated to `DONE`

## STOP conditions

Stop and report back (do not improvise) if:

- Any excerpt under "Current state" doesn't match the live code at that
  location — the function may have changed behavior since this plan was
  written; report the mismatch rather than writing a test against
  guessed-at current behavior.
- A test you write based on this plan's expected values actually *fails*
  against unmodified production code — that means either the plan's
  understanding of the function is wrong (fix the test to match real
  behavior and note the discrepancy in your report) or the function has an
  actual bug (do NOT fix `memory.py`/`nameutil.py`/`brain.py` yourself —
  this plan is test-only; report the suspected bug for a separate plan).
- `import brain` fails for a reason *other than* missing
  `requirements.txt` dependencies (e.g. a genuine `SyntaxError` or a
  new import that fails even on Windows) — report it rather than
  skipping the `brain.py` tests silently.
- `config.ASSISTANT_NAME` or `config.ASSISTANT_NAME_ALIASES` no longer
  contains `"Alyssa"` / `"alissa"` respectively (checked at
  `config.py:7-12` during the audit) — the `nameutil.py` tests hardcode
  these two specific values; if they've changed, update the test literals
  to match the new config rather than leaving stale assertions in place.

## Maintenance notes

- `memory.py`'s tests reset `memory._MEMORIES_CACHE` /
  `memory._MEMORIES_CACHE_MTIME` and monkeypatch `memory.MEMORY_FILE` in
  `setUp`/`tearDown` — any future test added to `tests/test_memory.py`
  that calls `remember`/`forget`/`load_memories`/`save_memories` must
  extend the same `TestFileBackedOperations` class (or replicate its
  setUp/tearDown) rather than running against the real `memory.json`.
- The `nameutil.py` tests rely on `config.ASSISTANT_NAME` /
  `config.ASSISTANT_NAME_ALIASES` staying at their current values (see the
  last STOP condition) — because `nameutil.name_variants()` and
  `name_pattern()` are `functools.cache`d, they're computed once per
  process from whatever `config.py` said at first call; a test suite that
  wants to test *custom* name configs would need to clear those caches
  (`nameutil.name_variants.cache_clear()`, `nameutil.name_pattern.cache_clear()`)
  between cases — not needed for this plan's tests, but worth knowing for
  future ones.
- Explicitly deferred out of this plan (see "Current state" and Scope):
  `actions.py`'s Windows-path-format helpers, the actual network-calling
  `_call_*` functions in `brain.py` (would need HTTP mocking — a
  reasonable Plan 005 candidate), and anything in `overlay.py`
  (Qt GUI, would need `pytest-qt` or similar — a bigger lift).
- A reviewer adding a 6th LLM provider in the future should add a matching
  `TestMessagesTo<Provider>` class to `tests/test_brain_message_conversion.py`
  alongside the new `_messages_to_<provider>()` function, following the
  existing three classes as the pattern.
