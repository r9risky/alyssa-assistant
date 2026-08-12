# Plan 003: Declare `beautifulsoup4` as a real dependency

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: This repository has no git history (verified
> during the audit — `git rev-parse --short HEAD` fails with "not a git
> repository"), so no commit-based drift check is possible. Instead, run
> `grep -n "bs4\|BeautifulSoup" alyssa_assistant/requirements.txt
> alyssa_assistant/plugins/web_summarizer.py` and confirm the output matches
> "Current state" below before making any change — if `requirements.txt`
> already lists `beautifulsoup4` or `bs4`, treat that as a STOP condition
> (someone may have already fixed this).

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: bug
- **Planned at**: no git repository (repo has no `.git` directory) — N/A
- **Issue**: (none — not published)

## Why this matters

`plugins/web_summarizer.py` gives Alyssa its `summarize_webpage` tool,
which fetches a URL and extracts readable text for the model to summarize.
It tries to import `BeautifulSoup` from `bs4` and, if that import fails,
silently falls back to a much cruder regex-based HTML stripper (see
"Current state" below for both code paths). The problem: `bs4` is never
listed in `requirements.txt`, so on every fresh install done exactly the
way `README.md` instructs (`pip install -r requirements.txt`, or the
equivalent automatic install `start_alyssa.bat` performs on first run),
the import fails and every single use of `summarize_webpage` silently uses
the weaker fallback — nobody following the documented setup steps ever
gets the parser path the code clearly prefers and was written first.

The fallback isn't broken, exactly — it does the same "detect
Python.list", the regex fallback keeps `<script>`/`<style>` tag *content*
out but does nothing about `<nav>`, `<footer>`, `<header>`, or `<aside>`
sections the BeautifulSoup path explicitly excludes (see the comment
"Remove script, style, nav, footer tags" at `plugins/web_summarizer.py:46`),
so real installs get navigation menus, footers, and sidebar boilerplate
mixed into the 2000-character snippet handed to the LLM for
summarization — silently lower-quality answers to "summarize this
article," with no error or warning surfaced anywhere.

## Current state

- `plugins/web_summarizer.py:9-14` — the conditional import:

  ```python
  try:
      from bs4 import BeautifulSoup
      _BS4_AVAILABLE = True
  except ImportError:
      BeautifulSoup = None
      _BS4_AVAILABLE = False
  ```

- `plugins/web_summarizer.py:43-53` — the preferred path (what should run
  after this fix, on every standard install):

  ```python
      if _BS4_AVAILABLE:
          soup = BeautifulSoup(html, "html.parser")
          # Remove script, style, nav, footer tags
          for element in soup(["script", "style", "nav", "footer", "header", "aside"]):
              element.extract()

          title = soup.title.string.strip() if soup.title and soup.title.string else ""

          # Extract text from paragraphs and headings
          paragraphs = [p.get_text().strip() for p in soup.find_all(["p", "h1", "h2", "h3"]) if p.get_text().strip()]
          text_content = " ".join(paragraphs)
  ```

- `plugins/web_summarizer.py:54-66` — the fallback everyone currently gets
  instead (do not modify this — it stays as a genuine fallback for the
  rare case `bs4` still fails to import, e.g. a broken environment):

  ```python
      else:
          # Simple regex fallback
          title_match = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
          if title_match:
              title = re.sub(r"<[^>]+>", "", title_match.group(1)).strip()

          body_match = re.search(r"<body.*?>(.*?)</body>", html, re.IGNORECASE | re.DOTALL)
          body_html = body_match.group(1) if body_match else html

          # Strip scripts and styles
          clean_html = re.sub(r"<(script|style).*?>.*?</\1>", "", body_html, flags=re.IGNORECASE | re.DOTALL)
          clean_text = re.sub(r"<[^>]+>", " ", clean_html)
          text_content = " ".join(clean_text.split())
  ```

- `requirements.txt` (full file checked during the audit) — `bs4` /
  `beautifulsoup4` does not appear anywhere in it. The file's existing
  style: each dependency is a single bare line (e.g. `requests`,
  `pyperclip`), with an explanatory `#`-comment placed *above* a package
  only when the reason for including it isn't obvious from the name alone
  (compare the bare `requests` line with the multi-line comment above
  `pygame-ce`). The last few lines of the file, for exact insertion-point
  context:

  ```
  # calendar + gmail (plugins/calendar_gmail.py) - also needs a
  # credentials.json from Google Cloud Console; see that file's docstring
  google-auth-oauthlib
  google-api-python-client
  ```

- Repo conventions: comments in `requirements.txt` explain *why* a
  dependency is there and *which plugin* uses it, matching the style seen
  for `psutil` ("system diagnostics (plugins/system_watch.py) -
  CPU/RAM/disk/battery") and `opencv-python` ("security camera motion
  detection (plugins/security_camera.py) - comment out if you don't want
  the extra ~80MB..."). Match this exactly for the new line.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Confirm `bs4` is currently missing | `grep -in "bs4\|beautifulsoup" requirements.txt` (from inside `alyssa_assistant/`) | no output (no match) |
| Confirm the import currently fails in a clean environment | `python -c "import bs4"` in a fresh venv with only `requirements.txt` installed | `ModuleNotFoundError: No module named 'bs4'` (this is the bug being fixed — expected to fail *before* Step 1, and succeed *after* Step 2) |
| Install the new dependency | `pip install beautifulsoup4` (or `pip install -r requirements.txt` after Step 1 edits it in) | exit 0 |
| Confirm the plugin now takes the BeautifulSoup path | see Step 3 below | prints `True` |

## Scope

**In scope** (the only files you should modify):
- `requirements.txt`
- `tests/__init__.py` — create if it doesn't already exist (see Plan 001,
  which may have created it first — check before creating).
- `tests/test_web_summarizer_bs4_available.py` — new test file.

**Out of scope** (do NOT touch, even though they look related):
- `plugins/web_summarizer.py` — the import/fallback logic itself is
  correct as written and needs no code change; this plan only fixes the
  missing dependency declaration.
- `requirements-gpu.txt` — unrelated to this fix; `bs4` is a small,
  pure-Python-plus-C-extension package needed on every install, not a GPU
  extra.
- Any other plugin's dependencies.

## Git workflow

This repository has no `.git` directory (confirmed during recon). Do not
attempt to create one, commit, or branch — just make the file changes
directly.

## Steps

### Step 1: Add `beautifulsoup4` to `requirements.txt`

Append the following near the other plugin-specific entries — after the
`google-api-python-client` line at the end of the file (shown in "Current
state" above) is fine, or anywhere among the other plugin dependencies;
exact position doesn't matter as long as it's grouped with a comment like
its neighbors:

```
# web page / article summarization (plugins/web_summarizer.py) - used for
# proper HTML parsing (strips nav/footer/header/aside sections cleanly);
# without it, summarize_webpage() silently falls back to a cruder regex
# HTML stripper that leaves navigation/footer text mixed into the summary.
beautifulsoup4
```

**Verify**: `grep -n "beautifulsoup4" requirements.txt` → shows the new
line.

### Step 2: Install it and confirm the import now succeeds

In the project's existing virtual environment (or the one
`start_alyssa.bat` would create — see `README.md` step 5), run:

```
pip install -r requirements.txt
```

**Verify**: `python -c "from bs4 import BeautifulSoup; print('ok')"` (run
with the same Python/venv that has `requirements.txt` installed) → prints
`ok`, no traceback.

### Step 3: Write the regression test

Create `tests/__init__.py` (empty file) if it doesn't already exist (check
first — Plan 001 may have created it).

Create `tests/test_web_summarizer_bs4_available.py`:

```python
"""Regression test ensuring beautifulsoup4 is an installed, importable
dependency, so plugins/web_summarizer.py takes its intended parsing path
instead of silently degrading to the regex fallback. See
plans/003-add-missing-beautifulsoup4-dependency.md.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


class TestBeautifulSoupAvailable(unittest.TestCase):
    def test_bs4_importable(self):
        try:
            import bs4  # noqa: F401
        except ImportError as e:
            self.fail(
                "beautifulsoup4 is not installed - check it's listed in "
                f"requirements.txt and `pip install -r requirements.txt` "
                f"was run. Original error: {e}"
            )

    def test_web_summarizer_reports_bs4_available(self):
        import importlib
        import plugins.web_summarizer as web_summarizer
        importlib.reload(web_summarizer)  # pick up bs4 if it was just installed mid-session
        self.assertTrue(
            web_summarizer._BS4_AVAILABLE,
            "plugins/web_summarizer.py fell back to the regex HTML "
            "stripper - beautifulsoup4 is installed but the plugin's "
            "own import still failed, or the plugin's fallback logic "
            "changed. Check plugins/web_summarizer.py:9-14.",
        )


if __name__ == "__main__":
    unittest.main()
```

Note: `plugins/` has no `__init__.py` in this repo (each plugin is loaded
dynamically via `plugin_loader.py`'s `importlib.util.spec_from_file_location`,
not normal package imports — confirmed by reading `plugin_loader.py`'s
`load_plugins()`). `import plugins.web_summarizer` in the test above
requires `plugins/` to be treated as a regular importable package for test
purposes only. If this import fails with `ModuleNotFoundError: No module
named 'plugins'`, add an empty `plugins/__init__.py` — check first whether
one already exists (it does not, as of this audit) — and re-run. If you
add it, add that file to this plan's Scope note when done, and confirm it
doesn't change `plugin_loader.py`'s own dynamic-loading behavior (it
shouldn't: `plugin_loader.py` never imports `plugins` as a package itself,
so this is purely additive for tests).

**Verify**: `python -m unittest tests.test_web_summarizer_bs4_available -v`
(from inside `alyssa_assistant/`) → both tests pass, output ends with `OK`.

## Test plan

- New tests, in `tests/test_web_summarizer_bs4_available.py`:
  - `test_bs4_importable` — `bs4` installs and imports cleanly from
    `requirements.txt` alone.
  - `test_web_summarizer_reports_bs4_available` — the plugin's own
    `_BS4_AVAILABLE` flag is `True` after installing, not just that `bs4`
    exists somewhere on the system (guards against the plugin's try/except
    catching something other than a plain missing-package error in the
    future).
- No existing test file to model this after (see Plan 001 for the same
  note — this repo has no test suite before this batch of plans).
- Verification: `python -m unittest tests.test_web_summarizer_bs4_available -v` → `OK`.

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `grep -c "beautifulsoup4" requirements.txt` → `1`
- [ ] `python -c "from bs4 import BeautifulSoup"` exits 0 in the project's
      venv after `pip install -r requirements.txt`
- [ ] `python -m unittest tests.test_web_summarizer_bs4_available -v`
      exits 0, both tests `OK`
- [ ] No files outside the in-scope list are modified (plus the optional
      `plugins/__init__.py`, only if it was required per the note in Step 3)
- [ ] `plans/README.md` status row for 003 updated to `DONE`

## STOP conditions

Stop and report back (do not improvise) if:

- `requirements.txt` already lists `beautifulsoup4` or `bs4` (contradicts
  the audit's finding — someone may have already fixed this).
- Installing `beautifulsoup4` produces a real error (e.g. no matching
  wheel for the target Python version) rather than just succeeding — don't
  pin to a workaround version without understanding why; report the exact
  error instead.
- Adding `plugins/__init__.py` (if needed for the test import, per the
  Step 3 note) causes `plugin_loader.py`'s dynamic loading to behave
  differently — e.g. plugins load twice, or `plugin_loader.load_plugins()`
  starts raising. If you see that, remove the `__init__.py` and instead
  have the test import `web_summarizer` directly via
  `importlib.util.spec_from_file_location`, matching the pattern in
  `plugin_loader.py` itself, rather than pushing forward with a change
  that alters plugin-loading behavior.

## Maintenance notes

- If `plugins/__init__.py` was added to make the test importable, a future
  reviewer should know it exists solely for test convenience and is not
  used by the app's own runtime plugin loading (`plugin_loader.py` loads
  every plugin file directly by path, never via `import plugins.x`).
- Any future plugin that does an optional `try: import X / except
  ImportError` fallback (search for the pattern with
  `grep -rn "except ImportError" plugins/` to find siblings) should be
  checked against `requirements.txt` the same way this plan checked
  `web_summarizer.py` — this exact mistake (a "nice to have" import that's
  silently missing from the manifest) is easy to repeat.
