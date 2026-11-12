import os

import actions
import plugin_loader
import plugin_permissions as pp

REPO_PLUGINS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugins")


def test_builtin_list_matches_shipped_plugins():
    shipped = {n[:-3].lstrip("_") for n in os.listdir(REPO_PLUGINS) if n.endswith(".py") and not n.startswith("__")}
    assert shipped == set(pp.BUILTIN_PLUGINS), "add new plugins/*.py files to BUILTIN_PLUGINS"


def _write(folder, name, body):
    path = os.path.join(folder, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    return path


def test_declared_is_read_without_running_the_plugin(tmp_path):
    path = _write(tmp_path, "x.py", "raise SystemExit('ran!')\nPERMISSIONS = ['Network', 'files']\n")
    assert pp.declared(path) == ["files", "network"]
    assert pp.declared(_write(tmp_path, "y.py", "X = 1\n")) is None


def test_new_plugin_waits_and_asks_again_when_it_wants_more(tmp_path):
    path = _write(tmp_path, "gallery_thing.py", "PERMISSIONS = ['network']\n")
    assert pp.needs_approval("gallery_thing.py", path) == ["network"]
    pp.approve("gallery_thing.py", path)
    assert pp.needs_approval("gallery_thing.py", path) is None
    assert pp.needs_approval("_gallery_thing.py", path) is None  # disabling keeps the approval
    _write(tmp_path, "gallery_thing.py", "PERMISSIONS = ['network', 'shell']\n")
    assert pp.needs_approval("gallery_thing.py", path) == ["network", "shell"]
    undeclared = _write(tmp_path, "mystery.py", "")
    assert pp.needs_approval("mystery.py", undeclared) == [pp.UNDECLARED]
    assert "could do anything" in pp.describe([pp.UNDECLARED])
    assert pp.needs_approval("weather.py", undeclared) is None  # built-in


def test_existing_custom_plugins_are_grandfathered_once(tmp_path):
    _write(tmp_path, "mine.py", "")
    pp.grandfather_existing(str(tmp_path))
    assert pp.needs_approval("mine.py", os.path.join(tmp_path, "mine.py")) is None
    newer = _write(tmp_path, "newer.py", "")
    pp.grandfather_existing(str(tmp_path))  # already initialised: no new free passes
    assert pp.needs_approval("newer.py", newer) == [pp.UNDECLARED]


def test_loader_never_runs_an_unapproved_plugin(tmp_path, monkeypatch):
    pp._save({})
    marker = tmp_path / "ran.txt"
    _write(
        tmp_path, "sneaky.py",
        f"open({str(marker)!r}, 'w').write('x')\nPERMISSIONS = ['network']\n"
        "FUNCTIONS = {'sneaky': lambda: 'hi'}\n",
    )
    monkeypatch.setattr(plugin_loader, "PLUGINS_DIR", str(tmp_path))
    try:
        functions, _tools = plugin_loader.load_plugins()
        assert "sneaky" not in functions and not marker.exists()
        assert plugin_loader.get_pending_approvals() == [{"name": "sneaky.py", "permissions": ["network"]}]

        pp.approve("sneaky.py", str(tmp_path / "sneaky.py"))
        functions, _tools = plugin_loader.load_plugins()
        assert "sneaky" in functions and marker.exists()
    finally:
        monkeypatch.undo()
        actions.reload_plugins()
