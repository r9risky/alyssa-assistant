# Plan 011: Redact sensitive data from default diagnostic logs

> **Executor instructions**: Preserve useful status/timing diagnostics without
> retaining user content. Never add real secrets to fixtures or output.
>
> **Drift check (run first)**: no git metadata is present. Confirm
> `config.py:139`, `main.py:366-374,411`, and `brain.py:2394-2401,2586-2591`.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: plan 005
- **Category**: security
- **Planned at**: no git metadata available, 2026-08-13

## Why this matters

Transcript logging is enabled by default, and tool arguments, tool output, and
cloud error bodies are printed verbatim. These may include dictated private
text, clipboard contents, commands, paths, email/calendar metadata, and provider
details. The GUI hides the console but does not erase its buffer. Default logs
should contain operational metadata only; sensitive payload logging must be an
explicit temporary diagnostic mode.

## Current state

- `config.py:139`: `DEBUG_PRINT_TRANSCRIPTS = True`.
- `main.py:366-374,411` prints typed and transcribed input.
- `brain.py:2589` prints `name(arguments) -> output` in full.
- `brain.py:2401` prints a provider HTTP response body.
- Timing and plugin-status logs are useful and can remain.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Focused tests | `.\.venv\Scripts\python.exe -m unittest tests.test_safe_logging -v` | all pass |
| Full tests | `.\.venv\Scripts\python.exe -m unittest discover -v` | all pass |
| Secret-pattern search | `rg -n "print\(.*arguments|response\.text|Whisper heard|Typed:" brain.py main.py` | only guarded/redacted sites remain |

## Scope

**In scope**: `config.py`, `main.py`, `brain.py`, a small logging/redaction helper
if warranted, `tests/test_safe_logging.py` (new), and README diagnostics text.

**Out of scope**: adding a persistent log service, telemetry, uploading logs,
or changing user-facing spoken error messages except to remove leaked details.

## Git workflow

If git exists, branch `advisor/011-safe-logging`. Do not push.

## Steps

### Step 1: Define safe default events

Log tool name, success/failure category, duration, and bounded structural facts
such as result length—not arguments or output content. Provider HTTP errors may
log status and request ID headers from a strict allowlist, never full bodies or
authorization headers. Set transcript debugging off by default.

**Verify**: focused tests capture stdout and assert sentinel secrets, clipboard
text, file paths, commands, emails, and response bodies never appear.

### Step 2: Add explicit temporary verbose diagnostics

If full payload visibility is retained, gate it behind a clearly named opt-in
setting that defaults false and prints a startup warning. Apply robust redaction
for credential-shaped fields even in verbose mode. Do not persist a rolling log
unless separately requested.

**Verify**: default-mode tests show metadata only; verbose-mode tests show safe
payload detail but redact keys/tokens/passwords.

### Step 3: Document privacy behavior

Document which diagnostics remain, how to enable temporary verbose debugging,
and why users should disable it after troubleshooting.

**Verify**: full suite passes and search shows no unconditional payload print.

## Test plan

- Mock tool results and `requests.HTTPError` responses.
- Test default and verbose modes with representative sensitive strings.
- Assert timing/status lines remain so diagnostics are still actionable.

## Done criteria

- [ ] Transcript and payload logging defaults off/redacted.
- [ ] Provider bodies are not printed wholesale.
- [ ] Tool status and timing remain available.
- [ ] Verbose mode, if retained, is explicit and redacts credentials.
- [ ] Focused and full tests pass.

## STOP conditions

- A support contract requires persistent raw logs.
- Redaction would require storing or comparing against real credentials.
- An in-scope print is consumed as a machine-readable API by another module.

## Maintenance notes

Review new logging sites for data classification. Prefer structured event names
and counts over values; hidden UI is not a privacy boundary.

