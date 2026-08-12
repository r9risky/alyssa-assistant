# Plan 007: Require trusted local approval for protected actions

> **Executor instructions**: Implement the fail-closed boundary described here.
> Do not restore an ad-hoc biometric algorithm. Update the index when done.
>
> **Drift check (run first)**: no git metadata is present. Confirm
> `voice_id.py:1-22`, `brain.py:59-103`, and `README.md:442-463` still match.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: plan 006
- **Category**: security
- **Planned at**: no git metadata available, 2026-08-13

## Why this matters

Protected actions currently resume after any nearby voice says an affirmative
phrase because the removed voice-ID stub returns success unconditionally. The
README still advertises speaker verification. Until a vetted authenticator
exists, shell commands, deletion, power changes, screen clicks, and other
protected plugin actions must require deliberate local UI/terminal approval.

## Current state

- `voice_id.py:21-22`: `verify(audio)` returns `(True, "")` for every input.
- `brain.py:74-92` treats that result as authorization and calls the pending
  function with `confirmed=True`.
- `main.py:381-384` routes any utterance to the pending-confirmation parser.
- `actions.py:1254-1282` still exposes a voice-enrollment tool.
- `README.md:442-463` describes a voiceprint implementation that does not exist.
- The GUI already has a typed chat path; terminal mode has standard input.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Focused tests | `.\.venv\Scripts\python.exe -m unittest tests.test_protected_confirmation -v` | all pass |
| Full tests | `.\.venv\Scripts\python.exe -m unittest discover -v` | all pass |
| Syntax | `.\.venv\Scripts\python.exe -c "import ast,pathlib; [ast.parse(p.read_text(encoding='utf-8')) for p in pathlib.Path('.').rglob('*.py') if '.venv' not in p.parts and 'plans' not in p.parts]"` | exit 0 |

## Scope

**In scope**: `brain.py`, `main.py`, `actions.py`, `overlay.py`, `voice_id.py`,
`config.py` only to remove obsolete voice-ID settings if present,
`tests/test_protected_confirmation.py` (new), and relevant README text.

**Out of scope**: implementing speaker biometrics, Windows Hello integration,
changing which actions are protected (plan 008), or weakening confirmation.

## Git workflow

If git is available, branch `advisor/007-trusted-confirmation`. Do not push.

## Steps

### Step 1: Define one trusted confirmation interface

Represent a pending request with an opaque ID, action name, exact description,
and immutable arguments. Provide approve/deny entry points that require the
matching request ID. Spoken audio may cancel or ask for the prompt again, but
must not authorize. GUI mode should show explicit Approve and Cancel controls;
console mode should use blocking local `y/N` input without echoing secret
arguments unnecessarily.

**Verify**: focused tests prove spoken “yes” cannot approve, the matching local
approval can approve once, wrong/stale IDs fail, denial clears state, and timeout
fails closed.

### Step 2: Remove the false biometric surface

Remove `enroll_voice` from built-in functions/tool schemas and diagnostics.
Delete the misleading `VOICE_ID_ENABLED` branches; retain `voice_id.py` only if
another live caller still needs it, otherwise remove it. Do not leave an
always-true compatibility verifier on an authorization path.

**Verify**: `rg -n "VOICE_ID_ENABLED|enroll_voice|voiceprint" -g "*.py" .`
finds no active authorization or advertised enrollment path.

### Step 3: Update user-facing behavior

Replace the README voice-ID section with the actual trusted approval behavior.
The confirmation prompt must name the exact action and target. A timeout or UI
closure must produce a clear cancellation response.

**Verify**: GUI and console smoke tests show approve, deny, timeout, and close;
the action executes exactly once only after explicit approval.

## Test plan

- Use fake action functions; never run shell commands, delete files, power off,
  or click the real desktop.
- Cover request replacement, replay, timeout, denial, typed/UI approval, spoken
  affirmative, callback absence, action exception, and single execution.
- Follow `tests/test_tool_argument_sanitization.py` for isolated `unittest` style.

## Done criteria

- [ ] No spoken phrase alone authorizes a protected action.
- [ ] Approval is bound to one exact pending request and cannot be replayed.
- [ ] Missing UI/callback/auth state fails closed.
- [ ] Obsolete voice-ID UI, tool schema, diagnostics, and docs are removed.
- [ ] Focused and full tests pass; syntax check passes.

## STOP conditions

- Product requirements mandate hands-free spoken authorization without an
  available, vetted authenticator.
- The GUI cannot expose a request-bound approval signal without a broad redesign.
- Another live module depends on the voice-ID stub for non-authorization behavior.

## Maintenance notes

Any future biometric or Windows Hello implementation should satisfy this same
request-bound interface and fail closed. Do not let model-supplied arguments
contain approval state.

