# Plan 018: Reload plugin tools, watchers, and sibling imports consistently

> **Executor instructions**: Complete watcher test infrastructure first. Preserve
> backward compatibility for valid bare `.py` plugins.
>
> **Drift check (run first)**: no git metadata is present. Confirm
> `plugin_loader.py:39-118`, `main.py:102-107`, and `overlay.py:4290-4297`.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: MED
- **Depends on**: plans 013, 017
- **Category**: bug
- **Planned at**: no git metadata available, 2026-08-13

## Why this matters

Live reload replaces `_watchers` with a new list, but the running scheduler keeps
its original snapshot, so disabled or deleted watcher functions continue to run.
Sibling modules may also exist under ordinary and synthetic module names, leaving
one plugin bound to stale code after another is edited. Malformed nested tool
metadata can escape validation and abort startup/reload. Reload should publish one
atomic, versioned plugin generation.

## Current state

- `main.py:102` snapshots `plugin_loader.get_watchers()` once.
- `plugin_loader.py:44-46` rebinds `_watchers` on every scan.
- Modules execute under `alyssa_plugin_<stem>` at `plugin_loader.py:61-66`, while
  plugins such as `news_digest.py` import sibling `web_search` normally.
- `plugin_loader.py:99-105` assumes `tool["function"]` is a dictionary.
- `overlay.py:4295-4296` reloads actions and brain schemas and promises live use.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Focused tests | `.\.venv\Scripts\python.exe -m unittest tests.test_runtime_plugin_reload -v` | all pass |
| Full tests | `.\.venv\Scripts\python.exe -m unittest discover -v` | all pass |

## Scope

**In scope**: `plugin_loader.py`, `actions.py`, `brain.py` only for atomic
generation publication, watcher scheduler integration in `main.py`, and plan-013
plugin reload tests.

**Out of scope**: plugin manifests/dependency installation (plan 025), sandboxing
trusted local plugins, or redesigning the GUI editor.

## Git workflow

If git exists, branch `advisor/018-plugin-reload`. Do not push.

## Steps

### Step 1: Define a plugin-generation object

Build functions, schemas, watchers, module identities, and load errors in local
temporary structures. Validate nested schema types completely. Publish the new
immutable generation only after the whole scan completes; keep the prior
generation active if a loader-level failure occurs.

**Verify**: malformed plugin fixtures are skipped/reported without aborting and
valid plugins in the same scan still load.

### Step 2: Use stable module identities

Load each plugin under one canonical package/module name and manage sibling
imports consistently. Before re-execution, invalidate only modules owned by the
plugin directory/generation—never arbitrary `sys.modules` entries. Ensure a
dependent plugin observes edited sibling code after reload.

**Verify**: a temporary plugin pair changes a sibling return value; after reload,
both direct and dependent calls return the new value.

### Step 3: Make the watcher scheduler generation-aware

Plan 017's scheduler must observe published generations, stop scheduling removed
watchers, retain or reset deadlines by a documented identity rule, and discard
late results from obsolete generations. Do not abruptly kill running Python code.

**Verify**: add, edit, disable, delete, and late-completion tests show only the
current generation can enqueue future alerts.

### Step 4: Publish actions and schemas atomically

Update `actions.FUNCTIONS`, plugin schemas, brain provider caches, and watchers as
one logical reload result. The model must never see a schema whose callable is
absent or an old callable paired with a new schema.

**Verify**: concurrent fake model/reload tests always observe a consistent pair.

## Test plan

- Use temporary plugin directories and modules; clean `sys.modules` afterward.
- Cover malformed nested metadata, import exception, duplicate name, sibling
  edit, watcher add/remove, late old result, and atomic schema/function pairing.

## Done criteria

- [ ] Disabled/deleted watchers stop future scheduling.
- [ ] Sibling edits are visible after reload through every importer.
- [ ] Malformed metadata cannot abort startup or live reload.
- [ ] Functions, schemas, watchers, and errors publish atomically.
- [ ] Focused/full suites pass repeatedly without module leakage.

## STOP conditions

- A valid existing plugin relies on duplicate module execution side effects.
- Stable identities require breaking the documented bare-plugin contract.
- The watcher scheduler from plan 017 is not generation-aware/extensible.

## Maintenance notes

Plan 025 should build manifests around this generation abstraction. Treat module
objects and plugin code as process-lifetime resources; reload is cooperative, not
a safe way to terminate already-running arbitrary code.

