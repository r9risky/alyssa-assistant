# Plan 001: Prevent the LLM from self-approving confirmation-gated actions

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
> `brain.py` at the line numbers cited under "Current state" below and
> confirm the code there matches the quoted excerpts *before* editing
> anything. If it doesn't match, treat that as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: security
- **Planned at**: no git repository (repo has no `.git` directory) — N/A
- **Issue**: (none — not published)

## Why this matters

`actions.py` defines four "confirmation-gated" tools — `delete_file`,
`run_command`, `system_power_action`, and `click_screen_element` — that are
supposed to *always* ask the user for a spoken y/n before running,
regardless of the `CONFIRM_BEFORE_ACTIONS` setting in `config.py`. This is
the safety guarantee the project's `README.md` advertises explicitly:
"`run_command` ... and `delete_file` always ask you to confirm ... no matter
what `CONFIRM_BEFORE_ACTIONS` is set to."

That guarantee is implemented by each function checking a `confirmed: bool
= False` keyword argument and only proceeding (or asking for approval) based
on it — see `actions.py:668` (`delete_file`) and `actions.py:688`
(`run_command`). The problem: the *first* time any tool is called, in
`brain.py`'s `handle_command()`, the arguments dict passed to `func(**arguments)`
is built directly from whatever JSON the LLM's tool call contained (see
"Current state" below), with no filtering. `confirmed` is never declared in
any tool's schema shown to the model (checked all `_BASE_TOOLS` entries in
`brain.py`), but nothing stops a model from emitting
`{"path": "C:\\...", "confirmed": true}` anyway — at which point
`delete_file(path=..., confirmed=True)` skips its own confirmation check
entirely, because from its point of view the action was already approved.

This turns a hard safety promise into something a sufficiently unusual model
output (or content designed to provoke one) can bypass silently, for the two
actions the README calls out as "genuinely hard to undo" (arbitrary shell
commands and file deletion). It's a realistic path, not just a theoretical
one: `plugins/web_summarizer.py`'s `summarize_webpage` fetches arbitrary
third-party web pages and feeds their extracted text back into the model's
context as a tool result, and `install_startup.bat` is designed to register
Alyssa as a scheduled task that runs with **elevated (admin) privileges** at
every login — so an over-eager or manipulated tool call reaching `run_command`
unconfirmed could execute with admin rights.

The fix is narrow: strip the `confirmed` key out of any arguments dict that
came from a *first-round* LLM tool call, before it's ever passed to a tool
function. The only place `confirmed=True` should legitimately reach a tool
function is the internal pending-confirmation resume path
(`_handle_pending_power_confirmation` in `brain.py`, which builds its own
trusted arguments dict — see "Current state" — not one that passed through
this sanitization).

## Current state

- `brain.py` — all LLM integration and tool dispatch logic. The relevant
  pieces:

  1. The tool schemas shown to the model (`_BASE_TOOLS`, starting at
     `brain.py:119`) never include a `confirmed` property. For example, the
     `run_command` schema (`brain.py:236-247`):

     ```python
     {
         "type": "function",
         "function": {
             "name": "run_command",
             "description": "Run a Windows command-line command and return its output. Use for anything not covered by the other tools.",
             "parameters": {
                 "type": "object",
                 "properties": {"command": {"type": "string"}},
                 "required": ["command"],
             },
         },
     },
     ```

  2. The vulnerable dispatch path, inside `handle_command()`, at
     `brain.py:2499-2530`:

     ```python
             raw_arguments = fn.get("arguments", {})
             if isinstance(raw_arguments, str):
                 try:
                     arguments = json.loads(raw_arguments)
                 except (TypeError, ValueError):
                     arguments = {}
             elif isinstance(raw_arguments, dict):
                 arguments = raw_arguments
             else:
                 arguments = {}

             if name == "open_app":
                 opened_app_this_round = True
             if name == "search_web":
                 searched_web_this_round = True

             func = actions.FUNCTIONS.get(name)
             if func is None:
                 tool_output = f"Unknown tool: {name}"
             else:
                 # Speak an anticipatory "Opening Chrome..." before running
                 # the action, so she talks first, action second. Skipped
                 # when CONFIRM_BEFORE_ACTIONS is on, since then every
                 # action waits on a y/n first.
                 if on_partial_reply is not None and not getattr(config, "CONFIRM_BEFORE_ACTIONS", False):
                     announce = _natural_announce_reply(name, arguments)
                     if announce:
                         on_partial_reply(announce)
                 try:
                     tool_output = func(**arguments)
                 except Exception as e:
                     tool_output = f"Error running {name}: {e}"
     ```

     `arguments` here is untouched, attacker/model-controlled JSON, and it
     flows straight into `func(**arguments)`.

  3. The trusted, *legitimate* path that is allowed to pass
     `confirmed=True`, in `_handle_pending_power_confirmation()` at
     `brain.py:88-94` — note it calls `pending["arguments"]`, a dict that was
     captured by `_request_voice_confirmation()` (`brain.py:34-43`) from a
     literal dict built inside `actions.py` itself (e.g.
     `actions.py:674-676`: `{"path": path}`), not from raw LLM JSON. This
     path is correct as-is and must not be changed:

     ```python
             func = actions.FUNCTIONS.get(pending["name"])
             if func is None:
                 return "I couldn't complete that approved action."
             try:
                 raw_output = func(**pending["arguments"], confirmed=True)
     ```

  4. The gated-tools list, at `brain.py:1052`:

     ```python
     _CONFIRMATION_GATED_TOOLS = {"delete_file", "run_command", "system_power_action", "click_screen_element"}
     ```

- Repo conventions: `brain.py` groups small private helpers near the data
  they operate on and documents *why*, not just *what*, in the docstring —
  see `_natural_announce_reply` at `brain.py:1060-1068` for the style to
  match (a one-paragraph docstring explaining the reasoning, then the
  logic). New helpers are prefixed with `_` and placed near the constants
  they use.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Install deps (one-time, if not already done) | `pip install -r requirements.txt` (run from inside `alyssa_assistant/`, ideally in a venv per `README.md` step 4) | exit 0 |
| Run the new unit test | `python -m unittest tests.test_tool_argument_sanitization -v` (run from inside `alyssa_assistant/`) | `OK` — all tests pass |
| Manual import sanity check | `python -c "import brain"` (run from inside `alyssa_assistant/`) | exits 0, no traceback |

(There is no existing test runner or CI config in this repo — these are the
first automated tests being added. `unittest` is used, not `pytest`,
because it needs zero new dependencies; Plan 004 introduces `pytest` and a
`tests/` convention more broadly. This plan creates `tests/` and
`tests/__init__.py` if they don't already exist yet — check first, since
Plan 004 may have run before this one.)

## Scope

**In scope** (the only files you should modify or create):
- `brain.py` — add the sanitization helper and call it at the dispatch site.
- `tests/__init__.py` — create if it doesn't already exist.
- `tests/test_tool_argument_sanitization.py` — new test file.

**Out of scope** (do NOT touch, even though they look related):
- `actions.py` — the `confirmed` kwarg on `delete_file`/`run_command`/etc.
  stays exactly as-is; this plan fixes the caller, not the callees.
- `_handle_pending_power_confirmation()` (`brain.py:59-104`) — this is the
  trusted resume path described above and must not be modified.
- Any other tool or plugin — this plan only touches the generic dispatch
  path, which already applies to every tool uniformly.

## Git workflow

This repository has no `.git` directory (confirmed during recon). Do not
attempt to create one, commit, or branch — just make the file changes
directly. If the operator has since initialized git themselves, ask before
assuming any particular branch/commit convention rather than guessing one.

## Steps

### Step 1: Add the sanitization helper

In `brain.py`, immediately after the `_CONFIRMATION_GATED_TOOLS` /
`_SKIP_ANNOUNCE_TOOLS` block (i.e., right after the line
`_SKIP_ANNOUNCE_TOOLS = {"get_datetime", "read_clipboard", "run_diagnostics", "reset_conversation", "enroll_voice"}`
at `brain.py:1057`, and before `def _natural_announce_reply(...)`), insert:

```python


def _sanitize_tool_arguments(arguments: dict) -> dict:
    """Strips the internal-only "confirmed" flag from a *first-round* tool
    call's arguments before they reach a tool function.

    "confirmed" is not declared in any tool's schema in _BASE_TOOLS above -
    it's purely an internal signal that a human already approved the action,
    set by _handle_pending_power_confirmation() when resuming an approved
    call (see that function's own arguments dict, which it builds itself
    from a trusted literal, never from raw model JSON). Without this
    stripping step, a model's tool call could include "confirmed": true in
    its own arguments and skip delete_file()/run_command()'s confirmation
    check entirely - including when that tool call was prompted by
    untrusted content the model was asked to read, e.g. a page fetched by
    summarize_webpage(). Applied unconditionally to every tool call, not
    just the four in _CONFIRMATION_GATED_TOOLS, since no tool's schema ever
    legitimately includes "confirmed"."""
    if "confirmed" in arguments:
        arguments = dict(arguments)
        del arguments["confirmed"]
    return arguments
```

**Verify**: `python -c "import brain"` (from inside `alyssa_assistant/`) →
exits 0, no traceback.

### Step 2: Call the helper at the dispatch site

In `brain.py`, inside `handle_command()`, locate the block shown in
"Current state" item 2 above. Immediately after the `arguments` dict is
resolved (after the `else: arguments = {}` line, i.e. right before the
`if name == "open_app":` line), insert one line:

```python
            arguments = _sanitize_tool_arguments(arguments)
```

So the surrounding code reads:

```python
            elif isinstance(raw_arguments, dict):
                arguments = raw_arguments
            else:
                arguments = {}

            arguments = _sanitize_tool_arguments(arguments)

            if name == "open_app":
```

Do not change anything else in this block — `_natural_announce_reply(name,
arguments)` and `func(**arguments)` both need to see the *sanitized*
`arguments`, which happens automatically since they run after this line.

**Verify**: `python -c "import brain"` → exits 0, no traceback.

### Step 3: Write the regression test

Create `tests/__init__.py` (empty file) if `tests/` doesn't already exist.

Create `tests/test_tool_argument_sanitization.py`:

```python
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
```

**Verify**: `python -m unittest tests.test_tool_argument_sanitization -v`
(from inside `alyssa_assistant/`) → all 4 tests pass, output ends with `OK`.

## Test plan

- New tests, all in `tests/test_tool_argument_sanitization.py`:
  - `test_strips_confirmed_key` — the core regression case: a tool call
    with `confirmed: true` in its arguments must have that key removed.
  - `test_strips_confirmed_key_when_false` — the key is stripped
    regardless of its value, since its mere presence is the untrusted
    signal.
  - `test_leaves_arguments_without_confirmed_untouched` — no false
    positives; normal tool calls are unaffected.
  - `test_does_not_mutate_the_original_dict` — the helper returns a new
    dict rather than mutating the caller's dict in place, since
    `handle_command()`'s later logging (`print(f"[tool] {name}({arguments}) -> ...")`,
    `brain.py:2535`) and `_record_recent_action(name, arguments, tool_output)`
    (`brain.py:2537`) both still run against `arguments` after this point —
    check whether the plan's Step 2 placement means those see the
    sanitized or original dict, and note it in a code comment either way
    (sanitized is fine and arguably preferable, since it avoids echoing an
    attempted bypass into the recent-actions log — no behavior change is
    required here, just be aware of which one happens).
- No existing test file to model this after — this is the first test in
  the repo. Keep the style plain (stdlib `unittest`, no fixtures needed)
  since `tests/` has no established convention yet.
- Verification: `python -m unittest tests.test_tool_argument_sanitization -v` → `OK`.

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `python -c "import brain"` exits 0
- [ ] `python -m unittest tests.test_tool_argument_sanitization -v` exits 0, all tests `OK`
- [ ] `grep -n "_sanitize_tool_arguments" brain.py` shows both the
      definition and exactly one call site inside `handle_command()`
- [ ] `grep -n "func(\*\*arguments)" brain.py` — confirm this line still
      exists unchanged (only the value of `arguments` upstream changed, not
      this call itself)
- [ ] No files outside the in-scope list are modified
- [ ] `plans/README.md` status row for 001 updated to `DONE`

## STOP conditions

Stop and report back (do not improvise) if:

- The code at `brain.py:2499-2530` doesn't match the "Current state"
  excerpt above (the dispatch loop has been restructured since this plan
  was written) — re-locate the equivalent point (where `arguments` is
  finalized but before `func(**arguments)` runs) and confirm with the
  operator before proceeding, rather than guessing a new insertion point.
- `_CONFIRMATION_GATED_TOOLS` or the schemas in `_BASE_TOOLS` have gained a
  legitimate, intentional `confirmed`-like property in the meantime — if a
  tool schema now genuinely exposes something the model should set, this
  plan's blanket stripping approach needs to be scoped down to exclude it,
  and that's a design decision for the operator, not something to improvise.
- `python -m unittest` fails for a reason unrelated to this change (e.g.
  missing dependencies like `pyautogui` not installed) — that means
  `pip install -r requirements.txt` wasn't run first; don't work around it
  by mocking imports, just report that the precondition wasn't met.

## Maintenance notes

- Any new confirmation-gated tool added in the future automatically gets
  this protection for free, since the stripping is unconditional and
  happens before the `func is None` branch — no need to add new tool names
  to a list.
- If a future tool legitimately needs to accept a boolean flag from the
  model that happens to be named `confirmed`, that's a naming collision to
  avoid, not a reason to weaken this check — pick a different parameter
  name for that tool instead.
- A reviewer should double check that `_sanitize_tool_arguments` runs
  *before* both `_natural_announce_reply(name, arguments)` and
  `func(**arguments)` — if a future edit reorders `handle_command()`, it
  would be easy to accidentally sanitize after one of those two uses.
- This plan does not address `CONFIRM_BEFORE_ACTIONS = False` (the
  default) allowing all *non*-gated tools to run without any confirmation
  at all — that's the existing, documented, by-design behavior for the
  non-destructive tools, not a bug.
