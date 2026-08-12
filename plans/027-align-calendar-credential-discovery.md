# Plan 027: Use one credential-discovery policy for Calendar and Gmail

> **Executor instructions**: Do not read, print, or copy credential contents.
> Test paths with empty placeholder files and mocked Google clients only.
>
> **Drift check (run first)**: no git metadata is present. Confirm
> `plugins/calendar_gmail.py:75-120,153-158` still matches.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: plan 005
- **Category**: bug
- **Planned at**: no git metadata available, 2026-08-13

## Why this matters

Authentication supports four credential-file locations through
`_find_credentials_path()`, but `_availability_check()` tests only the root
`_CREDENTIALS_PATH`. A credential placed in another supported location is
reported missing and every user-facing Calendar/Gmail tool opens setup guidance
instead of authenticating. Discovery and availability must share one source of truth.

## Current state

- `plugins/calendar_gmail.py:75-90` searches root, plugin, nested plugin, and
  config locations and returns the first existing path.
- `_get_credentials()` uses that helper at line 97.
- `_availability_check()` at lines 153-158 checks only
  `os.path.exists(_CREDENTIALS_PATH)`.
- OAuth token storage/hygiene is already covered by completed plan 002 and secret
  storage changes in plan 005; do not duplicate or expose token contents.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Focused tests | `.\.venv\Scripts\python.exe -m unittest tests.test_calendar_credentials -v` | all pass |
| Full tests | `.\.venv\Scripts\python.exe -m unittest discover -v` | all pass |

## Scope

**In scope**: `plugins/calendar_gmail.py` and
`tests/test_calendar_credentials.py` (new or plan-013 extension).

**Out of scope**: changing OAuth scopes, moving/printing real credentials,
redesigning protected secret storage, or making live Google calls.

## Git workflow

If git exists, branch `advisor/027-calendar-credential-discovery`. Do not push.

## Steps

### Step 1: Centralize availability on discovery

Make `_availability_check()` call `_find_credentials_path()` and distinguish
missing libraries from no discovered client file. Avoid a time-of-check/time-of-
use inconsistency by passing the discovered path into authentication where
practical, or document why re-discovery is safe.

**Verify**: temporary-path tests report available for each supported location and
missing only when none exists.

### Step 2: Clarify precedence and diagnostics

Document which location wins when multiple files exist. Diagnostics may identify
the selected path category but must not read or log contents. Ensure setup text
names every supported location or simplify the policy to one documented location.

**Verify**: precedence tests are deterministic; captured output contains no file
contents or credential fields.

### Step 3: Test user-facing tool entry points

Mock `_get_service()`/Google clients and prove `get_next_meeting()`,
`get_todays_schedule()`, `check_important_emails()`, and `check_watch()` no longer
reject a credential solely because it is in a supported alternate location.

**Verify**: focused and full suites pass with no network or browser launch.

## Test plan

- Use temporary directories and placeholder files; patch all path globals.
- Cover each location, none, multiple/precedence, missing Google libraries, and
  disappearance between discovery/authentication.

## Done criteria

- [ ] Availability and authentication use the same discovery policy.
- [ ] Every documented location works or unsupported locations are removed from both.
- [ ] Precedence is deterministic and documented.
- [ ] No credential contents enter logs/tests.
- [ ] Focused/full tests pass.

## STOP conditions

- Plan 005 changes Calendar client-secret storage to a non-file backend.
- Multiple-location discovery is intentionally being removed by product decision.
- Tests would require a real OAuth client file.

## Maintenance notes

Prefer one canonical discovery function returning typed status. Future settings
UI should call the same API instead of reproducing path checks.

