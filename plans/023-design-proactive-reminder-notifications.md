# Plan 023: Design and validate proactive dated-reminder notifications

> **Executor instructions**: This is a design/spike plan. Produce a reviewed
> design and test prototype; do not silently ship notifications until the open
> policy questions have explicit answers.
>
> **Drift check (run first)**: no git metadata is present. Confirm
> `plugins/reminders.py:9-12,99-188` and `plugins/timers.py:130-147`.

## Status

- **Priority**: P2
- **Effort**: S-M
- **Risk**: MED
- **Depends on**: plans 010, 013, 015, 017
- **Category**: direction
- **Planned at**: no git metadata available, 2026-08-13

## Why this matters

Users saying “remind me tomorrow at 3” normally expect an unsolicited alert,
but the current plugin explicitly requires them to ask what is due. The data
model already stores ISO due times and the plugin system already supports
watchers. A small, persisted notification state could close this expectation
gap, provided restart, overdue, deduplication, and quiet-time semantics are clear.

## Current state

- `plugins/reminders.py:9-12` states there is no background scheduler.
- `plugins/reminders.py:128-143` persists `id`, `task`, `due`, `done`, `created`.
- `plugins/reminders.py:151-188` already parses due values and identifies overdue
  or upcoming items.
- `plugins/timers.py:130-147` demonstrates a `check_watch() -> str | None` alert.
- Plans 015/017 provide coordinated, isolated watcher delivery; use that path.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Existing tests | `.\.venv\Scripts\python.exe -m unittest discover -v` | all pass |
| Spike tests | `.\.venv\Scripts\python.exe -m unittest tests.test_reminder_notifications -v` | all pass |

## Scope

**In scope**: `docs/reminder-notifications-design.md` (new), a test-only or
feature-flagged prototype in `plugins/reminders.py`,
`tests/test_reminder_notifications.py`, and a README note only after approval.

**Out of scope**: cloud push/mobile sync, OS toast integration, recurrence,
natural-language parser expansion, or silently enabling unapproved behavior.

## Git workflow

If git exists, branch `advisor/023-reminder-notification-spike`. Do not push.

## Steps

### Step 1: Decide notification semantics

Write the design answering: exact due-window threshold; what happens after the app
was closed; whether overdue reminders notify once; how snooze/dismiss/completion
interact; time-zone/DST behavior; maximum alerts per wake; quiet hours; and how
notification state survives crashes. Recommend: notify once at/after due,
persist `notified_at`, coalesce startup backlog, and never mark done automatically.

**Verify**: design contains an explicit decision and rationale for every question,
plus rejected alternatives.

### Step 2: Specify backward-compatible storage

Add optional notification fields that old entries can omit. Define atomic-write
behavior and recovery from corrupt/missing fields. Keep ISO timestamps and assign
stable IDs; do not infer notification state solely from process memory.

**Verify**: fixture migration tests load legacy/current/malformed entries without
losing tasks or duplicating IDs.

### Step 3: Prototype `check_watch()` behind a flag

Use an injected clock and the plan-017 watcher contract. Under a disabled-by-
default experimental flag, return one bounded/coalesced alert, persist notified
state atomically before or in a crash-safe sequence, and avoid repeat alerts.

**Verify**: fake-clock tests cover before due, exact due, after due, restart,
multiple due, completed/deleted, DST boundary, write failure, and quiet hours.

### Step 4: Review and decide rollout

Present design, test results, sample spoken output, and unresolved tradeoffs. Only
after maintainer approval should the flag default change and README promise the
feature.

**Verify**: design records APPROVED/REJECTED/NEEDS CHANGES and the chosen rollout.

## Test plan

- Use temporary JSON and deterministic aware datetimes; no real sleeping.
- Assert exact-once semantics across simulated restarts and write failures.
- Test compatibility with list/complete/delete behavior.

## Done criteria

- [ ] Design answers notification, restart, dedupe, quiet-hour, and DST policy.
- [ ] Storage evolution is backward compatible and atomically tested.
- [ ] Feature-flagged prototype passes deterministic tests.
- [ ] No production-default behavior changes without recorded approval.

## STOP conditions

- Plans 015/017 are absent, leaving no safe alert-delivery path.
- Product owner cannot decide overdue/quiet-hour semantics.
- Exact-once persistence cannot be achieved without a larger storage migration.

## Maintenance notes

Recurrence and sync should build on explicit occurrence IDs, not overload a
single reminder's `notified_at` field.

