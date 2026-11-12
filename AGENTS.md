# Alyssa Assistant — AI Agent Instructions & Rules

## Mandatory Release & Continuous Deployment Rule

> **CRITICAL REQUIREMENT**: Whenever you modify or add code, fix bugs, refactor, or make any changes to this repository, you **MUST ALWAYS** follow this release protocol before completing your turn:

### 1. Update Version in `updater.py`
- Open `updater.py` and inspect `CURRENT_VERSION` (e.g., `CURRENT_VERSION = "v1.5.1"`).
- Increment the version following Semantic Versioning (`vMAJOR.MINOR.PATCH`):
  - Increment **PATCH** (e.g., `v1.5.1` -> `v1.5.2`) for bugfixes, small features, or refactors.
  - Increment **MINOR** (e.g., `v1.5.1` -> `v1.6.0`) for substantial features or GUI/audio updates.
  - Increment **MAJOR** for breaking architecture changes.
- Save `updater.py` with the new version string.

### 2. Commit All Changes
- Stage all changed files:
  ```bash
  git add -A
  ```
- Commit using a conventional commit message:
  ```bash
  git commit -m "chore(release): bump version to vX.Y.Z and <summary of changes>"
  ```

### 3. Push to GitHub
- Push the local commits to the remote repository:
  ```bash
  git push origin <current-branch>
  ```

### 4. Tag the Release & Push Tag
- Create an annotated git tag corresponding to the new version:
  ```bash
  git tag -a vX.Y.Z -m "Release vX.Y.Z"
  ```
- Push the tag to GitHub:
  ```bash
  git push origin vX.Y.Z
  ```
- If the tag push is refused (cloud sessions can't push tags), merge the version bump to `main` and give the owner a prefilled link instead, so they can publish the release in one click:
  `https://github.com/r9risky/alyssa-assistant/releases/new?tag=vX.Y.Z&target=main&title=vX.Y.Z`
  Publishing it from that page creates the tag, which starts the same build below.

### 5. Build Standalone Windows Executable & Publish Release
- Pushing the tag `vX.Y.Z` automatically triggers the GitHub Actions workflow `.github/workflows/build_exe.yml` on `windows-latest`.
- The workflow:
  - Compiles `Alyssa.exe` using `scripts/build_exe.py` (PyInstaller).
  - Bundles config, plugins, assets, and documentation into `dist/Alyssa-Windows-x64.zip`.
  - Creates the GitHub Release for `vX.Y.Z` with auto-generated release notes and attaches `Alyssa-Windows-x64.zip`.

### 6. Automated One-Command Shortcut
Instead of executing these steps manually, you can run the helper script:
```bash
python scripts/release.py
```
Options supported:
- `python scripts/release.py` (defaults to patch bump)
- `python scripts/release.py --bump minor` (bumps minor version)
- `python scripts/release.py --version v1.6.0` (explicit version)
- `python scripts/release.py --message "feat: description"` (custom commit message)

---

## Code Quality & Verification
- **One feature or fix per pull request.** Don't bundle unrelated changes, and don't port a batch of features from another project in one go. Small PRs are what keep a bad change from breaking everything at once.
- Run `python -m pytest -q` and `python scripts/smoke_test.py` before committing. CI runs both on every push and pull request (`.github/workflows/tests.yml`), and a release build won't start if the tests fail.
- A new feature or bug fix comes with a test that would fail without it.
- Take ideas from other projects, not their code or art. Some (like Capri) are under licences that forbid reuse without credit or commercial use.
- Ensure Python code compiles cleanly without syntax errors before committing.
- Respect preserved files listed in `updater.py` (`color_themes.json`, `credentials.json`, `memory.json`, etc.).
- Never commit sensitive tokens or credentials (`.env`, `token.json`, API keys).
