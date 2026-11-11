---
name: release-on-change
description: Enforce automated version bump in updater.py, git commit and push, tag creation, GitHub release, and Windows .exe build whenever changes are made.
---

# Release On Change Skill

## Purpose
Ensure that whenever changes (bugfixes, features, refactoring, improvements) are made to the Alyssa Assistant codebase, the version in `updater.py` is updated, changes are committed and pushed to GitHub, a tag is pushed, and a standalone Windows executable is built and released via GitHub Actions.

## Mandatory Execution Steps

Whenever any changes are made:

### Step 1: Bump Version in `updater.py`
Update `CURRENT_VERSION` in `updater.py` (e.g. from `v1.5.1` to `v1.5.2` for patch updates, or bump minor/major accordingly).

### Step 2: Commit All Changes
Stage all modified files and commit with a concise, descriptive commit message:
```bash
git add -A
git commit -m "chore(release): bump version to vX.Y.Z and <summary of changes>"
```

### Step 3: Push Commits to GitHub
Push the branch to origin:
```bash
git push origin <current-branch>
```

### Step 4: Tag the Release and Push Tag
Tag the commit with the exact version and push the tag:
```bash
git tag -a vX.Y.Z -m "Release vX.Y.Z"
git push origin vX.Y.Z
```

### Step 5: Windows Executable Build & GitHub Release
Pushing the `v*` tag triggers the GitHub Actions workflow `.github/workflows/build_exe.yml` on the `windows-latest` runner. This workflow:
- Compiles `Alyssa.exe` via PyInstaller (`scripts/build_exe.py`).
- Packages `dist/Alyssa-Windows-x64.zip`.
- Creates/updates the GitHub Release for `vX.Y.Z` and attaches the zip archive.

### Step 6: Automated Script Alternative
All of the above steps can be executed in one command via:
```bash
python scripts/release.py
```
Or with custom flags:
```bash
python scripts/release.py --bump minor
python scripts/release.py --version vX.Y.Z --message "feat: description"
```
