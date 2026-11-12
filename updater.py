"""Download/install releases: bounded extraction, rollback, SHA-256 from
GitHub's release API (asset digest or checksum asset). HTTPS match only —
not a maintainer signature.
"""

from __future__ import annotations

import ast
import builtins
import hashlib
import html
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from urllib.parse import unquote, urlsplit

import requests

RELEASE_OWNER = "r9risky"
RELEASE_REPOSITORY = "alyssa-assistant"
LATEST_RELEASE_URL = (
    f"https://api.github.com/repos/{RELEASE_OWNER}/{RELEASE_REPOSITORY}/releases/latest"
)
CURRENT_VERSION = "v1.7.0"
TRANSACTION_DIR = ".alyssa-update-transaction"
COMMITTED_TRANSACTION_DIR = ".alyssa-update-committed"
MAX_CHECKSUM_BYTES = 1024 * 1024
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
SECRET_CONFIG_FIELDS = frozenset(
    {
        "GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
        "CUSTOM_API_KEY", "ELEVENLABS_API_KEY", "SPOTIFY_CLIENT_ID",
        "SPOTIFY_CLIENT_SECRET", "YOUTUBE_API_KEY",
    }
)


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


def _render_release_notes_inline(text: str) -> str:
    """Render supported inline markdown after escaping the source text."""
    rendered = html.escape(text, quote=True)
    replacements: list[tuple[str, str]] = []

    def protect(replacement: str) -> str:
        token = f"\x00{len(replacements)}\x00"
        replacements.append((token, replacement))
        return token

    rendered = re.sub(
        r"`([^`\n]+)`",
        lambda match: protect(f"<code>{match.group(1)}</code>"),
        rendered,
    )
    rendered = re.sub(
        r"\[([^\]\n]+)\]\(([^)\n]+)\)",
        lambda match: protect(
            f'<a href="{match.group(2)}">{match.group(1)}</a>'
        ),
        rendered,
    )
    rendered = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", rendered)
    rendered = re.sub(r"__(.+?)__", r"<b>\1</b>", rendered)
    rendered = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"<i>\1</i>", rendered)
    rendered = re.sub(
        r"(?<![\w_])_([^_\n]+?)_(?![\w_])", r"<i>\1</i>", rendered
    )

    for token, replacement in reversed(replacements):
        rendered = rendered.replace(token, replacement)
    return rendered


def render_release_notes_html(markdown_text: str) -> str:
    """Convert common release-note markdown into Qt-compatible rich text HTML."""
    blocks: list[str] = []
    paragraph: list[str] = []
    list_kind: str | None = None
    list_items: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            blocks.append(
                f"<p>{_render_release_notes_inline(' '.join(paragraph))}</p>"
            )
            paragraph.clear()

    def flush_list() -> None:
        nonlocal list_kind
        if list_kind is not None:
            items = "".join(
                f"<li>{_render_release_notes_inline(item)}</li>"
                for item in list_items
            )
            blocks.append(f"<{list_kind}>{items}</{list_kind}>")
            list_items.clear()
            list_kind = None

    for line in markdown_text.splitlines():
        if not line.strip():
            flush_paragraph()
            flush_list()
            continue

        header = re.match(r"^\s{0,3}(#{1,4})\s+(.+?)\s*$", line)
        if header:
            flush_paragraph()
            flush_list()
            level = len(header.group(1))
            blocks.append(
                f"<h{level}>{_render_release_notes_inline(header.group(2))}</h{level}>"
            )
            continue

        unordered = re.match(r"^\s*[-*]\s+(.+?)\s*$", line)
        ordered = re.match(r"^\s*\d+\.\s+(.+?)\s*$", line)
        match = unordered or ordered
        if match:
            flush_paragraph()
            kind = "ul" if unordered else "ol"
            if list_kind != kind:
                flush_list()
                list_kind = kind
            list_items.append(match.group(1))
            continue

        flush_list()
        paragraph.append(line.strip())

    flush_paragraph()
    flush_list()
    return "\n".join(blocks)


def _require_https_url(value: str, label: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError as error:
        raise RuntimeError(f"The {label} URL is invalid.") from error
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        raise RuntimeError(f"The {label} URL must use HTTPS.")
    return value


def _validate_release_asset_url(
    value: str,
    label: str,
    *,
    tag: str,
    asset_name: str,
    allow_redirect_host: bool = False,
) -> str:
    value = _require_https_url(value, label)
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    expected_path = (
        f"/{RELEASE_OWNER}/{RELEASE_REPOSITORY}/releases/download/{tag}/{asset_name}"
    )
    if parsed.username or parsed.password or parsed.fragment:
        raise RuntimeError(f"The {label} URL is not a valid GitHub release asset URL.")
    if host == "github.com" and unquote(parsed.path) == expected_path:
        return value
    if allow_redirect_host and host == "release-assets.githubusercontent.com":
        if parsed.path and parsed.path != "/":
            return value
    raise RuntimeError(f"The {label} URL is not an expected GitHub release asset URL.")


def _validate_download_response(
    response, requested_url: str, label: str, *, tag: str, asset_name: str
) -> None:
    final_url = getattr(response, "url", requested_url)
    if not isinstance(final_url, str) or not final_url:
        final_url = requested_url
    _validate_release_asset_url(
        final_url,
        f"final {label}",
        tag=tag,
        asset_name=asset_name,
        allow_redirect_host=True,
    )


def _checksum_asset_name(name: str) -> bool:
    lowered = name.lower()
    return (
        "checksum" in lowered
        or "sha256sum" in lowered
        or lowered.endswith((".sha256", ".sha256.txt"))
    )


def _release_asset_metadata(release: dict[str, object], tag: str) -> dict[str, object]:
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise RuntimeError("The GitHub release has no downloadable assets.")
    archives = [
        asset
        for asset in assets
        if isinstance(asset, dict)
        and isinstance(asset.get("name"), str)
        and str(asset["name"]).lower().endswith(".zip")
        and not _checksum_asset_name(str(asset["name"]))
    ]
    preferred = [
        asset
        for asset in archives
        if RELEASE_REPOSITORY in str(asset["name"]).lower()
    ]
    if len(preferred) == 1:
        archives = preferred
    elif len(archives) > 1:
        win_preferred = [
            asset
            for asset in archives
            if "windows" in str(asset["name"]).lower()
        ]
        if len(win_preferred) == 1:
            archives = win_preferred

    if len(archives) != 1:
        raise RuntimeError("The GitHub release must contain exactly one Alyssa ZIP asset.")

    archive = archives[0]
    archive_name = str(archive["name"])
    download_url = _validate_release_asset_url(
        str(archive.get("browser_download_url") or ""),
        "release download",
        tag=tag,
        asset_name=archive_name,
    )
    digest = archive.get("digest")
    sha256 = None
    checksum_url = None
    checksum_name = None
    if digest is not None:
        match = re.fullmatch(r"sha256:([0-9a-fA-F]{64})", str(digest).strip())
        if not match:
            raise RuntimeError("The GitHub release asset has an invalid SHA-256 digest.")
        sha256 = match.group(1).lower()
    else:
        checksum_assets = [
            asset
            for asset in assets
            if isinstance(asset, dict)
            and isinstance(asset.get("name"), str)
            and _checksum_asset_name(str(asset["name"]))
        ]
        if not checksum_assets:
            raise RuntimeError(
                "The GitHub release has no SHA-256 digest or checksum asset; "
                "refusing to install an unverified archive."
            )
        checksum_asset = sorted(
            checksum_assets, key=lambda asset: str(asset["name"]).lower()
        )[0]
        checksum_name = str(checksum_asset["name"])
        checksum_url = _validate_release_asset_url(
            str(checksum_asset.get("browser_download_url") or ""),
            "checksum download",
            tag=tag,
            asset_name=checksum_name,
        )
    return {
        "download_url": download_url,
        "archive_name": archive_name,
        "sha256": sha256,
        "checksum_url": checksum_url,
        "checksum_name": checksum_name,
    }


def _parse_checksum_file(contents: str, archive_name: str) -> str:
    for line in contents.splitlines():
        standard = re.fullmatch(
            r"\s*([0-9a-fA-F]{64})\s+[*]?(.+?)\s*", line
        )
        bsd = re.fullmatch(
            r"\s*SHA256\s*\((.+?)\)\s*=\s*([0-9a-fA-F]{64})\s*",
            line,
            re.IGNORECASE,
        )
        if standard:
            digest, name = standard.group(1), standard.group(2)
        elif bsd:
            name, digest = bsd.group(1), bsd.group(2)
        else:
            continue
        if PurePosixPath(name.replace("\\", "/")).name == archive_name:
            return digest.lower()
    raise RuntimeError(
        f"The published checksum file has no SHA-256 entry for {archive_name}."
    )


def _fetch_published_checksum(
    url: str, *, tag: str, checksum_name: str, archive_name: str, headers: dict[str, str]
) -> str:
    response = None
    try:
        response = requests.get(url, headers=headers, stream=True, timeout=(10, 30))
        response.raise_for_status()
        _validate_download_response(
            response, url, "checksum download", tag=tag, asset_name=checksum_name
        )
        downloaded = 0
        chunks = []
        for chunk in response.iter_content(64 * 1024):
            if not chunk:
                continue
            downloaded += len(chunk)
            if downloaded > MAX_CHECKSUM_BYTES:
                raise RuntimeError("The release checksum file is unexpectedly large.")
            chunks.append(chunk)
    except requests.RequestException as error:
        raise RuntimeError(f"Couldn't download the release checksum: {error}") from error
    finally:
        if response is not None:
            response.close()
    try:
        contents = b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError as error:
        raise RuntimeError("The release checksum file is not valid UTF-8.") from error
    return _parse_checksum_file(contents, archive_name)

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
    old_values = {
        name: value for name, value in _assignment_values(old_source).items()
        if name not in SECRET_CONFIG_FIELDS
    }
    tree = ast.parse(new_source)
    resolvable_names = set(dir(builtins))

    def add_assignment_names(target: ast.expr) -> None:
        resolvable_names.update(
            child.id
            for child in ast.walk(target)
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store)
        )

    def add_resolvable_names(node: ast.stmt) -> None:
        if isinstance(node, ast.Import):
            resolvable_names.update(
                alias.asname or alias.name.split(".", 1)[0] for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            resolvable_names.update(
                alias.asname or alias.name for alias in node.names if alias.name != "*"
            )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            resolvable_names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                add_assignment_names(target)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            add_assignment_names(node.target)

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
            add_resolvable_names(node)
            continue
        if not isinstance(target, ast.Name) or target.id not in old_values or node.value is None:
            add_resolvable_names(node)
            continue
        old_value = old_values[target.id]
        try:
            # Wrap in parentheses: get_source_segment() returns the value's
            # source text without any enclosing parens from the original
            # assignment, so a multi-line value (e.g. implicit string
            # concatenation across lines) can look like invalid indentation
            # when re-parsed on its own. Parentheses restore that context
            # without changing what the expression evaluates to.
            old_value_tree = ast.parse(f"({old_value})", mode="eval")
        except SyntaxError:
            print(
                f"config: kept new default for {target.id} - old value could "
                "not be re-parsed on its own"
            )
            add_resolvable_names(node)
            continue
        referenced_names = {
            child.id
            for child in ast.walk(old_value_tree)
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)
        }
        missing_names = sorted(referenced_names - resolvable_names)
        if missing_names:
            names = ", ".join(repr(name) for name in missing_names)
            verb = "is" if len(missing_names) == 1 else "are"
            print(
                f"config: kept new default for {target.id} - old value referenced "
                f"{names}, which {verb} not yet imported or defined"
            )
            add_resolvable_names(node)
            continue
        start = offsets[node.value.lineno - 1] + node.value.col_offset
        end = offsets[node.value.end_lineno - 1] + node.value.end_col_offset
        replacements.append((start, end, old_value))

        # Names become available only after their top-level statement runs.
        add_resolvable_names(node)

    merged = new_source
    for start, end, value in reversed(replacements):
        merged = merged[:start] + value + merged[end:]
    try:
        compile(merged, "config.py", "exec")
    except SyntaxError:
        print("config: merged settings were invalid; kept new config unchanged")
        return new_source
    return merged


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

    if len(roots) == 1:
        candidate = destination / next(iter(roots))
        if candidate.is_dir() and (
            (candidate / "config.py").is_file()
            or (candidate / "Alyssa.exe").is_file()
            or (candidate / "Alyssa").is_file()
            or (candidate / "main.py").is_file()
        ):
            root = candidate
        else:
            root = destination
    else:
        root = destination

    has_exe = (root / "Alyssa.exe").is_file() or (root / "Alyssa").is_file()
    has_source = (root / "main.py").is_file() and (
        (root / "overlay.py").is_file() or (root / "overlay" / "__init__.py").is_file()
    )
    if not (root / "config.py").is_file() or not (has_exe or has_source):
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


def _write_transaction_state(transaction_root: Path, state: dict[str, object]) -> None:
    path = transaction_root / "state.json"
    temporary = transaction_root / "state.json.update-tmp"
    temporary.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _load_transaction_state(transaction_root: Path) -> dict[str, object]:
    try:
        state = json.loads((transaction_root / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as error:
        raise RuntimeError("The interrupted update journal is unreadable.") from error
    if not isinstance(state, dict) or not isinstance(state.get("changes"), list):
        raise RuntimeError("The interrupted update journal is invalid.")
    return state


def _journal_destination(install_root: Path, relative_name: object) -> tuple[Path, Path]:
    if (
        not isinstance(relative_name, str)
        or "\\" in relative_name
        or any(":" in part for part in PurePosixPath(relative_name).parts)
    ):
        raise RuntimeError("The interrupted update journal contains an unsafe path.")
    relative = PurePosixPath(relative_name)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise RuntimeError("The interrupted update journal contains an unsafe path.")
    native_relative = Path(*relative.parts)
    destination = install_root / native_relative
    if not destination.resolve().is_relative_to(install_root):
        raise RuntimeError("The interrupted update journal contains an unsafe path.")
    return native_relative, destination


def _finish_transaction(install_root: Path, transaction_root: Path) -> None:
    committed_root = install_root / COMMITTED_TRANSACTION_DIR
    if committed_root.exists():
        shutil.rmtree(committed_root)
    os.replace(transaction_root, committed_root)
    shutil.rmtree(committed_root)


def _rollback_transaction(install_root: Path, transaction_root: Path) -> None:
    state = _load_transaction_state(transaction_root)
    backup_root = transaction_root / "backup"
    for change in reversed(state["changes"]):
        if not isinstance(change, dict):
            raise RuntimeError("The interrupted update journal is invalid.")
        relative, destination = _journal_destination(install_root, change.get("path"))
        if change.get("existed") is True:
            backup = backup_root / relative
            if not backup.is_file():
                raise RuntimeError("The interrupted update backup is incomplete.")
            _atomic_copy(backup, destination)
        else:
            destination.unlink(missing_ok=True)
            parent = destination.parent
            while parent != install_root:
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent
    _finish_transaction(install_root, transaction_root)


def _recover_incomplete_update(install_root: Path) -> bool:
    install_root = install_root.resolve()
    committed_root = install_root / COMMITTED_TRANSACTION_DIR
    if committed_root.exists():
        shutil.rmtree(committed_root)
    transaction_root = install_root / TRANSACTION_DIR
    if not transaction_root.exists():
        return False
    state_path = transaction_root / "state.json"
    if not state_path.is_file():
        shutil.rmtree(transaction_root)
        return True
    state = _load_transaction_state(transaction_root)
    if state.get("phase") == "committed":
        _finish_transaction(install_root, transaction_root)
        return True
    if state.get("phase") == "pending_restart":
        staged_root = transaction_root / "new"
        try:
            for change in state.get("changes", []):
                if not isinstance(change, dict):
                    continue
                relative, destination = _journal_destination(install_root, change.get("path"))
                if change.get("delete") is True:
                    destination.unlink(missing_ok=True)
                else:
                    staged = staged_root / relative
                    if staged.is_file():
                        _atomic_copy(staged, destination)
            _finish_transaction(install_root, transaction_root)
            bat = install_root / "apply_update.bat"
            bat.unlink(missing_ok=True)
            return True
        except (PermissionError, OSError):
            return False
    try:
        _rollback_transaction(install_root, transaction_root)
    except Exception as error:
        raise RuntimeError(
            "An interrupted Alyssa update could not be rolled back. "
            "Restore the installation manually before trying again."
        ) from error
    return True


def _prepare_update_transaction(
    source_root: Path, install_root: Path, managed: set[str], version: str
) -> Path:
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
    transaction_root = install_root / TRANSACTION_DIR
    transaction_root.mkdir()
    staged_root = transaction_root / "new"
    backup_root = transaction_root / "backup"
    staged_root.mkdir()
    backup_root.mkdir()
    changes = []
    recorded = set()

    def record(relative_name: str, *, delete: bool = False) -> None:
        if relative_name in recorded:
            return
        recorded.add(relative_name)
        relative, destination = _journal_destination(install_root, relative_name)
        if destination.exists() and not destination.is_file():
            raise RuntimeError(f"Cannot update {relative_name}: destination is not a file.")
        existed = destination.is_file()
        if existed:
            backup = backup_root / relative
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(destination, backup)
        changes.append({"path": relative_name, "existed": existed, "delete": delete})

    try:
        for source in sorted(source_root.rglob("*")):
            if not source.is_file():
                continue
            relative = source.relative_to(source_root)
            relative_name = relative.as_posix()
            if relative_name in {MANIFEST_FILE, ".alyssa-version"}:
                continue
            if relative_name not in managed and relative_name.lower() != "config.py":
                continue
            destination = install_root / relative
            staged = staged_root / relative
            staged.parent.mkdir(parents=True, exist_ok=True)
            if relative_name.lower() == "config.py" and destination.is_file():
                merged = merge_config_settings(
                    source.read_text(encoding="utf-8"),
                    destination.read_text(encoding="utf-8"),
                )
                staged.write_text(merged, encoding="utf-8")
            else:
                shutil.copy2(source, staged)
            record(relative_name)

        for name in sorted(obsolete):
            _, destination = _journal_destination(install_root, name)
            if destination.is_file():
                record(name, delete=True)

        manifest = {
            relative: _file_hash(staged_root / Path(*PurePosixPath(relative).parts))
            for relative in sorted(managed)
            if (staged_root / Path(*PurePosixPath(relative).parts)).is_file()
        }
        (staged_root / MANIFEST_FILE).write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        (staged_root / ".alyssa-version").write_text(version + "\n", encoding="utf-8")
        record(MANIFEST_FILE)
        record(".alyssa-version")
        state = {"phase": "prepared", "version": version, "changes": changes}
        _write_transaction_state(transaction_root, state)
    except BaseException:
        if not (transaction_root / "state.json").is_file():
            shutil.rmtree(transaction_root, ignore_errors=True)
        raise
    return transaction_root


def _prepare_restart_updater(
    install_root: Path, transaction_root: Path, staged_root: Path
) -> Path:
    bat_path = install_root / "apply_update.bat"
    bat_content = (
        "@echo off\r\n"
        "setlocal\r\n"
        "set \"PID=%~1\"\r\n"
        "set \"INSTALL_ROOT=%~2\"\r\n"
        "set \"STAGED_ROOT=%~3\"\r\n"
        "set \"TARGET_EXE=%~4\"\r\n"
        "\r\n"
        # cmd can't goto a label inside a ( ) block, so the wait loop is flat.
        "if \"%PID%\"==\"\" goto apply\r\n"
        ":wait_loop\r\n"
        "timeout /t 1 /nobreak >nul 2>&1\r\n"
        "tasklist /fi \"PID eq %PID%\" 2>nul | find \"%PID%\" >nul\r\n"
        "if not errorlevel 1 goto wait_loop\r\n"
        ":apply\r\n"
        "\r\n"
        "timeout /t 1 /nobreak >nul 2>&1\r\n"
        "\r\n"
        "rem Copy all staged files into install directory\r\n"
        "xcopy \"%STAGED_ROOT%\\*\" \"%INSTALL_ROOT%\\\" /E /Y /Q /H /R >nul 2>&1\r\n"
        "\r\n"
        "rem Remove transaction directory\r\n"
        f"rd /s /q \"{transaction_root}\" >nul 2>&1\r\n"
        "\r\n"
        "rem Re-launch Alyssa\r\n"
        "if exist \"%TARGET_EXE%\" (\r\n"
        "    start \"\" \"%TARGET_EXE%\"\r\n"
        ") else if exist \"%INSTALL_ROOT%\\Alyssa.exe\" (\r\n"
        "    start \"\" \"%INSTALL_ROOT%\\Alyssa.exe\"\r\n"
        ") else if exist \"%INSTALL_ROOT%\\scripts\\start_alyssa.bat\" (\r\n"
        "    start \"\" \"%INSTALL_ROOT%\\scripts\\start_alyssa.bat\"\r\n"
        ")\r\n"
        "\r\n"
        "(goto) 2>nul & del \"%~f0\"\r\n"
        "exit\r\n"
    )
    bat_path.write_text(bat_content, encoding="utf-8")
    return bat_path


def _is_locked_file_error(error: Exception) -> bool:
    """True if error is a Windows sharing violation or running process file lock."""
    if sys.platform == "win32":
        winerror = getattr(error, "winerror", None)
        if winerror in (32, 5):  # ERROR_SHARING_VIOLATION, ERROR_ACCESS_DENIED
            return True
        if isinstance(error, PermissionError):
            return True
    return False


def _commit_update_transaction(install_root: Path, transaction_root: Path) -> None:
    state = _load_transaction_state(transaction_root)
    state["phase"] = "committing"
    _write_transaction_state(transaction_root, state)
    staged_root = transaction_root / "new"
    locked_changes = []
    try:
        for change in state["changes"]:
            if not isinstance(change, dict):
                raise RuntimeError("The update journal is invalid.")
            relative, destination = _journal_destination(install_root, change.get("path"))
            if change.get("delete") is True:
                try:
                    destination.unlink(missing_ok=True)
                except Exception as err:
                    if _is_locked_file_error(err):
                        locked_changes.append(change)
                    else:
                        raise
            else:
                staged = staged_root / relative
                if not staged.is_file():
                    raise RuntimeError("The staged update is incomplete.")
                try:
                    _atomic_copy(staged, destination)
                except Exception as err:
                    if _is_locked_file_error(err):
                        locked_changes.append(change)
                    else:
                        raise

        if locked_changes:
            _prepare_restart_updater(install_root, transaction_root, staged_root)
            state["phase"] = "pending_restart"
            _write_transaction_state(transaction_root, state)
            return

        state["phase"] = "committed"
        _write_transaction_state(transaction_root, state)
    except Exception:
        # Any real failure rolls back, even after some files were deferred as locked;
        # swallowing it here used to finish a half-applied update with no journal left.
        _rollback_transaction(install_root, transaction_root)
        raise
    try:
        _finish_transaction(install_root, transaction_root)
    except OSError:
        # The committed journal is safe to clean up on the next launch/check.
        pass


def _apply_release(
    source_root: Path,
    install_root: Path,
    managed: set[str] | None = None,
    version: str | None = None,
) -> None:
    """Stage and commit files, manifest, and version through a rollback journal."""
    install_root = install_root.resolve()
    _recover_incomplete_update(install_root)
    version = version if version is not None else current_version(install_root)
    managed = managed if managed is not None else _managed_paths(source_root, install_root)
    transaction_root = _prepare_update_transaction(
        source_root, install_root, managed, version
    )
    _commit_update_transaction(install_root, transaction_root)


def current_version(install_root: str | os.PathLike[str]) -> str:
    install_root = Path(install_root).resolve()
    _recover_incomplete_update(install_root)
    marker = install_root / ".alyssa-version"
    return marker.read_text(encoding="utf-8").strip() if marker.is_file() else CURRENT_VERSION


def check_latest(install_root: str | os.PathLike[str]) -> dict[str, object]:
    """Return GitHub's latest release metadata and whether it is newer."""
    installed = current_version(install_root)
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "Alyssa-Updater"}
    response = None
    try:
        response = requests.get(LATEST_RELEASE_URL, headers=headers, timeout=20)
        response.raise_for_status()
        release = response.json()
        tag = str(release["tag_name"]).strip()
    except (requests.RequestException, KeyError, TypeError, ValueError) as error:
        raise RuntimeError(f"Couldn't check GitHub for updates: {error}") from error
    finally:
        if response is not None:
            response.close()

    latest_key, installed_key = _version_key(tag), _version_key(installed)
    if latest_key is None or installed_key is None:
        raise RuntimeError(f"Couldn't compare release versions: installed {installed}, latest {tag}.")
    update_available = installed_key < latest_key
    asset_metadata = {
        "download_url": "",
        "archive_name": "",
        "sha256": None,
        "checksum_url": None,
        "checksum_name": None,
    }
    if update_available:
        asset_metadata = _release_asset_metadata(release, tag)
    return {
        "current_version": installed,
        "latest_version": tag,
        "update_available": update_available,
        "notes": str(release.get("body") or "No release notes were provided.").strip(),
        **asset_metadata,
    }


def install_release(
    install_root: str | os.PathLike[str], release: dict[str, object]
) -> str:
    """Download, verify SHA-256, and journal-install a release from check_latest."""
    install_root = Path(install_root).resolve()
    _recover_incomplete_update(install_root)
    try:
        tag = str(release["latest_version"]).strip()
        archive_name = str(release["archive_name"]).strip()
        download_url = _validate_release_asset_url(
            str(release["download_url"]),
            "release download",
            tag=tag,
            asset_name=archive_name,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError("The release metadata is incomplete.") from error
    if _version_key(tag) is None:
        raise RuntimeError(f"The release version is invalid: {tag}.")
    if not archive_name or PurePosixPath(archive_name).name != archive_name:
        raise RuntimeError("The release archive name is invalid.")

    expected_sha256 = release.get("sha256")
    if expected_sha256 is not None:
        expected_sha256 = str(expected_sha256).strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
            raise RuntimeError("The release metadata has an invalid SHA-256 digest.")
    headers = {"Accept": "application/octet-stream", "User-Agent": "Alyssa-Updater"}
    if expected_sha256 is None:
        checksum_name = str(release.get("checksum_name") or "").strip()
        checksum_url = _validate_release_asset_url(
            str(release.get("checksum_url") or ""),
            "checksum download",
            tag=tag,
            asset_name=checksum_name,
        )
        expected_sha256 = _fetch_published_checksum(
            checksum_url,
            tag=tag,
            checksum_name=checksum_name,
            archive_name=archive_name,
            headers=headers,
        )

    with tempfile.TemporaryDirectory(prefix="alyssa-update-") as temporary_name:
        temporary = Path(temporary_name)
        archive_path = temporary / archive_name
        response = None
        try:
            response = requests.get(
                download_url, headers=headers, stream=True, timeout=(10, 60)
            )
            response.raise_for_status()
            _validate_download_response(
                response,
                download_url,
                "release download",
                tag=tag,
                asset_name=archive_name,
            )
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

        actual_sha256 = _file_hash(archive_path)
        if actual_sha256 != expected_sha256:
            raise RuntimeError(
                "The release archive checksum did not match GitHub's published SHA-256; "
                "the update was not installed."
            )
        source_root = _safe_extract(archive_path, temporary / "source")
        managed = _managed_paths(source_root, install_root)
        _apply_release(source_root, install_root, managed, version=tag)
    return tag


def install_latest(install_root: str | os.PathLike[str]) -> tuple[bool, str]:
    """Compatibility helper: check for and install GitHub's latest release."""
    release = check_latest(install_root)
    if not release["update_available"]:
        return False, release["latest_version"]
    return True, install_release(install_root, release)
