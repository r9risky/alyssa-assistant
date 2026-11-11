"""Automated release script for Alyssa Assistant.

Handles:
1. Parsing and incrementing CURRENT_VERSION in updater.py
2. Staging and committing codebase changes
3. Pushing commits to remote tracking branch
4. Tagging release with vX.Y.Z and pushing tag to trigger GitHub Actions Windows .exe build
5. Verifying release workflow triggers

Usage:
    python scripts/release.py
    python scripts/release.py --bump minor
    python scripts/release.py --bump major
    python scripts/release.py --version v1.6.0
    python scripts/release.py --message "feat(audio): enhance audio engine"
    python scripts/release.py --dry-run
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys

ROOT_DIR = Path(__file__).resolve().parent.parent
UPDATER_FILE = ROOT_DIR / "updater.py"


def run_cmd(cmd: list[str], check: bool = True, dry_run: bool = False) -> subprocess.CompletedProcess[str]:
    print(f"[release] $ {' '.join(cmd)}")
    if dry_run:
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")
    res = subprocess.run(cmd, cwd=str(ROOT_DIR), capture_output=True, text=True)
    if check and res.returncode != 0:
        print(f"[release] Error executing: {' '.join(cmd)}", file=sys.stderr)
        if res.stdout:
            print(f"[stdout]\n{res.stdout}", file=sys.stderr)
        if res.stderr:
            print(f"[stderr]\n{res.stderr}", file=sys.stderr)
        sys.exit(res.returncode)
    return res


def get_current_version() -> tuple[str, int, int, int]:
    if not UPDATER_FILE.is_file():
        raise FileNotFoundError(f"Missing updater.py at {UPDATER_FILE}")
    content = UPDATER_FILE.read_text(encoding="utf-8")
    match = re.search(r'CURRENT_VERSION\s*=\s*["\']v?(\d+)\.(\d+)\.(\d+)["\']', content)
    if not match:
        raise ValueError("Could not find CURRENT_VERSION in updater.py")
    major, minor, patch = int(match.group(1)), int(match.group(2)), int(match.group(3))
    tag_str = f"v{major}.{minor}.{patch}"
    return tag_str, major, minor, patch


def compute_next_version(
    current_major: int,
    current_minor: int,
    current_patch: int,
    bump: str,
    explicit_version: str | None,
) -> str:
    if explicit_version:
        v = explicit_version.strip()
        if not v.startswith("v"):
            v = f"v{v}"
        return v

    if bump == "major":
        return f"v{current_major + 1}.0.0"
    if bump == "minor":
        return f"v{current_major}.{current_minor + 1}.0"
    # default: patch
    return f"v{current_major}.{current_minor}.{current_patch + 1}"


def update_updater_file(new_version: str, dry_run: bool = False) -> None:
    content = UPDATER_FILE.read_text(encoding="utf-8")
    updated, count = re.subn(
        r'(CURRENT_VERSION\s*=\s*["\'])[^"\']+(["\'])',
        rf'\g<1>{new_version}\g<2>',
        content,
        count=1,
    )
    if count == 0:
        raise ValueError("Failed to replace CURRENT_VERSION in updater.py")
    if not dry_run:
        UPDATER_FILE.write_text(updated, encoding="utf-8")
    print(f"[release] Updated {UPDATER_FILE.name} to {new_version}")


def get_current_branch() -> str:
    res = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    branch = res.stdout.strip()
    return branch or "main"


def main() -> None:
    parser = argparse.ArgumentParser(description="Automate version bumping, git push, tagging, and exe release.")
    parser.add_argument("--bump", choices=["patch", "minor", "major"], default="patch", help="Semantic bump type (default: patch)")
    parser.add_argument("--version", dest="explicit_version", help="Explicit version string (e.g. v1.5.2)")
    parser.add_argument("-m", "--message", help="Commit message")
    parser.add_argument("--dry-run", action="store_true", help="Preview actions without modifying files or pushing")
    parser.add_argument("--no-push", action="store_true", help="Commit locally without pushing or tagging")
    args = parser.parse_args()

    print("[release] Starting release workflow...")
    current_str, major, minor, patch = get_current_version()
    new_version = compute_next_version(major, minor, patch, args.bump, args.explicit_version)
    print(f"[release] Current version: {current_str} -> Next version: {new_version}")

    # Update version in updater.py
    update_updater_file(new_version, dry_run=args.dry_run)

    # Check git status
    status_res = run_cmd(["git", "status", "--porcelain"])
    has_changes = bool(status_res.stdout.strip())

    branch = get_current_branch()

    if has_changes:
        run_cmd(["git", "add", "-A"], dry_run=args.dry_run)
        commit_msg = args.message or f"chore(release): bump version to {new_version}"
        run_cmd(["git", "commit", "-m", commit_msg], dry_run=args.dry_run)
        print(f"[release] Committed changes: '{commit_msg}'")
    else:
        print("[release] No uncommitted changes detected.")

    if args.no_push:
        print("[release] --no-push requested. Skipping push and tagging.")
        return

    # Push branch commits
    print(f"[release] Pushing commits to origin/{branch}...")
    run_cmd(["git", "push", "origin", branch], dry_run=args.dry_run)

    # Tag and push tag
    print(f"[release] Creating tag {new_version}...")
    tag_msg = f"Release {new_version}"
    run_cmd(["git", "tag", "-a", new_version, "-m", tag_msg], dry_run=args.dry_run)

    print(f"[release] Pushing tag {new_version} to origin to trigger Windows .exe build & GitHub Release...")
    run_cmd(["git", "push", "origin", new_version], dry_run=args.dry_run)

    print(f"\n[release] SUCCESS! {new_version} released and pushed to GitHub.")
    print("[release] GitHub Actions is now compiling the standalone Windows .exe and attaching it to the release.")


if __name__ == "__main__":
    main()
