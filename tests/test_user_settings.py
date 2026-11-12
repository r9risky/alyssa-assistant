"""settings.json holds what the user changed; config.py only holds defaults."""
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import user_settings

ROOT = Path(__file__).resolve().parents[1]


def test_save_merges_and_load_round_trips(tmp_path):
    path = str(tmp_path / "settings.json")
    user_settings.save({"LLM_PROVIDER": "ollama", "QUIET_MODE": True}, path)
    user_settings.save({"QUIET_MODE": False}, path)
    assert user_settings.load(path) == {"LLM_PROVIDER": "ollama", "QUIET_MODE": False}


def test_broken_file_falls_back_to_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not json", encoding="utf-8")
    assert user_settings.load(str(path)) == {}
    path.write_text("[1, 2]", encoding="utf-8")
    assert user_settings.load(str(path)) == {}


def test_failed_write_keeps_the_old_file(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"QUIET_MODE": true}', encoding="utf-8")
    with patch.object(user_settings.os, "replace", side_effect=OSError("disk error")):
        try:
            user_settings.save({"QUIET_MODE": False}, str(path))
        except OSError:
            pass
        else:
            raise AssertionError("save() should raise when the write fails")
    assert json.loads(path.read_text(encoding="utf-8")) == {"QUIET_MODE": True}
    assert not os.path.exists(str(path) + ".tmp")


def test_apply_only_overrides_known_non_secret_settings(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"LLM_PROVIDER": "ollama", "NOT_A_SETTING": 1, "GEMINI_API_KEY": "x", "lower": 2}),
        encoding="utf-8",
    )
    namespace = {"LLM_PROVIDER": "gemini", "GEMINI_API_KEY": "", "lower": 0}
    applied = user_settings.apply(namespace, str(path), skip={"GEMINI_API_KEY"})
    assert applied == ["LLM_PROVIDER"]
    assert namespace == {"LLM_PROVIDER": "ollama", "GEMINI_API_KEY": "", "lower": 0}


def _config_value(tmp_path, settings_text, name):
    """Import config.py in a fresh interpreter with settings.json next to it."""
    (tmp_path / "settings.json").write_text(settings_text, encoding="utf-8")
    code = (
        "import sys, user_settings;"
        f"user_settings.SETTINGS_FILE = {str(tmp_path / 'settings.json')!r};"
        f"import config; print(repr(config.{name}))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60
    )
    assert out.returncode == 0, out.stderr
    return out.stdout.strip().splitlines()[-1]


def test_config_picks_up_saved_settings(tmp_path):
    assert _config_value(tmp_path, '{"WAKE_WORD_MODE": "always_listening"}', "WAKE_WORD_MODE") == "'always_listening'"


def test_config_still_loads_with_a_corrupt_settings_file(tmp_path):
    default = _config_value(tmp_path, "{}", "LLM_PROVIDER")
    assert _config_value(tmp_path, "garbage{", "LLM_PROVIDER") == default


def test_settings_file_survives_updates():
    import updater

    assert "settings.json" in updater.PRESERVED_FILES
