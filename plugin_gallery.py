"""Plugin gallery: a curated list of extra plugins, installed with one click.

The list lives in this repo (gallery/index.json + gallery/plugins/*.py) and is
fetched from GitHub, so new gallery plugins show up without an app update.
Each download is checked against the sha256 in the index before it's saved.
Installing shows the plugin's PERMISSIONS and counts as approving them.
"""
import hashlib
import os
import re

import requests

import plugin_loader
import plugin_permissions

GALLERY_BASE_URL = "https://raw.githubusercontent.com/r9risky/alyssa-assistant/main/gallery/"
_NAME_RE = re.compile(r"^[a-z0-9_]{1,48}$")


class GalleryError(Exception):
    """A message that's safe to show the user as-is."""


def fetch_index(base_url: str = GALLERY_BASE_URL) -> list[dict]:
    """The gallery's plugins, skipping malformed entries."""
    try:
        response = requests.get(base_url + "index.json", timeout=10)
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as e:
        raise GalleryError(f"Couldn't load the plugin gallery: {e}") from e
    entries = []
    for entry in (data.get("plugins") or []) if isinstance(data, dict) else []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", ""))
        if not _NAME_RE.match(name) or plugin_permissions.is_builtin(name):
            continue
        if not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("sha256", ""))):
            continue
        entries.append({
            "name": name,
            "title": str(entry.get("title") or name),
            "description": str(entry.get("description") or ""),
            "permissions": [str(p) for p in entry.get("permissions") or []],
            "file": str(entry.get("file") or f"plugins/{name}.py"),
            "sha256": entry["sha256"],
        })
    return entries


def installed_path(name: str) -> str | None:
    """Path of this gallery plugin in plugins/ (enabled or disabled), if installed."""
    for candidate in (f"{name}.py", f"_{name}.py"):
        path = os.path.join(plugin_loader.PLUGINS_DIR, candidate)
        if os.path.exists(path):
            return path
    return None


def install(entry: dict, base_url: str = GALLERY_BASE_URL) -> str:
    """Download, verify and save the plugin; approve what it declares. Returns its path."""
    name = entry["name"]
    if not _NAME_RE.match(name) or plugin_permissions.is_builtin(name):
        raise GalleryError(f"'{name}' isn't a valid gallery plugin name.")
    if not re.fullmatch(r"plugins/[a-z0-9_]{1,48}\.py", entry.get("file", "")):
        raise GalleryError(f"'{name}' points somewhere I won't download from.")
    try:
        response = requests.get(base_url + entry["file"], timeout=15)
        response.raise_for_status()
    except requests.RequestException as e:
        raise GalleryError(f"Couldn't download {name}: {e}") from e
    if hashlib.sha256(response.content).hexdigest() != entry["sha256"]:
        raise GalleryError(f"{name} didn't match the gallery's checksum, so I didn't install it.")

    path = installed_path(name) or os.path.join(plugin_loader.PLUGINS_DIR, f"{name}.py")
    os.makedirs(plugin_loader.PLUGINS_DIR, exist_ok=True)
    tmp = path + ".download"
    with open(tmp, "wb") as f:
        f.write(response.content)
    if sorted(plugin_permissions.declared(tmp) or []) != sorted(entry["permissions"]):
        os.remove(tmp)
        raise GalleryError(f"{name} asks for different permissions than the gallery lists, so I didn't install it.")
    os.replace(tmp, path)
    plugin_permissions.approve(os.path.basename(path), path)
    return path
