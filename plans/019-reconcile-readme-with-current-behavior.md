# Plan 019: Make the README describe the software that actually ships

> **Executor instructions**: This is documentation reconciliation. Verify every
> changed claim against live code; do not restore removed features to satisfy prose.
>
> **Drift check (run first)**: no git metadata is present. Confirm README sections
> around lines 202-307, 346-370, 442-463, and 643-668 still contain cited claims.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: plans 005, 006, 007, 010, 011, 012
- **Category**: docs
- **Planned at**: no git metadata available, 2026-08-13

## Why this matters

The README advertises active token compression even though the engine is a
pass-through stub, describes a removed voice-ID implementation, calls
`memory_db/` the current store in two places, and points to a missing plugin
example. These are setup and safety claims, not cosmetic prose. Documentation
must match the post-security-plan behavior exactly.

## Current state

- `README.md:266-307` documents active compression and nonexistent settings.
- `token_compression.py:1-27` says the engine was removed and reports inactive.
- `README.md:202-237` correctly explains `memory.json`, but `346-355` and
  `664-665` revert to `memory_db/`.
- `README.md:368-370` references absent `plugins/example_dice_and_jokes.py`.
- `README.md:442-463` documents removed speaker verification.
- `voice_id.py` and credential/startup behavior may change under plans 005–007;
  document the landed behavior, not this plan's assumptions.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Stale-term search | `rg -n "memory_db|VOICE_ID_ENABLED|voiceprint|COMPRESSION_MODE|example_dice_and_jokes" README.md` | only intentional migration/history references remain |
| Link/path check | `.\.venv\Scripts\python.exe -m unittest discover -v` | all tests pass |

## Scope

**In scope**: `README.md` only, plus `plans/README.md` status update.

**Out of scope**: production code, adding the missing example plugin instead of
correcting docs, or reintroducing compression/voice ID.

## Git workflow

If git exists, branch `advisor/019-readme-reconciliation`. Do not push.

## Steps

### Step 1: Inventory executable claims

Make a checklist of commands, filenames, settings, storage locations, safety
controls, packaging instructions, and feature statements in README. Verify each
against source after dependent plans land.

**Verify**: every named local path in README exists or is explicitly described as
runtime-generated/legacy.

### Step 2: Correct retired and contradictory sections

Remove the active compression tuning section or clearly state it is inactive;
use `memory.json` consistently while retaining a short legacy migration note;
replace the missing plugin-example reference with the actual in-app New Plugin
template and live plugin contract; describe the trusted confirmation behavior
from plan 007 and least-privilege startup from plan 006.

**Verify**: stale-term search has only justified historical matches.

### Step 3: Correct operational and security notes

Document protected secret storage, default log privacy, public-only webpage
summarization, the verified test command, optional dependency profiles if plan
021 has landed, and the true standalone bundle contents. Do not overpromise
features not verified on real hardware.

**Verify**: follow setup instructions from a clean copy or perform a line-by-line
command/path validation; all referenced settings exist.

## Test plan

- No new code tests required; run full suite as a drift guard.
- Check Markdown headings/fences and every relative path manually or with an
  available link checker that does not modify the tree.

## Done criteria

- [ ] Removed features are not described as active.
- [ ] Memory storage is consistently `memory.json` except explicit migration text.
- [ ] Plugin authoring points to an existing workflow.
- [ ] Security/startup/secret claims match landed code.
- [ ] All referenced commands, settings, and paths are valid.

## STOP conditions

- A dependent security plan has not landed and its final behavior is undecided.
- README appears generated from another source.
- A claim cannot be verified from code or a safe local command.

## Maintenance notes

Treat safety and data-location prose as release-blocking documentation. Future
feature removal should update README in the same change.

