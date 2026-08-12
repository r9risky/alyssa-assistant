# Plan 016: Make PortAudio stream admission and recovery mutually exclusive

> **Executor instructions**: Use the plan-013 fake blocking streams. This native
> lifecycle change must not be attempted without deterministic interleaving tests.
>
> **Drift check (run first)**: no git metadata is present. Confirm
> `recorder.py:21-46,302-327` still matches.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: HIGH
- **Depends on**: plans 013, 014, 015
- **Category**: bug
- **Planned at**: no git metadata available, 2026-08-13

## Why this matters

Recovery observes `_active_streams == 0`, releases that lock, and only later
calls global `sd._terminate()`/`_initialize()`. A new stream can open in between.
The code's own comments correctly note that tearing down PortAudio with a live
stream can corrupt the heap and crash without a Python traceback. Admission and
recovery need one atomic state protocol.

## Current state

- `_open_stream()` at `recorder.py:34-46` constructs/enters `sd.InputStream`
  without holding `_recovery_lock`; it increments after stream entry.
- `_reap_and_recover()` at `recorder.py:316-325` waits for zero under
  `_active_streams_lock`, releases it, then acquires `_recovery_lock` and resets.
- Multiple reaper threads can exist after repeated stalls.
- Private sounddevice reset APIs are already used; preserve a contained fallback
  and make failures nonfatal.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Focused tests | `.\.venv\Scripts\python.exe -m unittest tests.test_runtime_portaudio_recovery -v` | all pass |
| Full tests | `.\.venv\Scripts\python.exe -m unittest discover -v` | all pass, no leaked threads |

## Scope

**In scope**: `recorder.py`, plan-013 fake stream/recovery tests, and comments
documenting lock/state invariants.

**Out of scope**: changing VAD, adaptive silence, selecting devices, replacing
sounddevice, or swallowing persistent hardware failure.

## Git workflow

If git exists, branch `advisor/016-portaudio-recovery`. Do not push.

## Steps

### Step 1: Specify the invariant

Introduce a condition/state protected by one lock: stream admission waits while
recovery is pending/running; recovery waits for active streams to reach zero and
sets “recovering” before releasing the lock; stream construction, count
increment, lifetime, and decrement follow a documented order. Coalesce multiple
recovery requests into one generation.

**Verify**: state-only tests prove no admission between zero observation and
recovery completion, and counters never go negative.

### Step 2: Implement admission/recovery coordination

Refactor `_open_stream()` and `_reap_and_recover()` around the invariant. Do not
hold the condition lock during a potentially blocking stream read; do hold the
right admission state across stream construction. Always notify waiters after
close or recovery failure.

**Verify**: deterministic interleaving test pauses recovery after zero detection,
attempts a new open, and proves it cannot enter until reset finishes.

### Step 3: Handle permanent stalls and shutdown

Ensure a worker that never returns does not spawn unbounded reapers or admit
unsafe competing recovery. Surface status while letting the process remain
responsive where safe. Add a bounded shutdown/test escape for fakes; do not call
unsafe force-close APIs on a live real stream.

**Verify**: repeated-stall tests show one recovery generation, bounded helper
threads, and no reset while a fake stream is active.

## Test plan

- Cover normal open/close, recovery with zero streams, open racing recovery,
  multiple active streams, repeated recovery requests, reset exception, and
  permanently stuck worker.
- Use events/barriers, not time-based luck.

## Done criteria

- [ ] New streams cannot enter while recovery is pending/running.
- [ ] Global reset can only occur with zero admitted streams.
- [ ] Multiple reapers coalesce; counters and notifications remain consistent.
- [ ] Tests terminate cleanly and pass repeatedly.
- [ ] Full suite passes.

## STOP conditions

- Plan 013 fake streams cannot reproduce the critical interleaving.
- Sounddevice semantics show stream construction itself must occur outside the
  protected admission window.
- Safe behavior requires a different audio backend.

## Maintenance notes

Review lock ordering whenever capture paths change. Keep the invariant in the
module docstring beside the state, not only in tests.

