import sys
import types

import startup_logging
from overlay import app_shell


def test_crashing_loop_restarts_then_gives_up():
    calls, given_up, waits = [], [], []

    def flaky():
        calls.append(1)
        raise RuntimeError("boom")

    app_shell.run_with_restarts(flaky, given_up.append, attempts=3, wait=waits.append)
    assert len(calls) == 3 and waits == [2, 4]
    assert str(given_up[0]) == "boom"


def test_loop_that_recovers_never_gives_up():
    calls, given_up = [], []

    def once_flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("hiccup")
        return "ok"

    assert app_shell.run_with_restarts(once_flaky, given_up.append, wait=lambda s: None) == "ok"
    assert not given_up


def test_warn_once_logs_each_place_once(capsys):
    for _ in range(3):
        startup_logging.warn_once("test place", ValueError("bad api_key=sk-12345678secret"))
    out = capsys.readouterr().out
    assert out.count("[warn] test place") == 1 and "sk-12345678secret" not in out


def test_shutdown_stops_started_services_and_survives_failures(monkeypatch):
    stopped = []
    bot = types.SimpleNamespace(stop=lambda: (_ for _ in ()).throw(RuntimeError("stuck")))
    remote = types.SimpleNamespace(stop=lambda: stopped.append("remote"))
    monkeypatch.setitem(sys.modules, "discord_bot", bot)
    monkeypatch.setitem(sys.modules, "phone_remote", remote)
    app_shell.shut_down_services(timeout=2)
    assert stopped == ["remote"]


def test_corrupt_memory_file_is_kept_not_overwritten(tmp_path, monkeypatch):
    import memory

    path = tmp_path / "memory.json"
    path.write_text("[\"likes tea\", ")
    monkeypatch.setattr(memory, "MEMORY_FILE", str(path))
    assert memory.load_memories() == []
    assert (tmp_path / "memory.json.corrupt").read_text() == "[\"likes tea\", "


def test_fact_embeddings_are_reused_between_turns(monkeypatch):
    import numpy as np

    import memory

    embedded = []

    class FakeModel:
        def passage_embed(self, facts):
            embedded.extend(facts)
            return (np.array([float(len(f)), 1.0]) for f in facts)

    monkeypatch.setattr(memory, "_FACT_VECTORS", {})
    model = FakeModel()
    memory._fact_vectors(model, ["likes tea", "has a cat"])
    vectors = memory._fact_vectors(model, ["likes tea", "has a cat", "plays piano"])
    assert embedded == ["likes tea", "has a cat", "plays piano"] and len(vectors) == 3
    memory._fact_vectors(model, ["plays piano"])
    assert set(memory._FACT_VECTORS) == {"plays piano"}


def test_call_reply_uses_persona_voice(monkeypatch):
    from brain import dialogue, persona

    seen = {}

    def fake_call(messages, provider, label, tools=None):
        seen["system"] = messages[0]["content"]
        return {"message": {"content": "**Ha**, nice one!"}}, None

    monkeypatch.setattr(dialogue, "_call_model_with_error_handling", fake_call)
    monkeypatch.setattr(dialogue.memory, "load_personality_notes", lambda: "Loves puns.")
    monkeypatch.setattr(dialogue.memory, "get_personality_path", lambda: __file__)
    reply = dialogue.reply_to_call("Sam", "Alyssa, tell a joke")
    assert reply == "Ha, nice one!"
    assert persona.voice(dialogue.ui_config.ASSISTANT_NAME, "x").split()[0] in seen["system"]
    assert "Loves puns." in seen["system"]
