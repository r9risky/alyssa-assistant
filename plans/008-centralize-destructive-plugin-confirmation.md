# Plan 008: Put destructive plugin actions behind the core confirmation protocol

> **Executor instructions**: Complete plan 007 first, then extend its public
> confirmation API. Never call private callback globals from plugins.
>
> **Drift check (run first)**: no git metadata is present. Compare
> `plugins/process_manager.py:26-78,123-178` and `brain.py:2593-2599`.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: plan 007
- **Category**: security
- **Planned at**: no git metadata available, 2026-08-13

## Why this matters

`kill_process` and `empty_recycle_bin` call a private confirmation callback and
return its `None` value rather than the dispatcher sentinel, so users are not
shown the queued approval question. Both fail open if callback initialization is
absent. `clean_temp_files` permanently deletes a recursive set of files with no
confirmation. All destructive tools need one fail-closed, request-bound protocol.

## Current state

- `plugins/process_manager.py:53-58` accesses
  `actions._critical_confirmation_callback` directly.
- `plugins/process_manager.py:123-145` walks `%TEMP%`/`%TMP%` and calls
  `os.remove()` without preview or confirmation.
- `plugins/process_manager.py:161-170` suppresses Windows recycle-bin UI after
  the same private/fail-open check.
- `brain.py:2593-2599` only asks the user when output equals
  `VOICE_CONFIRMATION_REQUIRED`.
- Core actions already keep approval state outside model arguments; extend that
  convention rather than accepting `confirmed` from tool JSON.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Focused tests | `.\.venv\Scripts\python.exe -m unittest tests.test_process_manager_confirmation -v` | all pass |
| Full tests | `.\.venv\Scripts\python.exe -m unittest discover -v` | all pass |
| Search | `rg -n "_critical_confirmation_callback" plugins` | no matches |

## Scope

**In scope**: `actions.py`, `brain.py` only if plan 007's public protocol needs a
small extension, `plugins/process_manager.py`, tool confirmation classification,
and `tests/test_process_manager_confirmation.py` (new).

**Out of scope**: changing process matching, redesigning temp cleanup selection,
adding elevation, or modifying unrelated plugins.

## Git workflow

If git exists, branch `advisor/008-plugin-confirmation`. Do not push.

## Steps

### Step 1: Expose a public fail-closed request helper

Use the plan-007 confirmation boundary through a public action-layer function.
It must either return the exact dispatcher sentinel, denial, or approved state;
missing callbacks/UI must never authorize. Keep request arguments immutable and
strip model-supplied `confirmed` as today.

**Verify**: unit tests cover missing backend, pending, deny, approve, timeout,
and replay for a fake plugin action.

### Step 2: Migrate process termination and recycle-bin emptying

Replace private callback access in `kill_process` and `empty_recycle_bin`.
Approval descriptions must include the exact matched process summary or recycle
bin action. Approved continuations must execute once.

**Verify**: mocked psutil/shell32 tests prove no terminate/kill/empty call occurs
before approval and no call occurs after deny/timeout.

### Step 3: Protect recursive temp deletion

Before deletion, resolve and validate the exact `%TEMP%`/`%TMP%` roots, calculate
a bounded preview (root paths and candidate count/bytes), then request approval.
Revalidate roots when resuming. Never follow a root outside the resolved temp
directories and never broaden to system-wide cleanup.

**Verify**: temporary-directory tests prove preview-only before approval,
deletion after approval, no traversal outside roots, and failure-closed behavior.

### Step 4: Register all three as protected tools

Ensure anticipatory speech does not claim completion before approval and that
the dispatcher recognizes their pending sentinel consistently.

**Verify**: an end-to-end mocked dispatcher test shows the question, approval,
one execution, and natural result for each tool.

## Test plan

- Mock `psutil`, `SHEmptyRecycleBinW`, environment variables, and filesystem
  deletion. Use disposable temporary directories only.
- Cover substring matches resolving multiple processes, no match, callback
  absence, stale request, denial, approval, and partial deletion errors.

## Done criteria

- [ ] No plugin accesses a private confirmation callback.
- [ ] All three destructive actions fail closed without a trusted backend.
- [ ] Temp targets are previewed and revalidated before deletion.
- [ ] Tests prove no side effect occurs before matching approval.
- [ ] Focused and full suites pass.

## STOP conditions

- Plan 007's request-bound API is not present.
- Safe temp-root resolution cannot be guaranteed on the target machine.
- A proposed fix requires allowing model-supplied approval flags.

## Maintenance notes

New plugins that terminate processes, delete data, empty stores, or invoke
privileged APIs must use the same public protocol and receive equivalent tests.

