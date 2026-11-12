import config
import credential_store
from overlay import setup_wizard


def test_save_setup_writes_config_and_keyring(monkeypatch, tmp_path):
    path = tmp_path / "config.py"
    path.write_text(
        "LLM_PROVIDER = 'gemini'  # which brain\nTTS_PROVIDER = 'edge'\n", encoding="utf-8"
    )
    saved = {}
    monkeypatch.setattr(credential_store, "set_secret", lambda k, v: saved.update({k: v}) or True)
    for key in ("LLM_PROVIDER", "TTS_PROVIDER", "MICROPHONE_DEVICE", "GEMINI_API_KEY"):
        monkeypatch.setattr(config, key, getattr(config, key, ""))

    problems = setup_wizard.save_setup(
        {"provider": "gemini", "gemini_key": " abc123 ", "tts": "kokoro", "microphone": "USB Mic"},
        config_path=str(path),
    )

    assert problems == []
    text = path.read_text(encoding="utf-8")
    assert "LLM_PROVIDER = 'gemini'  # which brain" in text
    assert "TTS_PROVIDER = 'kokoro'" in text
    assert "MICROPHONE_DEVICE = 'USB Mic'" in text
    assert "abc123" not in text  # secrets never go into config.py
    assert saved == {"GEMINI_API_KEY": "abc123"}
    assert (config.TTS_PROVIDER, config.GEMINI_API_KEY) == ("kokoro", "abc123")


def test_save_setup_reports_keyring_failure(monkeypatch, tmp_path):
    path = tmp_path / "config.py"
    path.write_text("", encoding="utf-8")
    monkeypatch.setattr(credential_store, "set_secret", lambda k, v: False)
    for key in ("LLM_PROVIDER", "TTS_PROVIDER", "MICROPHONE_DEVICE", "GEMINI_API_KEY"):
        monkeypatch.setattr(config, key, getattr(config, key, ""))

    problems = setup_wizard.save_setup(
        {"provider": "gemini", "gemini_key": "k", "tts": "edge", "microphone": "default"},
        config_path=str(path),
    )

    assert len(problems) == 1 and "Credential Manager" in problems[0]


def test_wizard_needs_a_checked_key_for_gemini_but_not_ollama():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    wizard = setup_wizard.SetupWizard(None, {})
    brain = wizard.brain
    assert not brain.isComplete()
    brain.key.setText("abc")
    brain._key_checked("abc", True, "")
    assert brain.isComplete()
    brain.key.setText("abcd")  # editing invalidates the check
    assert not brain.isComplete()
    brain.ollama.setChecked(True)
    assert brain.isComplete()
    assert wizard.choices()["provider"] == "ollama"
    wizard.mic.stop_meter()
    wizard.deleteLater()
    app.processEvents()
