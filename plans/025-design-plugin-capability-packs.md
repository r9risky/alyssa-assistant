# Plan 025: Design backward-compatible plugin capability packs

> **Executor instructions**: This is a design/spike plan. Do not let plugins or
> the model install packages or grant permissions automatically.
>
> **Drift check (run first)**: no git metadata is present. Confirm the loader
> contract at `plugin_loader.py:39-118`, GUI editor at `overlay.py:4039-4307`,
> and optional dependency mapping from plan 021.

## Status

- **Priority**: P3
- **Effort**: M-L
- **Risk**: MED
- **Depends on**: plans 006, 008, 018, 020, 021, 022
- **Category**: direction
- **Planned at**: no git metadata available, 2026-08-13

## Why this matters

The runtime already has a concise tool/watcher plugin contract and a GUI editor,
but dependency needs, setup, permissions, and user-facing metadata live in source
comments or the monolithic requirements file. A lightweight manifest could let
users understand and enable supported capabilities safely. It must retain bare
`.py` compatibility and keep code installation and sensitive permissions under
explicit user control.

## Current state

- `plugin_loader.py:39-118` discovers `.py` files, executes them, validates
  `FUNCTIONS`/`TOOLS`, and collects optional watchers.
- `overlay.py:4039-4307` lists, edits, creates, enables, deletes, and reloads plugins.
- Plan 018 introduces atomic generations/stable identities.
- Plan 021 maps shipped integrations to optional dependency profiles.
- Shipped plugins can access processes, files, network, camera, email/calendar,
  and system state; there is no declarative capability/permission metadata.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Existing tests | `.\.venv\Scripts\python.exe -m unittest discover -v` | all pass |
| Manifest prototype | `.\.venv\Scripts\python.exe -m unittest tests.test_plugin_manifests -v` | all pass |

## Scope

**In scope**: `docs/plugin-capability-packs.md`, a schema example, parser/
validator prototype behind a flag, migration examples for shipped plugins, and
`tests/test_plugin_manifests.py`.

**Out of scope**: public marketplace/download service, arbitrary pip execution,
cryptographic signing infrastructure, process sandboxing claims, or removing
legacy bare `.py` support.

## Git workflow

If git exists, branch `advisor/025-plugin-capability-packs`. Do not push.

## Steps

### Step 1: Define goals and threat boundaries

State that manifests improve discoverability, dependency guidance, configuration,
and user consent; they do not sandbox Python code. Define trust levels for shipped,
local user-authored, and externally obtained plugins. List sensitive capabilities:
filesystem read/write/delete, shell/process control, input automation, screen,
camera/mic, network, email/calendar, secrets, background watchers, and elevation
(which must remain prohibited for the assistant runtime).

**Verify**: design explicitly distinguishes declarative permission UX from real
OS isolation and references least-privilege/confirmation behavior.

### Step 2: Specify a minimal versioned manifest

Define stable fields: schema version, ID, display name, description, plugin entry,
app version compatibility, dependency profile names (not arbitrary commands),
settings with secret/non-secret classification, capabilities, watcher metadata,
and optional homepage/license. Choose JSON or TOML already supported by the
standard library/project; avoid adding a parser dependency without need.

**Verify**: JSON Schema or equivalent validator fixtures accept a minimal and a
full manifest and reject unknown versions, traversal entry paths, arbitrary
install commands, malformed settings, and undeclared sensitive capabilities.

### Step 3: Define legacy compatibility and discovery

Manifest-backed packs should coexist with current bare `.py` plugins. The loader
must never reinterpret an existing file as a directory pack unexpectedly.
Specify canonical IDs/module names aligned with plan 018 and deterministic
collision behavior.

**Verify**: prototype tests load bare-only, pack-only, mixed, duplicate-ID, and
invalid-pack directories while preserving valid legacy behavior.

### Step 4: Define explicit install/enable/permission UX

Settings should show required profiles, configuration fields, sensitive
capabilities, and watcher cadence before enablement. Dependency installation is a
separate user-confirmed launcher action using plan-020/021 reviewed constraints.
No plugin or model may supply an arbitrary command. Capability changes require
renewed consent; destructive actions still use plan 008 at call time.

**Verify**: wireframe/state table covers unavailable dependency, enable, deny,
capability change, disable, uninstall, and reload failure.

### Step 5: Prototype two shipped packs and render a verdict

Prototype one read-only plugin (weather/system status) and one sensitive plugin
(camera or process manager) behind a flag. Measure migration complexity and list
open questions. Record GO, REVISE, or NO-GO before broad conversion.

**Verify**: prototype parser/loader tests pass; legacy mode remains default unless
the design is explicitly approved.

## Test plan

- Validate schema, paths, versions, collisions, settings, profile references,
  capability changes, legacy coexistence, and atomic reload.
- Never run real plugin install commands or sensitive OS operations.

## Done criteria

- [ ] Design states goals, non-goals, trust model, and sensitive capabilities.
- [ ] Minimal versioned schema rejects arbitrary installers and unsafe paths.
- [ ] Bare `.py` compatibility and collision rules are explicit/tested.
- [ ] Enable/install/permission UX requires deliberate user action.
- [ ] Two prototypes and a GO/REVISE/NO-GO verdict are recorded.

## STOP conditions

- Plan 018 stable plugin identity/generation work is absent.
- Dependency profiles from plan 021 cannot be referenced declaratively.
- Stakeholders expect manifests alone to sandbox arbitrary Python.
- Proposed UX permits plugins or the model to install/approve automatically.

## Maintenance notes

If external distribution becomes real, address signatures, provenance, updates,
and isolation in separate plans. Do not imply those guarantees here.

