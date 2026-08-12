# Plan 005: Rotate the exposed credential and move secrets out of `config.py`

> **Executor instructions**: Follow every step and verification gate. Never print,
> copy, commit, or quote any credential value. If a STOP condition occurs, report
> it instead of improvising. Update this plan's row in `plans/README.md` when done.
>
> **Drift check (run first)**: this extracted tree has no `.git` metadata. Confirm
> that `config.py:24`, `overlay.py:4438-4478`, and the excerpts below still match.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: none
- **Category**: security
- **Planned at**: no git metadata available, 2026-08-13

## Why this matters

`config.py` currently contains a non-empty Gemini credential literal, and the
Settings UI writes all provider credentials back into that source file. Anyone
receiving an archive, backup, or future commit can receive live credentials.
This plan rotates the exposed credential and persists future secrets in a
per-user Windows-protected store while leaving ordinary settings editable.

## Current state

- `config.py:24` assigns `GEMINI_API_KEY` directly to a non-empty string literal.
  Do not reproduce that value in code, tests, logs, plans, or review comments.
- `overlay.py:4438-4461` adds changed keys and client secrets to the dictionary
  passed to `_patch_config_line()`.
- `overlay.py:4472-4478` rewrites that dictionary into `config.py`.
- Other credentials normally use environment lookups, for example
  `config.py:28`: `OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")`.
- Runtime tests use `unittest`; follow the patching style in
  `tests/test_memory.py` and avoid real credential-store or network calls.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Tests | `.\.venv\Scripts\python.exe -m unittest discover -v` | all tests pass |
| Dependency consistency | `.\.venv\Scripts\python.exe -m pip check` | `No broken requirements found.` |
| Syntax | `.\.venv\Scripts\python.exe -c "import ast,pathlib; [ast.parse(p.read_text(encoding='utf-8')) for p in pathlib.Path('.').rglob('*.py') if '.venv' not in p.parts and 'plans' not in p.parts]"` | exit 0 |

## Scope

**In scope**:
- `config.py`
- `overlay.py`
- `requirements.txt` only if the selected Windows secret backend requires it
- a new narrowly named secret-storage module, if needed
- `tests/test_secret_storage.py` (new)
- `README.md` credential setup text

**Out of scope**:
- changing provider names, models, API request formats, or OAuth token storage
- printing or migrating the exposed credential value
- modifying any file under `plans/` except the status index

## Git workflow

No repository history is available. If execution happens in a git checkout,
use branch `advisor/005-secret-storage`; do not push or open a PR unless asked.

## Steps

### Step 1: Revoke and rotate outside the repository

Revoke the Gemini credential identified at `config.py:24` in the provider's
credential console. Create a replacement only after code no longer persists it
to source. This is an operator action: never place either value in terminal
output or a test fixture.

**Verify**: provider console shows the old credential disabled. If the executor
cannot access the account, STOP and report that rotation remains mandatory.

### Step 2: Add a protected secret-storage boundary

Implement a small module with `get_secret(name)`, `set_secret(name, value)`, and
`delete_secret(name)`. Prefer Windows Credential Manager or DPAPI-backed
per-user storage. Environment variables must remain the first read source so
headless users retain the documented setup path. Empty GUI fields delete stored
values. Errors must be user-visible without including secret contents.

**Verify**: `.\.venv\Scripts\python.exe -m unittest tests.test_secret_storage -v`
passes mocked store/read/delete/failure cases.

### Step 3: Stop writing secrets into Python source

Change every credential in `config.py` to resolve from environment/protected
storage with an empty fallback. Remove credential keys from
`ConfigDialog._gather_assistant_updates()` so `_patch_config_line()` only sees
non-secret settings. In `_apply_assistant_live()`, persist changed secret fields
through the new boundary, then update the in-memory `config` attributes.

**Verify**: a repository search for assignments of the affected keys finds no
non-empty literal; mocked GUI-save tests prove `_patch_config_line()` receives
no key or client-secret names.

### Step 4: Document the supported secret paths

Update README setup and Settings text: environment variables are supported;
values entered in the GUI are stored in protected per-user storage and never in
`config.py`. Include removal/reset instructions without naming any real value.

**Verify**: `rg -n "API_KEY|CLIENT_SECRET" config.py` shows only safe lookup
expressions or empty defaults; the full test command passes.

## Test plan

- Mock the secret backend; never call the real credential store in tests.
- Cover environment precedence, protected-store fallback, missing secret,
  setting, deletion, backend failure, and GUI persistence exclusion.
- Model test structure after `tests/test_memory.py` (`unittest`, isolated state).

## Done criteria

- [ ] The credential previously at `config.py:24` is revoked.
- [ ] No non-empty provider credential literal remains in tracked source.
- [ ] Saving Settings cannot write credentials into `config.py`.
- [ ] Secret tests and all existing tests pass.
- [ ] `pip check` and syntax verification pass.
- [ ] Only in-scope files changed; the plan index is updated.

## STOP conditions

- Rotation cannot be performed by an authorized account owner.
- The selected backend requires machine-wide installation or administrator access.
- Existing source differs from the cited persistence flow.
- Migration would require reading or logging the current credential value.

## Maintenance notes

Future providers must register secrets through the same boundary. Reviewers
should reject any new `_patch_config_line()` entry for tokens, passwords,
client secrets, or API keys.

