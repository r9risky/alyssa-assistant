# Plan 013: Add mocked integration coverage for the runtime state machines

> **Executor instructions**: This is characterization and infrastructure only.
> Do not fix production bugs while writing tests; record unexpected behavior and
> follow the dependent plans. Update the index when done.
>
> **Drift check (run first)**: no git metadata is present. Confirm the current
> suite still contains the five existing test modules and 44 discovered tests.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: LOW
- **Depends on**: plans 007, 008, 009, 010, 011, 012
- **Category**: tests
- **Planned at**: no git metadata available, 2026-08-13

## Why this matters

The current 44 passing tests cover message conversion, memory/name helpers,
argument sanitization, and Beautiful Soup availability. They do not exercise the
audio, interruption, watcher, live-plugin, Windows-formatting, or confirmation
state machines where deterministic failures were found. This plan creates a
hardware-free mocked integration layer before concurrency-heavy fixes proceed.

## Current state

- `pytest.ini` exists, but pytest is not declared or installed by runtime setup.
- `.\.venv\Scripts\python.exe -m unittest discover -v` passes 44 tests and is
  therefore the verified zero-extra-dependency command.
- `plans/004-establish-test-baseline-pure-logic-modules.md:148-159` deliberately
  excluded the audio modules.
- `tests/test_brain_message_conversion.py` shows how imports and `unittest`
  assertions are structured.
- Hardware and network modules perform work through replaceable module globals
  (`sd`, `pygame`, `requests`, clocks, plugin directories), suitable for mocks.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Verified suite | `.\.venv\Scripts\python.exe -m unittest discover -v` | all tests pass |
| Dependency consistency | `.\.venv\Scripts\python.exe -m pip check` | no broken requirements |
| Collection count | `.\.venv\Scripts\python.exe -m unittest discover -v 2>&1` | includes all new modules |

## Scope

**In scope**: new `tests/test_runtime_*.py` modules, shared test-only fakes under
`tests/`, `README.md` contributor verification instructions, and optionally a
`requirements-dev.txt` only if pytest-specific features are genuinely required.

**Out of scope**: modifying runtime source to make failing characterization
tests pass, real mic/camera/network calls, GUI pixel tests, or coverage targets.

## Git workflow

If git exists, branch `advisor/013-runtime-tests`. Commit test infrastructure as
one logical unit; do not push.

## Steps

### Step 1: Document one clean verification command

Adopt `python -m unittest discover -v` as the mandatory baseline because it is
verified and dependency-free. If maintainers require pytest, add a dev-only
requirements file and CI install step; never put pytest in core runtime deps just
to satisfy contributor tooling.

**Verify**: a clean runtime venv runs the documented command successfully.

### Step 2: Build reusable deterministic fakes

Add test fakes for blocking/releasing input streams, NumPy audio, playback,
clocks, model transcription, HTTP responses, plugin directories/modules, and
request-bound confirmations. Fakes must expose events so tests control thread
interleavings without arbitrary sleeps.

**Verify**: a small self-test proves fake stream open/read/close and event
coordination terminate within a bounded timeout.

### Step 3: Add behavior coverage by subsystem

Create separate modules for confirmation dispatch, interruption handoff, watcher
scheduling/speech, PortAudio recovery, TTS cancellation/cleanup, plugin load and
reload, timer/reminder time behavior, and camera failure state. Characterize
current safe behavior and encode desired safety invariants from completed plans
007–012. For bugs assigned to later plans, mark tests with clear expected-failure
mechanics only if the standard library runner can report them without hiding
unexpected passes; otherwise defer those exact assertions to the dependent plan.

**Verify**: each module runs alone; full discovery is deterministic across three
consecutive runs.

### Step 4: Add Windows CI or a reproducible local CI script

Create a minimal Windows job/workflow if this is a git-hosted project; otherwise
add a read-only `verify.bat` that runs unittest discovery, syntax parsing, and
`pip check`. It must not install packages or launch hardware.

**Verify**: the job/script exits 0 on the current tree and nonzero after a
deliberate temporary failing assertion (revert that assertion afterward).

## Test plan

This plan is the test plan. Target at least one success, failure, timeout, and
cleanup assertion per state machine. Avoid test-order dependence and restore all
patched globals in cleanup.

## Done criteria

- [ ] One documented, clean-environment verification command works.
- [ ] Tests use no real hardware, network, destructive OS API, or credentials.
- [ ] Runtime state-machine modules are independently runnable.
- [ ] Three consecutive full runs pass and terminate cleanly.
- [ ] `pip check` passes and only in-scope test/docs/tooling files changed.

## STOP conditions

- A test can only be written by changing production behavior first.
- A mock leaks threads, files, module state, or environment variables between tests.
- Clean execution requires administrator access or external credentials.

## Maintenance notes

Dependent plans 014–018 should extend these fakes with regression assertions.
Keep live hardware smoke tests separate and opt-in.

