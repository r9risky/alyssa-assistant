# Plan 015: Coordinate proactive alerts with the listening state machine

> **Executor instructions**: Complete plans 013 and 014 first. Route state
> through one coordinator; do not add another independent lock/thread shortcut.
>
> **Drift check (run first)**: no git metadata is present. Confirm
> `main.py:26-85,88-126,336-354` still matches.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: plans 013, 014
- **Category**: bug
- **Planned at**: no git metadata available, 2026-08-13

## Why this matters

The watcher thread calls `speak()` while the main thread may be blocked in
ordinary microphone recording. Proactive speech then starts a second barge-in
listener, competes for the mic, can be recorded as user input, and discards any
audio returned when the user interrupts the alert. Alerts and reactive turns
need one explicit speech/listen coordinator.

## Current state

- `main.py:54` serializes speech only; it does not coordinate recording.
- `main.py:119-125` runs watcher code and calls `speak(alert, bridge)` directly.
- `main.py:336-354` may concurrently wait in `recorder.record_command()`.
- `speak()` creates a barge-in mic listener at `main.py:67-73`.
- `run_watcher_loop()` ignores the audio returned by `speak()`.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Focused tests | `.\.venv\Scripts\python.exe -m unittest tests.test_runtime_watcher_coordination -v` | all pass |
| Full tests | `.\.venv\Scripts\python.exe -m unittest discover -v` | all pass, no leaked threads |

## Scope

**In scope**: `main.py`, a narrowly named coordinator module only if separation
materially simplifies tests, and plan-013 watcher/audio tests.

**Out of scope**: parallelizing watcher checks (plan 017), changing plugin
contracts, TTS provider code, or PortAudio recovery internals (plan 016).

## Git workflow

If git exists, branch `advisor/015-speech-listen-coordinator`. Do not push.

## Steps

### Step 1: Model input/speech ownership

Define states such as idle, normal-listening, speaking-with-barge-in, processing,
and stopping. Only the coordinator may transition between them. Watcher threads
enqueue alerts; they do not call `speak()` directly.

**Verify**: pure state tests reject illegal simultaneous normal-listening and
barge-in-listening ownership.

### Step 2: Deliver alerts through the main loop

At a safe boundary, pause/cancel the normal recorder, speak one queued alert,
and route returned interruption audio into `pending_interrupt_audio`. Resume
normal listening only after alert speech/listener cleanup completes. Preserve
serialized speech and avoid dropping queued alerts.

**Verify**: event-controlled integration tests show an alert arriving during
listen, processing, and reactive speech; no simultaneous input streams occur.

### Step 3: Define queue policy

Use a bounded queue and deduplicate identical pending alerts by plugin/name or
message. User-requested replies take priority. Document what happens when the
queue is full and ensure timer alerts are not silently overwritten by low-value
status alerts.

**Verify**: tests cover ordering, deduplication, queue bound, and interruption of
a proactive alert becoming the next user command.

## Test plan

- Use coordinator events and fake record/speak functions; no sound hardware.
- Assert one input owner, user-reply priority, bounded queue, exact-once alert
  speech, and interruption handoff.
- Run tests repeatedly to expose ordering assumptions.

## Done criteria

- [ ] Watcher threads never call `speak()` directly.
- [ ] Normal and barge-in recording never overlap.
- [ ] Interrupting an alert delivers the captured command exactly once.
- [ ] User replies outrank proactive alerts; queue behavior is bounded/documented.
- [ ] Focused and full suites pass three times.

## STOP conditions

- Plan 014 has not established a completed/cancelled interruption contract.
- The coordinator would require Qt objects from the console-only path.
- A proposed design blocks the main loop on an unbounded watcher/network call.

## Maintenance notes

All future proactive sources should enqueue alerts through this boundary. The
speech mutex alone is not sufficient because microphone ownership matters too.

