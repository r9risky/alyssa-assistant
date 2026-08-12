# Plan 010: Format calendar and reminder times portably on Windows

> **Executor instructions**: Follow each gate and update `plans/README.md`.
>
> **Drift check (run first)**: no git metadata is present. Confirm every `%-I`
> occurrence still appears at the cited lines before editing.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: bug
- **Planned at**: no git metadata available, 2026-08-13

## Why this matters

Python's Windows runtime rejects the Unix-only `%-I` `strftime` directive.
Calendar answers therefore fail while formatting valid events. A reminder can
be written successfully and then throw while composing its response, inviting
the user to retry and create duplicates. A tiny portable formatting helper and
regression tests eliminate this Windows-specific failure.

## Current state

- `plugins/calendar_gmail.py:207` uses `strftime("%A at %-I:%M %p")`.
- `plugins/calendar_gmail.py:232` uses `strftime('%-I:%M %p')`.
- `plugins/reminders.py:146,181` use the same unsupported directive.
- The verified Windows runtime raises `ValueError: Invalid format string` for it.
- Tests use `unittest`; no existing plugin behavior tests cover date formatting.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Search | `rg -n "%[-#]I" -g "*.py" .` | no unsupported directives after fix |
| Focused tests | `.\.venv\Scripts\python.exe -m unittest tests.test_time_formatting -v` | all pass |
| Full tests | `.\.venv\Scripts\python.exe -m unittest discover -v` | all pass |

## Scope

**In scope**: `plugins/calendar_gmail.py`, `plugins/reminders.py`, and
`tests/test_time_formatting.py` (new). A tiny shared helper file is allowed only
if it reduces duplication without importing plugin or GUI state.

**Out of scope**: changing parsing semantics, time zones, reminder persistence,
calendar API queries, or adding a date/time dependency.

## Git workflow

If git exists, branch `advisor/010-portable-time-format`. Do not push.

## Steps

### Step 1: Add portable 12-hour formatting

Use `%I:%M %p`, then remove only the leading zero from the rendered hour. Keep
weekday and `today at` wording exactly as today. Do not use Windows-only `%#I`,
because the project claims Python-level portability even though it runs on Windows.

**Verify**: focused tests assert midnight, 1:05 AM, noon, 3:00 PM, and 11:59 PM.

### Step 2: Replace all four call sites

Apply the helper consistently to next meeting, today's schedule, reminder-add
confirmation, and reminder listing. Preserve aware/local datetime conversion in
the Calendar plugin.

**Verify**: the search command returns no matches; focused tests cover all four
paths using mocked services/files and deterministic datetimes.

## Test plan

- Use temporary reminder storage and mocked Calendar responses.
- Prove a timed reminder is saved once and returns a successful confirmation.
- Prove list output and calendar output contain no leading zero and no exception.
- Avoid live Google authentication or network calls.

## Done criteria

- [ ] No `%-I` or `%#I` remains in Python source.
- [ ] Timed reminder creation/listing succeeds under Windows semantics.
- [ ] Both Calendar display functions format valid events successfully.
- [ ] Focused and full tests pass.

## STOP conditions

- Live source has moved formatting into a different shared utility.
- Fixing display reveals a separate timezone/data bug; report it separately.
- Tests require live Google credentials.

## Maintenance notes

Future human-readable time output should reuse the same helper. Keep persistence
in ISO 8601; only presentation belongs in this formatter.

