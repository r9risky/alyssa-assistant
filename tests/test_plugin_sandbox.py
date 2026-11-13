"""Add-ons that declare PERMISSIONS run in their own process and are held to them."""
import os
import textwrap

import pytest

import plugin_loader
import plugin_permissions as pp
import plugin_sandbox

PLUGIN = textwrap.dedent('''
    import os, socket, subprocess
    PERMISSIONS = []
    INSTANT_REPLY_TOOLS = {"greet"}

    def greet(name="you"):
        return f"hi {name} from pid {os.getpid()}"

    def phone_home():
        socket.create_connection(("example.com", 80), timeout=2)
        return "connected"

    def run_program():
        return subprocess.run(["echo", "hi"], capture_output=True, text=True).stdout

    def write_outside(path):
        open(path, "w").write("x")
        return "wrote"

    def write_own():
        open("notes.txt", "w").write("x")  # cwd is its own data folder
        return open("notes.txt").read()

    def read_secret(path):
        return open(path).read()

    def secret_env():
        return os.environ.get("GEMINI_API_KEY", "")

    def handle_intent(cleaned, text=""):
        if cleaned == "ping":
            return "pong"
        if cleaned == "explode":
            raise RuntimeError("boom")
        return None

    def check_watch():
        return "tick"

    FUNCTIONS = {f.__name__: f for f in (greet, phone_home, run_program, write_outside, write_own,
                                         read_secret, secret_env)}
    TOOLS = [{"type": "function", "function": {"name": "greet", "parameters": {"type": "object", "properties": {}}}}]
''')


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "sk-should-not-leak")
    path = tmp_path / "plugins" / "helper.py"
    path.parent.mkdir()
    path.write_text(PLUGIN)
    box = plugin_sandbox.SandboxedPlugin(str(path), [])
    yield box
    box.close()


def test_runs_in_its_own_process_without_secrets(sandbox):
    assert sandbox.call("greet", name="Roi") == f"hi Roi from pid {sandbox._proc.pid}"
    assert sandbox._proc.pid != os.getpid()
    assert sandbox.call("secret_env") == ""


def test_refuses_what_it_did_not_ask_for(sandbox, tmp_path):
    secret = tmp_path / "memory.json"
    secret.write_text("[\"private\"]")
    for call, kwargs in (("phone_home", {}), ("run_program", {}),
                         ("write_outside", {"path": str(tmp_path / "x.txt")}),
                         ("read_secret", {"path": str(secret)})):
        reply = sandbox.call(call, **kwargs)
        assert "didn't ask for permission" in reply, (call, reply)
    assert not (tmp_path / "x.txt").exists()
    assert sandbox.call("write_own") == "x"


def test_hooks_answer_or_stay_out_of_the_way(sandbox):
    assert sandbox.call("__intent__", "ping", "") == "pong"
    assert sandbox.call("__intent__", "explode", "") is None  # a broken hook never answers for you
    assert sandbox.call("__watch__") == "tick"


def test_loader_sandboxes_declared_plugins(tmp_path, monkeypatch):
    path = tmp_path / "helper.py"
    path.write_text(PLUGIN)
    pp.approve("helper.py", str(path))
    monkeypatch.setattr(plugin_loader, "PLUGINS_DIR", str(tmp_path))
    try:
        functions, tools = plugin_loader.load_plugins()
        assert functions["greet"]().startswith("hi you from pid")
        assert functions["greet"]._alyssa_instant_reply
        assert [t["function"]["name"] for t in tools] == ["greet"]
        assert any(h("ping") == "pong" for h in plugin_loader.get_intent_handlers())
    finally:
        for box in plugin_loader._sandboxes:
            box.close()
        monkeypatch.undo()
        import actions
        actions.reload_plugins()
