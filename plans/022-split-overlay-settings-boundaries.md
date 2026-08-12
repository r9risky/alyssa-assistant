# Plan 022: Extract settings services from the overlay monolith

> **Executor instructions**: This is a staged refactor. Preserve
> `overlay.run_with_assistant()` and visible behavior; stop if characterization
> coverage is insufficient for a proposed extraction.
>
> **Drift check (run first)**: no git metadata is present. Confirm
> `overlay.py:1533-1787`, `2033-4594`, and `4039-4307` still define the cited areas.

## Status

- **Priority**: P3
- **Effort**: L
- **Risk**: HIGH
- **Depends on**: plans 005, 013, 018
- **Category**: tech-debt
- **Planned at**: no git metadata available, 2026-08-13

## Why this matters

`overlay.py` is roughly 4,800 lines. `ConfigDialog` alone spans about 2,560 lines
and mixes Qt layout/signals, provider network verification, secret/config
persistence, model discovery, plugin filesystem editing, and live reload. Small
settings changes therefore collide in one high-risk module. Extract stable,
testable services first; splitting visual tabs comes only after behavior is pinned.

## Current state

- `overlay.py:1533-1787` implements provider credential/model verification.
- `overlay.py:1789-1804` patches Python config source.
- `overlay.py:2033-4594` contains `ConfigDialog`.
- `overlay.py:4039-4307` contains plugin editor/reload orchestration.
- `overlay.py:4400-4565` gathers, persists, and applies settings live.
- `overlay.py:4734+` exposes `run_with_assistant()`; this is the public entry point.
- Pure helper tests and runtime/plugin tests should exist after plans 005, 013, 018.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Focused tests | `.\.venv\Scripts\python.exe -m unittest tests.test_overlay_services -v` | all pass |
| Full tests | `.\.venv\Scripts\python.exe -m unittest discover -v` | all pass |
| Import smoke | `.\.venv\Scripts\python.exe -c "import overlay; assert callable(overlay.run_with_assistant)"` | exit 0 |

## Scope

**In scope**: `overlay.py`, new modules narrowly scoped to provider verification,
settings persistence/application, and plugin-editor backend operations, plus
`tests/test_overlay_services.py` and existing relevant tests.

**Out of scope**: visual redesign, changing QSS/theme tokens, replacing Qt,
changing provider APIs, plugin manifest UX (plan 025), or renaming the public entry.

## Git workflow

If git exists, branch `advisor/022-overlay-service-extraction`. Commit each
behavior-preserving extraction separately; do not push.

## Steps

### Step 1: Add characterization at the seams

Cover provider verification request shapes/results, safe settings persistence
from plan 005, live config application, Whisper reload triggering, Spotify cache
reset, and plugin backend reload. Mock Qt/network/filesystem as appropriate.

**Verify**: focused tests pass against the current implementation before moving code.

### Step 2: Extract provider verification

Move `_verify_*`, model-list fetching orchestration, and result normalization to a
Qt-independent service. Accept dependencies/inputs explicitly; return typed/simple
results without touching widgets. Leave signal wiring in `ConfigDialog`.

**Verify**: characterization tests pass and `overlay.py` has no direct provider
HTTP verification implementation.

### Step 3: Extract settings persistence/application

Move config read/write, protected secret persistence, value validation, and live
application decisions behind a service. It should report which subsystems need
reload; the dialog performs UI notifications. Preserve atomic writes and error text.

**Verify**: tests cover write failure, secret exclusion, no-op, model reload, and
cache invalidation; full suite passes.

### Step 4: Extract plugin-editor backend

Move safe filename handling, file CRUD, generation reload, and error collection
into a service built on plan 018. Keep editor widgets/dialogs in `overlay.py`.

**Verify**: temporary-directory CRUD/reload tests pass; no backend code imports Qt.

### Step 5: Split UI sections only if still valuable

After services land, assess file/class size. Extract coherent tab-building
components while retaining ownership/lifetime of signals and timers. Do not split
for line-count alone if it makes Qt relationships less clear.

**Verify**: import smoke, full tests, and a manual Settings smoke covering every tab.

## Test plan

- Use dependency injection/mocks for HTTP, filesystem, secret store, config, and
  reload functions.
- Manually test open/close, live apply, provider verify, plugin edit/reload, theme,
  engine change, tray behavior, and console visibility.

## Done criteria

- [ ] Provider verification, settings persistence, and plugin backend are Qt-independent.
- [ ] `overlay.run_with_assistant()` remains compatible.
- [ ] No visual/behavioral redesign is bundled into the refactor.
- [ ] Focused/full tests and import smoke pass.
- [ ] Manual Settings checklist passes on Windows.

## STOP conditions

- Characterization cannot observe a load-bearing Qt lifetime/signal behavior.
- Extraction requires changing provider or plugin public behavior.
- Plan 005 or 018 interfaces are not stable/present.

## Maintenance notes

Future settings features should place policy/I/O in services and widget wiring in
Qt. Review signal ownership, debounce timers, and object lifetimes closely.

