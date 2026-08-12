# Plan 017: Isolate slow plugin watchers without overlapping their runs

> **Executor instructions**: Complete plan 015 first so watcher results enqueue
> alerts instead of speaking. Keep the public `check_watch() -> str | None` contract.
>
> **Drift check (run first)**: no git metadata is present. Confirm
> `main.py:88-126`, `plugins/system_watch.py:130-136`, and timer watcher code.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: MED
- **Depends on**: plans 013, 015
- **Category**: perf
- **Planned at**: no git metadata available, 2026-08-13

## Why this matters

All due watchers run sequentially. The system watcher intentionally blocks for
one second, and Calendar performs live network I/O; either can delay short timers
and every other alert. Each watcher should run in a bounded worker without a
second invocation overlapping its prior run, while speech remains serialized by
the coordinator from plan 015.

## Current state

- `main.py:109-126` invokes due watcher functions inline on one loop thread.
- `plugins/system_watch.py:136` calls `psutil.cpu_percent(interval=1.0)`.
- `plugins/calendar_gmail.py:276-282` can perform an external API call.
- `plugins/timers.py:15` asks for 5-second polling, but `plugin_loader.py:115`
  clamps all intervals to a minimum of 10 seconds.
- Watcher entries contain `name`, `func`, and `interval`.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Focused tests | `.\.venv\Scripts\python.exe -m unittest tests.test_runtime_watchers -v` | all pass |
| Full tests | `.\.venv\Scripts\python.exe -m unittest discover -v` | all pass, no worker leaks |

## Scope

**In scope**: `main.py`, `plugin_loader.py` interval validation, plan-013 watcher
tests/fakes, and comments defining scheduling guarantees.

**Out of scope**: converting the app to asyncio, changing plugin function
signatures, watcher reload identity (plan 018), or modifying individual API calls.

## Git workflow

If git exists, branch `advisor/017-watcher-isolation`. Do not push.

## Steps

### Step 1: Add bounded watcher execution state

Track per-watcher next-due time, running flag/future, and generation. Submit due
checks to a small bounded executor or explicit worker pool. Never submit a second
run while one is active. A hung watcher must not consume all worker capacity;
define bounded capacity and visible health diagnostics.

**Verify**: fake-clock tests prove slow A does not delay fast B or timer C and A
never overlaps itself.

### Step 2: Collect results through the alert coordinator

On completion, capture exceptions as safe diagnostics and enqueue non-empty
alerts through plan 015. Preserve per-plugin deduplication semantics and do not
call GUI/TTS APIs from worker threads.

**Verify**: tests prove result ordering is completion-based, exceptions do not
stop scheduling, and user-reply priority remains intact.

### Step 3: Honor safe short intervals

Replace the blanket 10-second clamp with validated bounds that allow the shipped
timer's 5 seconds. Keep a reasonable absolute minimum (for example one second)
to prevent accidental busy loops. Use monotonic time for deadlines.

**Verify**: a timer due just after a poll is announced within the documented
five-second cadence under a fake clock; invalid/zero intervals are clamped safely.

### Step 4: Shut down cleanly

Add a stop event/executor shutdown path usable by tests and application exit.
Do not wait forever for arbitrary plugin network calls during shutdown.

**Verify**: focused tests finish with zero watcher workers/futures left running.

## Test plan

- Cover simultaneous due watchers, slow/hung watcher, exception, repeated due
  while running, queue backpressure, 5-second timer cadence, and shutdown.
- Do not use real clocks, network, or psutil delays.

## Done criteria

- [ ] One slow watcher cannot delay other due watchers.
- [ ] A watcher never overlaps itself.
- [ ] Timer cadence honors five seconds within scheduler tolerance.
- [ ] Results enter the plan-015 alert coordinator.
- [ ] Focused/full tests pass repeatedly with no leaked workers.

## STOP conditions

- Plan 015's alert queue/coordinator is absent.
- Bounded worker capacity cannot isolate a permanently hung plugin.
- A shipped plugin depends on all watchers being strictly sequential.

## Maintenance notes

Do not replace this with unbounded thread-per-check execution. Record watcher
duration/status so future slow plugins are diagnosable without logging content.

