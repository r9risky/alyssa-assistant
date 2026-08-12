# Plan 024: Evaluate an optional low-idle-cost wake-word mode

> **Executor instructions**: This is a measured spike, not authorization to add a
> large dependency to the default install. Record data and render a go/no-go verdict.
>
> **Drift check (run first)**: no git metadata is present. Confirm
> `main.py:336-401`, recorder capture settings, and README rough edge at 650-652.

## Status

- **Priority**: P3
- **Effort**: L
- **Risk**: HIGH
- **Depends on**: plans 013, 015, 016, 020, 021
- **Category**: direction
- **Planned at**: no git metadata available, 2026-08-13

## Why this matters

Alyssa currently records and runs Whisper before checking for its name, which
costs idle CPU and creates false triggers from unrelated conversation. An
optional true wake-word front end could reduce work and improve privacy-like
expectations, but it introduces a model/native dependency and can miss commands.
The project needs measured accuracy, latency, CPU, licensing, and packaging data
before choosing an engine or changing defaults.

## Current state

- `main.py:352-379` records and transcribes each utterance.
- `main.py:381-401` applies pending/grace/name gating only after transcription.
- `README.md:650-652` documents constant transcription CPU/mic usage and false
  name triggers.
- `recorder.py` already owns microphone capture/VAD and plans 015/016 establish
  safe input ownership/recovery.
- Plan 021 separates optional dependency profiles; wake word belongs in one.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Baseline tests | `.\.venv\Scripts\python.exe -m unittest discover -v` | all pass |
| Benchmark harness | `.\.venv\Scripts\python.exe tools\benchmark_wake_word.py --fixture-dir tests\fixtures\wake_word` | produces anonymized metrics JSON, exit 0 |

## Scope

**In scope**: `docs/wake-word-spike.md`, a standalone benchmark harness,
synthetic/consented anonymized fixtures or fixture-generation instructions, an
optional prototype adapter behind a disabled flag/profile, and adapter tests.

**Out of scope**: enabling by default, uploading recordings, bundling unreviewed
models, replacing Whisper transcription, or weakening protected-action approval.

## Git workflow

If git exists, branch `advisor/024-wake-word-spike`. Keep models/audio fixtures
out of git unless small, licensed, consented, and explicitly approved.

## Steps

### Step 1: Define measurable acceptance criteria

Specify target false accepts/hour, false rejects across accent/distance/noise,
idle CPU/RAM, detection latency, package/model size, offline behavior, supported
Python/Windows versions, and license. Define a no-go threshold before evaluating
candidates to avoid choosing by demo quality.

**Verify**: design lists numeric targets and the current Whisper-first baseline.

### Step 2: Compare candidate approaches

Evaluate at least: retain current behavior; lightweight on-device keyword engine;
and a simple acoustic classifier compatible with the dependency policy. Use
primary project docs/licenses and tested wheels. Record maintenance health and
model redistribution terms. Do not select an engine that requires cloud audio.

**Verify**: comparison table includes every acceptance criterion and citations.

### Step 3: Build a standalone benchmark

The harness must operate offline on local fixtures, measure detection/latency/
resource use, and output aggregate metrics without transcript/audio upload.
Include positive aliases, hard negatives, TV/background speech, and multiple
noise levels; document consent/data handling.

**Verify**: repeated runs produce stable aggregate metrics within documented
tolerance and never make network calls.

### Step 4: Prototype the adapter behind a disabled flag

If one candidate meets targets, implement a narrow `WakeWordDetector` interface
that integrates with the plan-015 input coordinator and plan-016 recovery state.
On detection, hand subsequent audio to existing transcription. Provide an
immediate fallback to current behavior if initialization fails.

**Verify**: mocked adapter tests cover detection, miss, engine failure, disable,
barge-in, typed input, device recovery, and fallback.

### Step 5: Render a verdict

Document GO, NO-GO, or REVISIT with measured tradeoffs. GO must include optional
profile/package impact and a separate rollout plan; this spike does not enable it.

**Verify**: verdict references metrics and identifies unresolved risks/owners.

## Test plan

- Keep unit tests hardware-free; benchmark separately on representative hardware.
- Never commit private household recordings or voice biometrics.
- Compare idle and triggered resource use against current mode.

## Done criteria

- [ ] Numeric acceptance criteria and baseline are recorded.
- [ ] Candidate licenses, wheels, maintenance, and offline behavior are reviewed.
- [ ] Offline benchmark produces reproducible aggregate metrics.
- [ ] Optional adapter prototype exists only if targets are met.
- [ ] A clear GO/NO-GO/REVISIT verdict is recorded.

## STOP conditions

- No candidate has acceptable licensing or maintained Windows wheels.
- Representative, consented evaluation data cannot be obtained.
- Candidate requires cloud audio or administrator privilege.
- Plans 015/016 are not complete.

## Maintenance notes

Wake-word accuracy changes with microphones, accents, and environments. A GO
decision requires ongoing regression fixtures and an easy user-visible fallback.

