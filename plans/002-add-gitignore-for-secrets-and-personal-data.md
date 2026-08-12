# Plan 002: Add a `.gitignore` so credentials and personal data can't be committed

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: This repository has no git history (verified
> during the audit — `git rev-parse --short HEAD` fails with "not a git
> repository"), so no commit-based drift check is possible. Instead, run
> `ls -la` (or `dir /a` on Windows) in the repo root and confirm no
> `.gitignore` already exists before proceeding — if one does, treat that
> as a STOP condition (see below) rather than overwriting it blindly.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: security
- **Planned at**: no git repository (repo has no `.git` directory) — N/A
- **Issue**: (none — not published)

## Why this matters

This project has no `.gitignore` at all, and it's the kind of project a
user is likely to put under version control — the README's own "Download
this project" instructions describe it as something you unzip and run
locally, and it's plausible someone forks/clones it from GitHub, tweaks
it, and pushes their own copy without thinking about what's sitting in the
folder next to the source. Three files in this repo hold data that should
never end up in a git history, let alone a public GitHub repo:

- `plugins/calendar_gmail.py` writes `credentials.json` (a Google Cloud
  OAuth client secret the user downloads themselves) and `token.json` (a
  live OAuth refresh token for that user's actual Google Calendar and
  Gmail) directly into the project directory.
- `memory.py` writes `memory.json` — an ever-growing list of personal
  facts the assistant has been told to remember about its user (see
  `memory.py:174-183`, `remember()`) — also directly into the project
  directory, and there's already a (currently empty) `memory.json` sitting
  in the repo root as shipped.

Once any of these are committed, deleting them from a future commit does
not remove them from git history — the OAuth token in particular would
need to be revoked/rotated, not just deleted. A `.gitignore` is a cheap,
purely-preventative fix: it doesn't change anything about the current
`memory.json` file, it just stops these three files (and standard
Python/build noise already sitting in the repo, like `__pycache__/`) from
being picked up if the user ever does run `git init` / `git add .` here.

## Current state

- Repo root (`alyssa_assistant/`) — confirmed via `find . -iname
  ".gitignore"` during the audit: no `.gitignore` exists anywhere in the
  tree.
- `plugins/calendar_gmail.py:55-56` — where the two OAuth files are named:

  ```python
  _CREDENTIALS_PATH = os.path.join(_BASE_DIR, "credentials.json")
  _TOKEN_PATH = os.path.join(_BASE_DIR, "token.json")
  ```

  `_BASE_DIR` in this file resolves to the project root (same directory as
  `main.py`), not a subfolder — confirm this is still true by checking how
  `_BASE_DIR` is defined near the top of `plugins/calendar_gmail.py`
  before writing the `.gitignore` patterns, in case it has changed.

- `memory.py:14-19` — where `memory.json` is named, and where its path is
  resolved relative to the source file itself, not the current working
  directory (important: this means it always lands in the repo root, even
  when frozen into a standalone `.exe`):

  ```python
  if getattr(sys, "frozen", False):
      _BASE_DIR = os.path.dirname(sys.executable)
  else:
      _BASE_DIR = os.path.dirname(__file__)

  MEMORY_FILE = os.path.join(_BASE_DIR, "memory.json")
  ```

- `alyssa_assistant/__pycache__/` already exists in the shipped repo
  (confirmed via `find` during recon — contains `.pyc` files for
  `actions`, `brain`, `config`, `memory`, `nameutil`) — this is exactly the
  kind of build noise a `.gitignore` should also cover, alongside the
  virtualenv (`README.md` describes `start_alyssa.bat` auto-creating one)
  and the PyInstaller output directory (`build_alyssa.bat` outputs to
  `dist\Alyssa.exe`, confirmed via `grep -n "dist" build_alyssa.bat`).
- Repo conventions: there is no existing `.gitignore` to match the style
  of, so use the standard flat, one-pattern-per-line format with `#`
  section comments, consistent with how `requirements.txt` in this repo
  already uses `#` comments to explain *why* each entry exists (see
  `requirements.txt`'s comments on `pygame-ce` and the memory-storage
  section) — carry that same "explain why, not just what" style into the
  new file's comments.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Confirm no `.gitignore` exists yet | `ls -la alyssa_assistant/.gitignore` (from repo root, one level above `alyssa_assistant/`) or `dir alyssa_assistant\.gitignore` on Windows | "No such file" / "File Not Found" |
| Confirm `_BASE_DIR` in calendar_gmail.py is still the project root | `grep -n "_BASE_DIR" plugins/calendar_gmail.py` (from inside `alyssa_assistant/`) | shows the definition; confirm it does not point at a subfolder |
| Sanity-check the new file's patterns against what's actually present | `git status --porcelain` (only if the operator has since run `git init` — otherwise skip; this repo has no `.git` as shipped) | `memory.json`, `credentials.json`, `token.json`, `__pycache__/` do not appear as tracked/staged |

## Scope

**In scope** (the only files you should modify or create):
- `alyssa_assistant/.gitignore` (new file)

**Out of scope** (do NOT touch, even though they look related):
- `memory.json` — do not delete, move, or modify the existing (empty)
  file shipped in the repo; a `.gitignore` entry only affects *future* git
  tracking, not the file's presence on disk, which the app needs.
- `README.md` — this plan does not add a note about the `.gitignore` to
  the README; if the operator wants that documented, treat it as a
  separate follow-up (see Maintenance notes).
- Running `git init` or any other git command yourself — this plan only
  adds the ignore file; whether/when the user turns this into an actual
  git repo is up to them.

## Git workflow

This repository has no `.git` directory (confirmed during recon). Do not
run `git init`, `git add`, or any commit — this plan only creates a plain
text file on disk. If the operator has since initialized git themselves,
adding and committing the new `.gitignore` is fine, but do not stage or
commit anything else while doing so.

## Steps

### Step 1: Create `.gitignore`

Create `alyssa_assistant/.gitignore` with the following content:

```gitignore
# --- Secrets & personal data — never commit these ---
# Google OAuth client secret (plugins/calendar_gmail.py) - user-provided,
# downloaded from Google Cloud Console.
credentials.json
# Google OAuth refresh token for the user's actual Calendar/Gmail
# (plugins/calendar_gmail.py) - written automatically after first sign-in.
token.json
# Persistent assistant memory (memory.py) - personal facts the user has
# asked Alyssa to remember, stored in plain text.
memory.json

# --- Python ---
__pycache__/
*.pyc
*.pyo

# --- Local virtual environment (created automatically by start_alyssa.bat) ---
.venv/

# --- PyInstaller build output (build_alyssa.bat) ---
build/
dist/
*.spec
```

**Verify**: `cat alyssa_assistant/.gitignore` (from repo root) → file
exists and contains all three secret/personal-data patterns
(`credentials.json`, `token.json`, `memory.json`).

### Step 2: Confirm the patterns match the actual file locations

Re-check `plugins/calendar_gmail.py`'s `_CREDENTIALS_PATH`/`_TOKEN_PATH`
and `memory.py`'s `MEMORY_FILE` (both quoted under "Current state" above)
resolve to the project root, i.e. the same directory the new
`.gitignore` sits in — not a subfolder. If any of them resolve elsewhere
(e.g. a `plugins/` subfolder), add an additional pattern for that path
instead of assuming the root-level pattern covers it.

**Verify**: `grep -n "_CREDENTIALS_PATH\s*=\|_TOKEN_PATH\s*=" plugins/calendar_gmail.py` and `grep -n "MEMORY_FILE\s*=" memory.py` (from inside `alyssa_assistant/`) → both resolve to `_BASE_DIR`-joined paths at the project root, matching the patterns added in Step 1.

## Test plan

No automated test applies to a `.gitignore` file in a repo with no git
history to test against. Manual verification only:

- If the operator has (or later sets up) an actual git repository here,
  they can confirm the ignore rules work with:
  `git check-ignore -v credentials.json token.json memory.json __pycache__/anything.pyc`
  → each line should print a match against this `.gitignore`.
- No new test file is added for this plan.

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `alyssa_assistant/.gitignore` exists
- [ ] `grep -c "credentials.json" alyssa_assistant/.gitignore` → `1`
- [ ] `grep -c "token.json" alyssa_assistant/.gitignore` → `1`
- [ ] `grep -c "memory.json" alyssa_assistant/.gitignore` → `1`
- [ ] `grep -c "__pycache__" alyssa_assistant/.gitignore` → `1`
- [ ] No files outside the in-scope list are modified
- [ ] `plans/README.md` status row for 002 updated to `DONE`

## STOP conditions

Stop and report back (do not improvise) if:

- A `.gitignore` already exists in `alyssa_assistant/` (contradicts the
  audit's finding) — read it first and merge the new patterns in rather
  than overwriting whatever's already there.
- `_BASE_DIR` in `plugins/calendar_gmail.py` or `memory.py` no longer
  resolves to the project root (e.g. it now points into a `plugins/`
  subfolder or a user-config directory) — the patterns in Step 1 assume a
  flat, project-root layout; report the actual location instead of adding
  a guessed pattern.
- You find evidence that `memory.json` or `credentials.json`/`token.json`
  have *already* been committed somewhere (e.g. a `.git` directory exists
  and `git log --all --full-history -- credentials.json` returns commits)
  — a `.gitignore` alone does not fix that; report it so the operator can
  rotate the OAuth credentials and consider a history rewrite, rather than
  treating this plan as sufficient remediation.

## Maintenance notes

- This `.gitignore` only prevents *future* commits of these files — it
  does nothing for a history that may already contain them (see the last
  STOP condition above).
- If a future plugin or feature writes another credential/token file next
  to the source (following the same pattern as `calendar_gmail.py`), it
  needs its own `.gitignore` entry — this isn't automatic. A reviewer
  adding a new OAuth-style integration should check this file.
- Consider, as a separate follow-up (out of scope here), adding a short
  note to `README.md`'s setup instructions warning users not to commit
  `credentials.json`/`token.json`/`memory.json` if they fork this project
  — the `.gitignore` protects `git add .`, but someone using `git add -f`
  or a different VCS entirely isn't covered.
