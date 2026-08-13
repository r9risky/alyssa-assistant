"""Download and install the latest Alyssa source release safely."""

import ast
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import zipfile

import requests


LATEST_RELEASE_URL = "https://api.github.com/repos/r9risky/alyssa-assistant/releases/latest"
APP_VERSION = "1.5.0"
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
                parts_lower = tuple(part.lower() for part in relative.parts)
                destination = install_root / relative
                if parts_lower[0] in PRESERVED_DIRS:
                    continue
                if relative.as_posix().lower() in PRESERVED_FILES:
                    continue
                if parts_lower[0] == "plugins" and destination.exists():
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


def install_latest(install_root: str | os.PathLike) -> tuple[bool, str]:
    """Install GitHub's latest published release. Return (updated, tag)."""
    install_root = Path(install_root)
    marker = install_root / ".alyssa-version"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "Alyssa-Updater"}

    try:
        response = requests.get(LATEST_RELEASE_URL, headers=headers, timeout=20)
        response.raise_for_status()
        release = response.json()
        tag = str(release["tag_name"]).strip()
        download_url = release["zipball_url"]
    except (requests.RequestException, KeyError, TypeError, ValueError) as error:
        raise RuntimeError(f"Couldn't check GitHub for updates: {error}") from error

    installed = (
        marker.read_text(encoding="utf-8").strip()
        if marker.is_file()
        else APP_VERSION
    )
    latest_key, installed_key = _version_key(tag), _version_key(installed)
    if tag == installed or (latest_key and installed_key and latest_key <= installed_key):
        return False, tag

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
        _apply_release(source_root, install_root)

    temporary_marker = marker.with_name(marker.name + ".update-tmp")
    temporary_marker.write_text(tag + "\n", encoding="utf-8")
    os.replace(temporary_marker, marker)
    return True, tag
