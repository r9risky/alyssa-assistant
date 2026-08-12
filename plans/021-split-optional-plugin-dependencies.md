# Plan 021: Split optional plugin stacks from the core installation

> **Executor instructions**: Complete reproducible constraints first. Preserve a
> one-click core setup and actionable plugin-unavailable messages.
>
> **Drift check (run first)**: no git metadata is present. Confirm optional
> dependencies at `requirements.txt:38-52` and graceful imports in plugins.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: MED
- **Depends on**: plan 020
- **Category**: migration
- **Planned at**: no git metadata available, 2026-08-13

## Why this matters

Every first-time user installs OpenCV and the Google OAuth/API stack even if they
only want the core assistant. The plugins already detect missing libraries and
degrade safely, so mandatory installation adds download size, disk usage,
resolution time, and native compatibility failures without benefiting core use.

## Current state

- `requirements.txt:38-52` labels psutil, OpenCV, Google clients, and Beautiful
  Soup as plugin-related but installs all of them.
- `start_alyssa.bat:87-94` installs every selected requirements file wholesale.
- `plugins/calendar_gmail.py:32-41`, `security_camera.py:25-28`, and other plugins
  guard optional imports and return human-readable missing-library messages.
- Plan 003 intentionally added Beautiful Soup to the then-single requirements
  file; retain it in the appropriate web capability profile, not accidentally omit it.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Core tests | `.\.venv\Scripts\python.exe -m unittest discover -v` | all core-safe tests pass |
| Core consistency | `.\.venv\Scripts\python.exe -m pip check` | no broken requirements |
| Plugin smoke | `.\.venv\Scripts\python.exe -m unittest tests.test_plugin_profiles -v` | all profiles/fallbacks pass |

## Scope

**In scope**: requirements/constraint profile files, `start_alyssa.bat`, plugin
availability/diagnostic messages, Settings UI only for profile selection/status,
`tests/test_plugin_profiles.py`, and README installation text.

**Out of scope**: runtime pip installs triggered by model calls, arbitrary third-
party plugin dependency installation, or manifest design (plan 025).

## Git workflow

If git exists, branch `advisor/021-plugin-dependency-profiles`. Do not push.

## Steps

### Step 1: Define capability profiles

Keep a minimal core file for voice assistant runtime. Create named, documented
profiles such as system monitoring (`psutil`), camera (`opencv-python`), Google
productivity, and web summarization (`beautifulsoup4`). Map each to reviewed
constraints from plan 020. Decide whether a “recommended all” profile preserves
today's convenience as an explicit choice.

**Verify**: a table maps every direct dependency to exactly one core/profile
owner and every shipped plugin's imports are covered.

### Step 2: Let setup choose profiles safely

Provide a simple launcher/Settings selection before installation, defaulting to
core or an explicitly documented recommended set. Incorporate selected profiles
and file hashes into the install stamp. Never install packages merely because an
LLM selected a tool.

**Verify**: clean core install omits OpenCV/Google clients; selecting profiles
installs only mapped files; repeated launch skips correctly.

### Step 3: Improve unavailable-plugin diagnostics

Plugin load should remain successful when optional libraries are absent. Surface
one actionable status telling the user which profile to install, without repeated
watcher nags or tracebacks.

**Verify**: mocked import-absence tests prove startup/core tools work and affected
plugins report the exact profile command/action.

### Step 4: Test every supported combination

At minimum test core-only, each individual profile, recommended-all, and GPU plus
profiles on applicable matrix cells. Run full tests where imports exist and
fallback tests where they do not.

**Verify**: resolver matrix and plugin smoke tests pass; `pip check` is clean.

## Test plan

- Mock missing imports for deterministic fallback assertions.
- Test stamp changes when profiles toggle.
- Confirm core launch does not import or require optional native libraries.

## Done criteria

- [ ] Core install excludes plugin-only stacks.
- [ ] Each shipped optional plugin maps to a named profile.
- [ ] Missing profiles degrade gracefully with actionable guidance.
- [ ] Profile selection is included in reproducibility/stamp behavior.
- [ ] All profile matrix checks pass.

## STOP conditions

- A supposedly optional dependency is imported unconditionally by core startup.
- Plan 020 constraints cannot express the profiles reproducibly.
- Completing this requires runtime arbitrary package installation.

## Maintenance notes

Plan 025 may expose these profiles through manifests. Keep install authority with
the user, not with plugins or the model.

