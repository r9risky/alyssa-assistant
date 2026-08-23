"""Download and install the latest Alyssa source release safely."""

from __future__ import annotations

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
from urllib.parse import urlsplit

import requests

LATEST_RELEASE_URL = "https://api.github.com/repos/r9risky/alyssa-assistant/releases/latest"
CURRENT_VERSION = "v1.1.10"
MAX_DOWNLOAD_BYTES = 250 * 1024 * 1024
MAX_EXTRACTED_BYTES = 500 * 1024 * 1024
MAX_ARCHIVE_FILES = 10_000
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
_LEGACY_V1_0_8_PATHS = frozenset(
    {
        ".gitignore",
        "README.md",
        "actions.py",
        "assets/nottalk.png",
        "assets/talkopen.png",
        "brain.py",
        "install_startup.bat",
        "main.py",
        "memory.py",
        "nameutil.py",
        "overlay.py",
        "plugin_loader.py",
        "plugins/calculator_converter.py",
        "plugins/calendar_gmail.py",
        "plugins/caveman_mode.py",
        "plugins/location.py",
        "plugins/news_digest.py",
        "plugins/ponytail_mode.py",
        "plugins/process_manager.py",
        "plugins/reminders.py",
        "plugins/security_camera.py",
        "plugins/system_watch.py",
        "plugins/timers.py",
        "plugins/weather.py",
        "plugins/web_search.py",
        "plugins/web_summarizer.py",
        "pytest.ini",
        "recorder.py",
        "requirements-gpu.txt",
        "requirements.txt",
        "start_alyssa.bat",
        "tests/__init__.py",
        "tests/test_brain_message_conversion.py",
        "tests/test_memory.py",
        "tests/test_nameutil.py",
        "tests/test_runtime_fixes.py",
        "tests/test_safety_fixes.py",
        "tests/test_settings_gui.py",
        "tests/test_tool_argument_sanitization.py",
        "tests/test_updater.py",
        "tests/test_web_summarizer_bs4_available.py",
        "transcribe.py",
        "uninstall_startup.bat",
        "updater.py",
        "voice.py",
    }
)
_LEGACY_V1_1_0_PATHS = (
    _LEGACY_V1_0_8_PATHS
    - {
        "actions.py",
        "brain.py",
        "install_startup.bat",
        "overlay.py",
        "start_alyssa.bat",
        "tests/__init__.py",
        "uninstall_startup.bat",
    }
    | {
        "LATENCY_AUDIT.md",
        "actions/__init__.py",
        "actions/apps_and_files.py",
        "actions/clipboard_and_screen.py",
        "actions/confirmation.py",
        "actions/input_sim.py",
        "actions/media.py",
        "actions/music.py",
        "actions/system.py",
        "actions/windows.py",
        "brain/__init__.py",
        "brain/common.py",
        "brain/dialogue.py",
        "brain/providers/__init__.py",
        "brain/providers/anthropic.py",
        "brain/providers/gemini.py",
        "brain/providers/ollama.py",
        "brain/providers/openai.py",
        "brain/text_utils.py",
        "brain/vision.py",
        "overlay/__init__.py",
        "overlay/app_shell.py",
        "overlay/credential_checks.py",
        "overlay/rendering.py",
        "overlay/settings_dialog.py",
        "overlay/theming.py",
        "overlay/widgets.py",
        "requirements-dev.txt",
        "scripts/install_startup.bat",
        "scripts/start_alyssa.bat",
        "scripts/uninstall_startup.bat",
        "telemetry.py",
        "tests/test_latency_pipeline.py",
    }
)
_LEGACY_V1_1_1_PATHS = (
    _LEGACY_V1_1_0_PATHS
    - {"LATENCY_AUDIT.md"}
    | {
        "actions/bridges.py",
        "actions/desktop.py",
        "brain/tool_catalog.py",
        "brain/tool_registry.py",
        "credential_store.py",
        "tests/test_architecture_boundaries.py",
    }
)
_LEGACY_V1_1_4_PATHS = _LEGACY_V1_1_1_PATHS | {
    "alyssaai.zip",
    "overlay/companion/__init__.py",
    "overlay/companion/interaction_mixin.py",
    "overlay/companion/rendering_mixin.py",
    "overlay/companion/settings_mixin.py",
    "overlay/companion/talk_state_mixin.py",
    "overlay/companion/window_mixin.py",
    "overlay/settings_tabs/__init__.py",
    "overlay/settings_tabs/assistant_tab.py",
    "overlay/settings_tabs/audio_tab.py",
    "overlay/settings_tabs/companion_tab.py",
    "overlay/settings_tabs/engine_tab.py",
    "overlay/settings_tabs/plugins_tab.py",
    "overlay/settings_tabs/updates_tab.py",
    "scripts/diagnose_startup.ps1",
    "startup_logging.py",
    "tests/test_startup_contract.py",
    "voice_playback.py",
    "voice_synthesis.py",
}
_LEGACY_V1_1_10_PATHS = (
    _LEGACY_V1_1_4_PATHS
    - {"alyssaai.zip"}
    | {
        "tests/test_llm_routing.py",
        "tests/test_system_watch.py",
        "tests/test_tool_chaining.py",
        "tests/test_tool_filtering.py",
    }
)
LEGACY_MANAGED_PATHS = {
    "v1.0.8": _LEGACY_V1_0_8_PATHS,
    "v1.1.0": _LEGACY_V1_1_0_PATHS,
    "v1.1.1": _LEGACY_V1_1_1_PATHS,
    "v1.1.2": _LEGACY_V1_1_1_PATHS,
    "v1.1.4": _LEGACY_V1_1_4_PATHS,
    "v1.1.10": _LEGACY_V1_1_10_PATHS,
}


def _require_https_url(value: str, label: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError as error:
        raise RuntimeError(f"The {label} URL is invalid.") from error
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        raise RuntimeError(f"The {label} URL must use HTTPS.")
    return value


def _manifest_paths(install_root: Path) -> set[str]:
    try:
        manifest = json.loads((install_root / MANIFEST_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return set()
    if not isinstance(manifest, dict):
        return set()

    paths = set()
    for name in manifest:
        if not isinstance(name, str) or "\\" in name:
            continue
        path = PurePosixPath(name)
        if path.is_absolute() or not path.parts or ".." in path.parts:
            continue
        paths.add(path.as_posix())
    return paths


def _previously_managed_paths(install_root: Path) -> set[str]:
    manifest = install_root / MANIFEST_FILE
    if manifest.is_file():
        return _manifest_paths(install_root)
    if not (install_root / ".git").is_dir():
        if not (install_root / ".alyssa-version").is_file() and not all(
            (install_root / name).is_file() for name in ("main.py", "updater.py")
        ):
            return set()
        return set(LEGACY_MANAGED_PATHS.get(current_version(install_root), ()))
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=install_root,
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return set()

    paths = set()
    for raw_name in result.stdout.split(b"\0"):
        name = raw_name.decode("utf-8", errors="replace")
        path = PurePosixPath(name)
        if name and "\\" not in name and not path.is_absolute() and ".." not in path.parts:
            paths.add(path.as_posix())
    return paths


def _is_managed(
    relative: Path, destination: Path, previously_managed: set[str] = frozenset()
) -> bool:
    parts_lower = tuple(part.lower() for part in relative.parts)
    relative_lower = relative.as_posix().lower()
    return not (
        parts_lower[0] in PRESERVED_DIRS
        or relative_lower in PRESERVED_FILES
        or relative_lower == "config.py"
        or (
            parts_lower[0] == "plugins"
            and destination.exists()
            and relative_lower not in previously_managed
        )
    )


def _managed_paths(source_root: Path, install_root: Path) -> set[str]:
    previously_managed = {
        relative.lower() for relative in _previously_managed_paths(install_root)
    }
    return {
        source.relative_to(source_root).as_posix()
        for source in source_root.rglob("*")
        if source.is_file()
        and _is_managed(
            source.relative_to(source_root),
            install_root / source.relative_to(source_root),
            previously_managed,
        )
    }


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_manifest(
    source_root: Path, install_root: Path, managed: set[str] | None = None
) -> None:
    managed = managed if managed is not None else _managed_paths(source_root, install_root)
    manifest = {
        relative: _file_hash(install_root / relative)
        for relative in sorted(managed)
        if (install_root / relative).is_file()
    }
    path = install_root / MANIFEST_FILE
    temporary = path.with_name(path.name + ".update-tmp")
    try:
        temporary.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _version_key(value: str) -> tuple[int, int, int] | None:
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
    """Extract a GitHub source ZIP with traversal and resource limits."""
    roots = set()
    destination_root = destination.resolve()
    with zipfile.ZipFile(archive_path) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_ARCHIVE_FILES:
            raise RuntimeError("The release archive contains too many files.")
        if sum(info.file_size for info in entries) > MAX_EXTRACTED_BYTES:
            raise RuntimeError("The release archive expanded size is unexpectedly large.")

        extracted_files = set()
        for info in entries:
            path = PurePosixPath(info.filename)
            if (
                path.is_absolute()
                or ".." in path.parts
                or not path.parts
                or "\\" in info.filename
                or any(":" in part for part in path.parts)
            ):
                raise RuntimeError("The release archive contains an unsafe path.")
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise RuntimeError("The release archive contains an unsupported link.")
            if info.flag_bits & 0x1:
                raise RuntimeError("The release archive contains an encrypted file.")

            roots.add(path.parts[0])
            if info.is_dir():
                continue
            normalized = path.as_posix().casefold()
            if normalized in extracted_files:
                raise RuntimeError("The release archive contains duplicate paths.")
            extracted_files.add(normalized)

            target = destination.joinpath(*path.parts)
            if not target.resolve().is_relative_to(destination_root):
                raise RuntimeError("The release archive contains an unsafe path.")
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)

    if len(roots) != 1:
        raise RuntimeError("The release archive has an unexpected layout.")
    root = destination / roots.pop()
    required_files = ("main.py", "config.py")
    has_overlay = (root / "overlay.py").is_file() or (root / "overlay" / "__init__.py").is_file()
    if not all((root / name).is_file() for name in required_files) or not has_overlay:
        raise RuntimeError("The release archive is missing required Alyssa files.")
    return root

def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".update-tmp")
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _apply_release(
    source_root: Path, install_root: Path, managed: set[str] | None = None
) -> None:
    """Apply release files and restore the previous install on failure."""
    install_root = install_root.resolve()
    managed = managed if managed is not None else _managed_paths(source_root, install_root)
    previous = _previously_managed_paths(install_root)
    previous_lower = {relative.lower() for relative in previous}
    managed_lower = {relative.lower() for relative in managed}
    obsolete = {
        name
        for name in previous
        if name.lower() not in managed_lower
        and _is_managed(
            Path(*PurePosixPath(name).parts),
            install_root / Path(*PurePosixPath(name).parts),
            previous_lower,
        )
    }
    with tempfile.TemporaryDirectory(prefix="alyssa-backup-") as backup_name:
        backup_root = Path(backup_name)
        changed = []
        try:
            for source in sorted(source_root.rglob("*")):
                if not source.is_file():
                    continue
                relative = source.relative_to(source_root)
                destination = install_root / relative
                if relative.as_posix() not in managed and relative.as_posix().lower() != "config.py":
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

            for name in sorted(obsolete):
                relative = Path(*PurePosixPath(name).parts)
                destination = install_root / relative
                if not destination.is_file():
                    continue
                if os.path.commonpath((install_root, destination.resolve())) != str(install_root):
                    continue
                backup = backup_root / relative
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(destination, backup)
                changed.append((destination, backup))
                destination.unlink()
        except Exception:
            for destination, backup in reversed(changed):
                if backup is None:
                    destination.unlink(missing_ok=True)
                else:
                    _atomic_copy(backup, destination)
            raise


def current_version(install_root: str | os.PathLike[str]) -> str:
    marker = Path(install_root) / ".alyssa-version"
    return marker.read_text(encoding="utf-8").strip() if marker.is_file() else CURRENT_VERSION


def check_latest(install_root: str | os.PathLike[str]) -> dict[str, object]:
    """Return GitHub's latest release metadata and whether it is newer."""
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "Alyssa-Updater"}
    response = None
    try:
        response = requests.get(LATEST_RELEASE_URL, headers=headers, timeout=20)
        response.raise_for_status()
        release = response.json()
        tag = str(release["tag_name"]).strip()
        download_url = _require_https_url(
            str(release["zipball_url"]), "release download"
        )
    except (requests.RequestException, KeyError, TypeError, ValueError) as error:
        raise RuntimeError(f"Couldn't check GitHub for updates: {error}") from error
    finally:
        if response is not None:
            response.close()

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


def install_release(
    install_root: str | os.PathLike[str], release: dict[str, object]
) -> str:
    """Download and transactionally install a release returned by check_latest."""
    install_root = Path(install_root)
    marker = install_root / ".alyssa-version"
    try:
        tag = str(release["latest_version"]).strip()
        download_url = _require_https_url(
            str(release["download_url"]), "release download"
        )
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError("The release metadata is incomplete.") from error
    if _version_key(tag) is None:
        raise RuntimeError(f"The release version is invalid: {tag}.")

    headers = {"Accept": "application/vnd.github+json", "User-Agent": "Alyssa-Updater"}
    with tempfile.TemporaryDirectory(prefix="alyssa-update-") as temporary_name:
        temporary = Path(temporary_name)
        archive_path = temporary / "release.zip"
        response = None
        try:
            response = requests.get(
                download_url, headers=headers, stream=True, timeout=(10, 60)
            )
            response.raise_for_status()
            content_length = response.headers.get("Content-Length")
            if isinstance(content_length, str) and content_length.isdigit():
                if int(content_length) > MAX_DOWNLOAD_BYTES:
                    raise RuntimeError("The release download is unexpectedly large.")

            downloaded = 0
            with archive_path.open("wb") as output:
                for chunk in response.iter_content(1024 * 1024):
                    if not chunk:
                        continue
                    downloaded += len(chunk)
                    if downloaded > MAX_DOWNLOAD_BYTES:
                        raise RuntimeError("The release download is unexpectedly large.")
                    output.write(chunk)
        except requests.RequestException as error:
            raise RuntimeError(f"Couldn't download the latest release: {error}") from error
        finally:
            if response is not None:
                response.close()

        source_root = _safe_extract(archive_path, temporary / "source")
        managed = _managed_paths(source_root, install_root)
        _apply_release(source_root, install_root, managed)
        _write_manifest(source_root, install_root, managed)

    temporary_marker = marker.with_name(marker.name + ".update-tmp")
    try:
        temporary_marker.write_text(tag + "\n", encoding="utf-8")
        os.replace(temporary_marker, marker)
    finally:
        temporary_marker.unlink(missing_ok=True)
    return tag

def install_latest(install_root: str | os.PathLike[str]) -> tuple[bool, str]:
    """Compatibility helper: check for and install GitHub's latest release."""
    release = check_latest(install_root)
    if not release["update_available"]:
        return False, release["latest_version"]
    return True, install_release(install_root, release)
