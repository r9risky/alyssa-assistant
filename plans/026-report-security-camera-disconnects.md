# Plan 026: Report camera disconnects instead of claiming monitoring is armed

> **Executor instructions**: Keep this change local to camera state/error
> reporting and hardware-free tests. Update `plans/README.md` when complete.
>
> **Drift check (run first)**: no git metadata is present. Confirm
> `plugins/security_camera.py:56-83,86-120` still matches.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: plan 013
- **Category**: bug
- **Planned at**: no git metadata available, 2026-08-13

## Why this matters

After capture starts, repeated frame-read failures loop forever without setting an
error or clearing `_armed`. `camera_status()` then assures the user that motion
monitoring is armed even though no frames are being processed. Sustained failure
must transition atomically to a visible failed/disarmed state.

## Current state

- `plugins/security_camera.py:64-68` sleeps and retries forever when `cap.read()`
  returns false.
- `_camera_error` is only set for initial open failure.
- `camera_status()` at lines 118-120 reports solely from `_armed`.
- State is guarded by `_lock`; preserve that convention.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Focused tests | `.\.venv\Scripts\python.exe -m unittest tests.test_security_camera_state -v` | all pass |
| Full tests | `.\.venv\Scripts\python.exe -m unittest discover -v` | all pass |

## Scope

**In scope**: `plugins/security_camera.py` and
`tests/test_security_camera_state.py` (new or plan-013 extension).

**Out of scope**: changing motion sensitivity, recording footage, automatic
reconnect beyond a bounded documented policy, or real-camera CI.

## Git workflow

If git exists, branch `advisor/026-camera-disconnect-state`. Do not push.

## Steps

### Step 1: Define failure/retry policy

Choose a bounded consecutive-read-failure count or elapsed monotonic duration.
Transient failures below the bound retry; sustained failure sets a safe error,
clears armed state, signals stop, and exits the capture loop. Store state changes
under `_lock` and always release the device.

**Verify**: fake capture tests distinguish a transient failed read followed by
success from sustained disconnect.

### Step 2: Report actual state

`camera_status()` and `check_watch()` should distinguish off, armed, and failed.
Surface one bounded failure alert/status, not repeated spoken errors. A later
explicit enable clears the old error only after a new capture opens successfully.

**Verify**: tests cover initial open failure, runtime disconnect, status, one
alert, disable after failure, and successful re-enable.

## Test plan

- Mock `cv2.VideoCapture`, frames, clock, and stop events.
- Assert thread exits, `release()` runs once, `_armed` is false, error is visible,
  and no repeated alerts occur.

## Done criteria

- [ ] Sustained read failure terminates capture and clears armed state.
- [ ] Status never reports armed when the capture loop has failed.
- [ ] Failure notification is bounded and re-enable behavior is explicit.
- [ ] Focused/full tests pass without camera hardware.

## STOP conditions

- The intended camera backend documents unlimited false reads during normal use.
- A reliable failure threshold cannot be selected without representative data.
- State changes conflict with plan-013 watcher coordination interfaces.

## Maintenance notes

If automatic reconnection is added later, represent “reconnecting” explicitly;
never overload `_armed` to mean both desired and actual monitoring state.

