"""Global pytest configuration and fixtures for Alyssa test suite."""

import sys
import unittest.mock as mock

# If sounddevice fails to load PortAudio (e.g. headless Linux container),
# provide a mock so tests run cleanly.
try:
    import sounddevice
except (ImportError, OSError):
    sd_mock = mock.MagicMock()
    sd_mock.query_devices.return_value = [
        {"name": "default", "max_input_channels": 1, "max_output_channels": 2}
    ]
    sys.modules["sounddevice"] = sd_mock


import pytest


@pytest.fixture(autouse=True)
def _isolated_user_settings(tmp_path, monkeypatch):
    """Settings saved during tests go to a temp settings.json, never the repo's."""
    import user_settings
    monkeypatch.setattr(user_settings, "SETTINGS_FILE", str(tmp_path / "settings.json"))


@pytest.fixture(autouse=True)
def _isolated_plugin_approvals(tmp_path, monkeypatch):
    """Keep plugin approvals out of the repo; plugins already in plugins/ are built-ins."""
    import plugin_permissions
    monkeypatch.setattr(plugin_permissions, "APPROVALS_FILE", str(tmp_path / "plugin_approvals.json"))


@pytest.fixture(autouse=True)
def _no_real_portaudio_reset(monkeypatch):
    """Mic-error tests can trigger recorder's PortAudio reset. Doing that for real on the
    test (Qt) thread broke later Qt tests on Windows CI, so it's a no-op here."""
    import recorder
    monkeypatch.setattr(recorder.sd, "_terminate", lambda: None, raising=False)
    monkeypatch.setattr(recorder.sd, "_initialize", lambda: None, raising=False)
    monkeypatch.setattr(recorder, "_consecutive_mic_errors", 0)
    monkeypatch.setattr(recorder, "_last_rescan", 0.0)
