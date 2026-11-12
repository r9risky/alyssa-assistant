import hashlib
import json
import os
import runpy
import sys
from unittest.mock import MagicMock, patch

import pytest

import plugin_gallery
import plugin_loader
import plugin_permissions

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import build_gallery_index  # noqa: E402


def test_index_is_up_to_date():
    with open(os.path.join(ROOT, "gallery", "index.json"), encoding="utf-8") as f:
        assert json.load(f) == build_gallery_index.build(), "run python scripts/build_gallery_index.py"


@pytest.mark.parametrize("entry", build_gallery_index.build()["plugins"], ids=lambda e: e["name"])
def test_gallery_plugins_are_valid(entry):
    assert not plugin_permissions.is_builtin(entry["name"])
    namespace = runpy.run_path(os.path.join(ROOT, "gallery", entry["file"]))
    tool_names = {t["function"]["name"] for t in namespace["TOOLS"]}
    assert tool_names == set(namespace["FUNCTIONS"])
    assert set(entry["permissions"]) <= set(plugin_permissions.PERMISSIONS)


def test_dice_plugin_rolls():
    namespace = runpy.run_path(os.path.join(ROOT, "gallery", "plugins", "dice_and_coins.py"))
    assert namespace["roll_dice"]("3d6").endswith("total.")
    assert "like '2d6'" in namespace["roll_dice"]("banana")


def _response(content: bytes):
    r = MagicMock()
    r.content = content
    r.raise_for_status.return_value = None
    return r


SOURCE = b"PERMISSIONS = ['network']\nFUNCTIONS = {'hello_net': lambda: 'hi'}\n"
ENTRY = {"name": "hello_net", "title": "Hello", "description": "", "permissions": ["network"],
         "file": "plugins/hello_net.py", "sha256": hashlib.sha256(SOURCE).hexdigest()}


def test_install_verifies_saves_approves_and_loads(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin_loader, "PLUGINS_DIR", str(tmp_path))
    with patch.object(plugin_gallery.requests, "get", return_value=_response(SOURCE)):
        path = plugin_gallery.install(dict(ENTRY))
    assert open(path, "rb").read() == SOURCE
    functions, _ = plugin_loader.load_plugins()
    assert "hello_net" in functions


@pytest.mark.parametrize("change, message", [
    ({"sha256": "0" * 64}, "checksum"),
    ({"permissions": []}, "different permissions"),
    ({"name": "weather"}, "valid gallery plugin"),
    ({"file": "../main.py"}, "won't download"),
])
def test_install_refuses_anything_off(tmp_path, monkeypatch, change, message):
    monkeypatch.setattr(plugin_loader, "PLUGINS_DIR", str(tmp_path))
    with patch.object(plugin_gallery.requests, "get", return_value=_response(SOURCE)):
        with pytest.raises(plugin_gallery.GalleryError, match=message):
            plugin_gallery.install({**ENTRY, **change})
    assert os.listdir(tmp_path) == []


def test_fetch_index_drops_bad_entries():
    payload = {"plugins": [ENTRY, {**ENTRY, "name": "../evil"}, {**ENTRY, "name": "timers"}, "junk"]}
    r = _response(b"")
    r.json.return_value = payload
    with patch.object(plugin_gallery.requests, "get", return_value=r):
        assert [e["name"] for e in plugin_gallery.fetch_index()] == ["hello_net"]
