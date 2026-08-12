# Plan 014: Make interruption-audio handoff lossless and explicit

> **Executor instructions**: Complete plan 013 first and use its deterministic
> audio fakes. Do not rely on sleep-based timing tests.
>
> **Drift check (run first)**: no git metadata is present. Compare
> `main.py:40-85,424-440` and `recorder.py:195-299` with these excerpts.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: plan 013
- **Category**: bug
- **Planned at**: no git metadata available, 2026-08-13

## Why this matters

Interruption audio is a NumPy array, but `main.py` combines it with `or`, which
asks NumPy for an ambiguous truth value and raises. The speech wrapper also waits
only two seconds and reads a shared result before a listener may have finished
recording or transcribing. Users lose the command they spoke while interrupting
and can leave overlapping microphone streams behind.

## Current state

- `main.py:437-440` assigns `final_interrupt_audio or partial...`.
- `recorder.py:272` returns a multi-element `np.ndarray`.
- `main.py:81-85` signals playback done, joins for two seconds, then returns the
  current dictionary value even if the listener remains alive.
- `recorder.py:260-285` can record up to `MAX_RECORD_SECONDS` and transcribe
  without observing cancellation during phase two/name verification.
- `main.py:346-350` already has a `pending_interrupt_audio` queue point; preserve it.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Focused tests | `.\.venv\Scripts\python.exe -m unittest tests.test_runtime_interruption -v` | all pass |
| Full tests | `.\.venv\Scripts\python.exe -m unittest discover -v` | all pass, no leaked threads |
| Search | `rg -n "interrupt_audio.*\bor\b|\bor\b.*interrupt_audio" main.py` | no NumPy truthiness selection |

## Scope

**In scope**: `main.py`, `recorder.py`, `voice.py`, the interruption tests/fakes
from plan 013, and comments/docstrings describing the completion contract.

**Out of scope**: PortAudio global recovery (plan 016), watcher coordination
(plan 015), changing VAD/name-gating thresholds, or changing provider APIs.

## Git workflow

If git exists, branch `advisor/014-interruption-handoff`. Do not push.

## Steps

### Step 1: Replace implicit audio truthiness

Select final audio when `is not None`; otherwise select the first partial audio
when present. Never use array truthiness, length, or content as a presence check.

**Verify**: tests cover final-only, partial-only, both (final wins), empty NumPy
array as a present value, and neither.

### Step 2: Define listener result states

Replace the shared dictionary with a small result channel/future that reports
completed-with-audio, completed-without-audio, cancelled, or failed. `speak()`
must not read a result before completion. When playback ends after interruption
has triggered, allow a bounded utterance-completion window; otherwise cancel the
listener promptly and wait for confirmed stream closure.

**Verify**: event-controlled tests exercise playback-first, trigger-first,
slow-transcription, typed-input cancellation, and listener exception paths.

### Step 3: Prevent overlapping capture after return

Before `speak()` returns without audio, prove its barge-in listener has stopped
and released the stream. If cancellation cannot stop a blocked device read,
hand ownership to the existing recovery mechanism and prevent a new normal
listen from opening until admission is safe.

**Verify**: the fake stream asserts at most one admitted capture owner and every
test terminates with zero listener threads.

### Step 4: Terminate interrupted TTS cleanup workers

`voice.py:381-396` starts synthesis lazily but its cleanup helper waits on every
sentence event. After an early interruption, later entries may never have a
thread and their events never fire, leaving a daemon cleanup thread blocked
forever. Track which synthesis jobs actually started and clean only those, or
mark never-started entries complete during cancellation.

**Verify**: interrupt at every sentence boundary, including before more than one
future sentence was scheduled; all started temp files are removed and no cleanup
thread remains alive.

## Test plan

- Use actual NumPy arrays and event-driven fake streams/transcription.
- Assert returned audio object identity, not just equality.
- Cover interruption during partial and final speech, cancellation before
  trigger, exception, timeout, repeated interruptions, and multi-sentence cleanup.

## Done criteria

- [ ] No NumPy array is evaluated as a boolean.
- [ ] A triggered interruption is either delivered once or explicitly cancelled.
- [ ] `speak()` never consumes an incomplete result.
- [ ] No capture/listener thread leaks in focused or full tests.
- [ ] Interrupted pipelined TTS leaves no blocked cleanup thread or temp file.
- [ ] All tests pass on three consecutive runs.

## STOP conditions

- Plan 013's deterministic audio fakes are unavailable.
- The sounddevice backend cannot support bounded cancellation without changes
  assigned to plan 016.
- A proposed fix discards already-triggered speech silently.

## Maintenance notes

Keep result state explicit; `None` means “no audio,” not “listener may still be
working.” Review future changes for thread ownership and single delivery.
