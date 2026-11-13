"""Settings that looked fine in the window but did nothing, or didn't survive a restart."""
import importlib.util
import json
import os
import pathlib
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QCheckBox, QSpinBox

import config
import overlay
import user_settings
import voice_dsp
from overlay import widgets

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _load_plugin(stem):
    spec = importlib.util.spec_from_file_location(f"test_plugin_{stem}", ROOT / "plugins" / f"{stem}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_robot_effect_plays_without_a_hidden_master_switch(tmp_path):
    import wave
    import numpy as np

    path = tmp_path / "voice.wav"
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(24000)
        wf.writeframes((np.sin(np.arange(24000) / 10.0) * 8000).astype(np.int16).tobytes())
    with patch.object(config, "VOICE_ROBOT_AMOUNT", 40):
        assert voice_dsp.effects_active()
        out = voice_dsp.process_audio_file(str(path))
    assert out is not None
    os.remove(out)
    assert not voice_dsp.effects_active()  # defaults: her voice untouched, no processing


def test_effects_saved_while_the_old_switch_was_off_stay_off(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"VOICE_DSP_ENABLED": False, "VOICE_ROBOT_AMOUNT": 60, "QUIET_MODE": True}))
    loaded = user_settings.load(str(path))
    assert loaded["VOICE_ROBOT_AMOUNT"] == 0 and loaded["QUIET_MODE"] is True
    assert "VOICE_DSP_ENABLED" not in loaded

    user_settings.save({"VOICE_ROBOT_AMOUNT": 25}, str(path))  # a later change sticks
    assert user_settings.load(str(path))["VOICE_ROBOT_AMOUNT"] == 25
    assert "VOICE_DSP_ENABLED" not in json.loads(path.read_text())


def test_effects_saved_with_the_old_switch_on_keep_playing(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"VOICE_DSP_ENABLED": True, "VOICE_ROBOT_AMOUNT": 60}))
    assert user_settings.load(str(path)) == {"VOICE_ROBOT_AMOUNT": 60}


def test_builtin_settings_tabs_use_the_names_the_loader_looks_for():
    for path in (ROOT / "plugins").glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "TAB_TITLE = " not in source.replace("GUI_PANEL_TITLE = ", ""), path.name
        if "def build_gui_panel(" in source:
            assert "GUI_PANEL_TITLE = " in source, path.name


@pytest.mark.parametrize("stem,label,key", [
    ("screen_lookout", "Watch my screen", "SCREEN_WATCH_ENABLED"),
    ("screen_lookout", "Check in on me", "CHECK_IN_ENABLED"),
    ("code_husk_mode", "Turn on coding buddy mode", "HUSK_MODE_ENABLED"),
])
def test_addon_tab_toggles_are_saved(app, stem, label, key):
    module = _load_plugin(stem)
    original = getattr(config, key)
    try:
        with patch.object(user_settings, "save") as save, \
                patch.object(module, "start_husk_server", create=True), \
                patch.object(module, "stop_husk_server", create=True):
            panel = module.build_gui_panel(None)
            box = next(b for b in panel.findChildren(QCheckBox) if b.text().startswith(label))
            box.setChecked(not original)
        save.assert_called_once_with({key: not original})
        assert getattr(config, key) is (not original)
    finally:
        setattr(config, key, original)


def test_screen_tab_has_no_second_quiet_mode_switch(app):
    panel = _load_plugin("screen_lookout").build_gui_panel(None)
    assert not [b for b in panel.findChildren(QCheckBox) if "quiet" in b.text().lower()]


def test_changing_coding_buddy_port_moves_the_listener(app):
    module = _load_plugin("code_husk_mode")
    original = config.HUSK_PORT
    try:
        with patch.object(user_settings, "save"), \
                patch.object(module, "stop_husk_server") as stop, \
                patch.object(module, "start_husk_server") as start:
            module._http_server = object()  # listening
            panel = module.build_gui_panel(None)
            panel.findChild(QSpinBox).setValue(original + 1)
        stop.assert_called_once_with()
        start.assert_called_once_with()
        assert f":{original + 1}/" in module.get_claude_code_hook_snippet()
    finally:
        module._http_server = None
        config.HUSK_PORT = original


def test_settings_apply_saves_only_what_changed(app):
    with patch.object(widgets, "save_overlay_settings"), \
            patch.object(overlay.ConfigDialog, "_queue_assistant_apply"):
        companion = overlay.CompanionWindow(dict(overlay.DEFAULT_OVERLAY_SETTINGS), overlay.Bridge())
        dialog = overlay.ConfigDialog(companion)
    original = (config.QUIET_MODE, config.WEATHER_UNITS)
    try:
        config.QUIET_MODE = not original[0]  # Quiet mode toggled by its hotkey while Settings is open
        dialog.weather_units_combo.setCurrentIndex(
            dialog.weather_units_combo.findData("metric" if original[1] != "metric" else "imperial"))
        with patch.object(user_settings, "save") as save:
            dialog._apply_assistant_live()
        assert save.call_args.args[0] == {"WEATHER_UNITS": dialog.weather_units_combo.currentData()}
        assert config.QUIET_MODE is (not original[0])  # not put back by the stale checkbox
    finally:
        config.QUIET_MODE, config.WEATHER_UNITS = original
        dialog.close()
        companion.close()
