# Plan 020: Make CPU and GPU installations reproducible

> **Executor instructions**: Preserve the unzip-and-run Windows experience. Do
> not blindly freeze versions from the current venv without clean-matrix testing.
>
> **Drift check (run first)**: no git metadata is present. Confirm
> `requirements.txt`, `requirements-gpu.txt`, and `start_alyssa.bat:61-95`.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: MED
- **Depends on**: plan 013
- **Category**: migration
- **Planned at**: no git metadata available, 2026-08-13

## Why this matters

Almost every native/runtime package is unbounded and setup upgrades pip before
resolving the latest graph. Identical source archives installed on different
dates can receive incompatible Qt, audio, Whisper, OpenCV, or CUDA stacks while
existing machines keep working. Reproducible reviewed constraints and a concrete
Python support matrix make failures diagnosable and releases repeatable.

## Current state

- `requirements.txt:1-52` has unpinned direct requirements except `setuptools<81`.
- `requirements-gpu.txt:12-14` leaves two CUDA packages unpinned and allows any
  cuDNN 9.x release.
- `start_alyssa.bat:84-95` installs requirements whenever its content hash changes.
- README claims broad `Python 3.10+` support without a tested upper bound.
- `pip check` passes in the current venv, but that proves only internal
  consistency of one resolved environment, not reproducibility.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Runtime tests | `.\.venv\Scripts\python.exe -m unittest discover -v` | all pass |
| Consistency | `.\.venv\Scripts\python.exe -m pip check` | no broken requirements |
| Resolution | `.\.venv\Scripts\python.exe -m pip install --dry-run -r requirements.txt -c constraints-cpu.txt` | exits 0 in each supported Python version |

## Scope

**In scope**: requirements inputs, new reviewed CPU/GPU constraint files,
`start_alyssa.bat`, `build_alyssa.bat` if it must consume constraints, a version
matrix/verification workflow, and relevant README text.

**Out of scope**: splitting optional plugin dependencies (plan 021), migrating
to a package manager solely for fashion, or claiming untested Python versions.

## Git workflow

If git exists, branch `advisor/020-reproducible-installs`. Commit generated
constraints together with the documented command/source used to regenerate them.

## Steps

### Step 1: Define the support matrix

Select concrete Python minor versions based on available wheels for
faster-whisper/ctranslate2, sounddevice, pygame-ce, PySide6, OpenCV, and Google
clients. At minimum include the currently working version and one lower supported
minor. Record CPU as mandatory and GPU as an explicitly tested variant.

**Verify**: clean venv creation and dry-run resolution succeed for each declared
version; unsupported versions fail early with a clear launcher message.

### Step 2: Generate and review constraints

Keep direct requirements human-readable. Generate fully resolved, hashed or
exact-version constraints for CPU and GPU from trusted indexes. Review licenses,
wheel availability, and known advisories. Do not copy the current environment's
entire freeze if it contains unrelated packages.

**Verify**: two fresh resolutions per matrix cell produce the same installed
versions; `pip check` passes.

### Step 3: Make the launcher consume constraints

Include the chosen constraint file and Python minor in the dependency stamp.
Install with `-c <constraints>` so updated top-level inputs cannot silently
escape reviewed versions. Avoid unconditional pip upgrades unless its version is
also controlled/tested.

**Verify**: first launch installs, second skips, changing a constraint invalidates
the stamp, and CPU/GPU profile changes choose the correct constraint set.

### Step 4: Validate runtime and build

Run unit tests and smoke imports in every matrix cell. On at least one supported
Windows machine, launch console mode and build the PyInstaller executable.

**Verify**: tests, `pip check`, core imports, and build exit 0 in documented cells.

## Test plan

- Add a read-only resolver/CI matrix; cache downloads but never reuse site-packages
  between Python versions.
- Test stamp invalidation and helpful unsupported-version failures.
- Record but do not silently suppress optional GPU fallback failures.

## Done criteria

- [ ] README names exact supported Python minors and CPU/GPU status.
- [ ] Fresh installs resolve identically from reviewed constraints.
- [ ] Launcher/build scripts consume and hash constraints.
- [ ] Unit tests, imports, and `pip check` pass in every supported cell.
- [ ] At least one PyInstaller smoke build succeeds.

## STOP conditions

- Required packages have no compatible wheels for a proposed matrix cell.
- Constraint generation requires an untrusted package index.
- GPU combinations cannot be tested on appropriate hardware.

## Maintenance notes

Regenerate constraints deliberately, with changelog/advisory review. Dependabot-
style updates should change one coherent stack at a time and run the full matrix.

