"""Behaviour kept when the Capri-derived text and names were rewritten."""
import hashlib
from unittest.mock import patch

import memory
from actions.confirmation import get_preset_permissions, normalize_preset
from plugins.mini_games_and_ink import parse_ink_shapes


def test_old_security_preset_names_still_work():
    assert normalize_preset("blackwall") == "autopilot"
    assert normalize_preset("Trusted") == "balanced"
    assert normalize_preset("lockdown") == "ask_always"
    assert normalize_preset("guarded") == "careful"
    assert normalize_preset("nonsense") == "balanced"
    assert get_preset_permissions("guarded") == get_preset_permissions("careful")


def test_untouched_old_check_ins_file_gets_new_defaults(tmp_path):
    path = tmp_path / "check-ins.md"
    path.write_text("my own lines\n", encoding="utf-8")
    with patch.object(memory, "get_check_ins_path", return_value=str(path)):
        assert memory.load_check_in_lines() == "my own lines"
        with patch.object(memory, "_OLD_DEFAULT_CHECK_INS_SHA256",
                          hashlib.sha256(b"my own lines").hexdigest()):
            assert memory.load_check_in_lines() == memory.DEFAULT_CHECK_INS_MARKDOWN.strip()


def test_ink_commands():
    shapes = parse_ink_shapes("circle 100 100 50; box 10 20 30 40; label 5 6 Look here; circle 1 1 0")
    assert [s["kind"] for s in shapes] == ["circle", "box", "text"]
    assert shapes[0]["points"] == [(50.0, 50.0), (150.0, 150.0)]
    assert shapes[1]["points"] == [(10.0, 20.0), (40.0, 60.0)]
    assert shapes[2]["text"] == "Look here"
