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
