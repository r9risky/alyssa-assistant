"""Download and install the latest Alyssa source release safely."""

import ast
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile

import requests


LATEST_RELEASE_URL = "https://api.github.com/repos/r9risky/alyssa-assistant/releases/latest"
CURRENT_VERSION = "v1.0.5"
MAX_DOWNLOAD_BYTES = 250 * 1024 * 1024
PRESERVED_FILES = {
    "color_themes.json",
    "credentials.json",
    "memory.json",
    "overlay_config.json",
    "reminders.json",
    "token.json",
}
PRESERVED_DIRS = {".git", ".venv", "__pycache__", "build", "dist"}
MANIFEST_FILE = ".alyssa-manifest.json"


class LocalChangesError(RuntimeError):
    """Raised before an update could overwrite locally edited app code."""


def _is_managed(relative: Path, destination: Path) -> bool:
    parts_lower = tuple(part.lower() for part in relative.parts)
    return not (
        parts_lower[0] in PRESERVED_DIRS
        or relative.as_posix().lower() in PRESERVED_FILES
        or relative.as_posix().lower() == "config.py"
        or (parts_lower[0] == "plugins" and destination.exists())
    )


def _managed_paths(source_root: Path, install_root: Path) -> set[str]:
    return {
        source.relative_to(source_root).as_posix()
        for source in source_root.rglob("*")
        if source.is_file()
        and _is_managed(
            source.relative_to(source_root),
            install_root / source.relative_to(source_root),
        )
    }


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _local_changes(source_root: Path, install_root: Path) -> list[str]:
    """Find edited files that the incoming release would replace."""
    managed = _managed_paths(source_root, install_root)
    changed = set()
    manifest_path = install_root / MANIFEST_FILE
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            changed.update(
                relative for relative in managed
                if relative in manifest
                and (
                    not (install_root / relative).is_file()
                    or _file_hash(install_root / relative) != manifest[relative]
                )
            )
        except (OSError, ValueError, TypeError):
            pass

    if not (install_root / ".git").is_dir():
        return sorted(changed)
    try:
        commands = (
            ["git", "diff", "--name-only", "-z", "HEAD"],
            ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        )
        for command in commands:
            result = subprocess.run(
                command, cwd=install_root, capture_output=True, check=True
            )
            changed.update(
                name.decode("utf-8", errors="replace").replace("\\", "/")
                for name in result.stdout.split(b"\0") if name
            )
        return sorted(managed & changed)
    except (OSError, subprocess.SubprocessError):
        return sorted(changed)


def _write_manifest(source_root: Path, install_root: Path) -> None:
    manifest = {
        relative: _file_hash(install_root / relative)
        for relative in sorted(_managed_paths(source_root, install_root))
        if (install_root / relative).is_file()
    }
    path = install_root / MANIFEST_FILE
    temporary = path.with_name(path.name + ".update-tmp")
    temporary.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _version_key(value: str):
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", value.strip(), re.IGNORECASE)
    return tuple(map(int, match.groups())) if match else None


def _assignment_values(source: str) -> dict[str, str]:
    """Return source text for simple top-level assignment values."""
    tree = ast.parse(source)
    values = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        elif isinstance(node, ast.AnnAssign):
            target = node.target
        else:
            continue
        if isinstance(target, ast.Name) and node.value is not None:
            values[target.id] = ast.get_source_segment(source, node.value)
    return values


def merge_config_settings(new_source: str, old_source: str) -> str:
    """Keep installed config values while retaining the new file structure."""
    old_values = _assignment_values(old_source)
    tree = ast.parse(new_source)
    lines = new_source.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))

    replacements = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        elif isinstance(node, ast.AnnAssign):
            target = node.target
        else:
            continue
        if not isinstance(target, ast.Name) or target.id not in old_values or node.value is None:
            continue
        start = offsets[node.value.lineno - 1] + node.value.col_offset
        end = offsets[node.value.end_lineno - 1] + node.value.end_col_offset
        replacements.append((start, end, old_values[target.id]))

    for start, end, value in reversed(replacements):
        new_source = new_source[:start] + value + new_source[end:]
    return new_source


def _safe_extract(archive_path: Path, destination: Path) -> Path:
    """Extract a GitHub source ZIP, rejecting traversal and symlinks."""
    roots = set()
    with zipfile.ZipFile(archive_path) as archive:
        for info in archive.infolist():
            path = PurePosixPath(info.filename)
            if (
                path.is_absolute()
                or ".." in path.parts
                or not path.parts
                or "\\" in info.filename
            ):
                raise RuntimeError("The release archive contains an unsafe path.")
            if stat.S_ISLNK(info.external_attr >> 16):
                raise RuntimeError("The release archive contains an unsupported link.")
            roots.add(path.parts[0])
            if info.is_dir():
                continue
            target = destination.joinpath(*path.parts)
            if os.path.commonpath(
                (destination.resolve(), target.resolve())
            ) != str(destination.resolve()):
                raise RuntimeError("The release archive contains an unsafe path.")
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, open(target, "wb") as output:
                shutil.copyfileobj(source, output)

    if len(roots) != 1:
        raise RuntimeError("The release archive has an unexpected layout.")
    root = destination / roots.pop()
    if not all((root / name).is_file() for name in ("main.py", "overlay.py", "config.py")):
        raise RuntimeError("The release archive is missing required Alyssa files.")
    return root


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".update-tmp")
    shutil.copy2(source, temporary)
    os.replace(temporary, destination)


def _apply_release(source_root: Path, install_root: Path) -> None:
    """Apply release files and restore the previous install on failure."""
    install_root = install_root.resolve()
    with tempfile.TemporaryDirectory(prefix="alyssa-backup-") as backup_name:
        backup_root = Path(backup_name)
        changed = []
        try:
            for source in sorted(source_root.rglob("*")):
                if not source.is_file():
                    continue
                relative = source.relative_to(source_root)
                destination = install_root / relative
                if not _is_managed(relative, destination) and relative.as_posix().lower() != "config.py":
                    continue

                backup = backup_root / relative
                existed = destination.exists()
                if existed:
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(destination, backup)

                if relative.as_posix().lower() == "config.py" and existed:
                    merged = merge_config_settings(
                        source.read_text(encoding="utf-8"),
                        destination.read_text(encoding="utf-8"),
                    )
                    temporary_source = backup_root / "merged-config.py"
                    temporary_source.write_text(merged, encoding="utf-8")
                    _atomic_copy(temporary_source, destination)
                else:
                    _atomic_copy(source, destination)
                changed.append((destination, backup if existed else None))
        except Exception:
            for destination, backup in reversed(changed):
                if backup is None:
                    destination.unlink(missing_ok=True)
                else:
                    _atomic_copy(backup, destination)
            raise


def current_version(install_root: str | os.PathLike) -> str:
    marker = Path(install_root) / ".alyssa-version"
    return marker.read_text(encoding="utf-8").strip() if marker.is_file() else CURRENT_VERSION


def check_latest(install_root: str | os.PathLike) -> dict:
    """Return GitHub's latest release metadata and whether it is newer."""
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "Alyssa-Updater"}
    try:
        response = requests.get(LATEST_RELEASE_URL, headers=headers, timeout=20)
        response.raise_for_status()
        release = response.json()
        tag = str(release["tag_name"]).strip()
        download_url = str(release["zipball_url"])
    except (requests.RequestException, KeyError, TypeError, ValueError) as error:
        raise RuntimeError(f"Couldn't check GitHub for updates: {error}") from error

    installed = current_version(install_root)
    latest_key, installed_key = _version_key(tag), _version_key(installed)
    if latest_key is None or installed_key is None:
        raise RuntimeError(f"Couldn't compare release versions: installed {installed}, latest {tag}.")
    return {
        "current_version": installed,
        "latest_version": tag,
        "update_available": installed_key < latest_key,
        "notes": str(release.get("body") or "No release notes were provided.").strip(),
        "download_url": download_url,
    }


def install_release(install_root: str | os.PathLike, release: dict) -> str:
    """Download and transactionally install a release returned by check_latest."""
    install_root = Path(install_root)
    marker = install_root / ".alyssa-version"
    tag = release["latest_version"]
    download_url = release["download_url"]
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "Alyssa-Updater"}

    with tempfile.TemporaryDirectory(prefix="alyssa-update-") as temporary_name:
        temporary = Path(temporary_name)
        archive_path = temporary / "release.zip"
        try:
            response = requests.get(download_url, headers=headers, stream=True, timeout=60)
            response.raise_for_status()
            downloaded = 0
            with open(archive_path, "wb") as output:
                for chunk in response.iter_content(1024 * 1024):
                    downloaded += len(chunk)
                    if downloaded > MAX_DOWNLOAD_BYTES:
                        raise RuntimeError("The release download is unexpectedly large.")
                    output.write(chunk)
        except requests.RequestException as error:
            raise RuntimeError(f"Couldn't download the latest release: {error}") from error

        source_root = _safe_extract(archive_path, temporary / "source")
        changed = _local_changes(source_root, install_root)
        if changed:
            shown = ", ".join(changed[:8])
            if len(changed) > 8:
                shown += f", and {len(changed) - 8} more"
            raise LocalChangesError(
                "Update stopped to protect your work. Alyssa found locally edited "
                f"application files that the update would replace: {shown}. "
                "Your settings and personal data were not changed. Back up or commit "
                "those code changes, then update them manually."
            )
        _apply_release(source_root, install_root)
        _write_manifest(source_root, install_root)

    temporary_marker = marker.with_name(marker.name + ".update-tmp")
    temporary_marker.write_text(tag + "\n", encoding="utf-8")
    os.replace(temporary_marker, marker)
    return tag


def install_latest(install_root: str | os.PathLike) -> tuple[bool, str]:
    """Compatibility helper: check for and install GitHub's latest release."""
    release = check_latest(install_root)
    if not release["update_available"]:
        return False, release["latest_version"]
    return True, install_release(install_root, release)
