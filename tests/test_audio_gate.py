import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from audio_gate import AdaptiveSpeechGate, dbfs_int16


class AudioGateTests(unittest.TestCase):
    def test_digital_silence_is_floor(self):
        self.assertEqual(dbfs_int16(np.zeros(480, dtype=np.int16)), -120.0)

    def test_energy_fallback_detects_voice_when_webrtc_misses(self):
        gate = AdaptiveSpeechGate(
            absolute_threshold_dbfs=-50.0,
            margin_db=10.0,
            initial_noise_floor_dbfs=-60.0,
        )
        # ~-30 dBFS synthetic PCM: clearly above the fallback threshold.
        chunk = np.full(480, 1000, dtype=np.int16)
        result = gate.classify(chunk, webrtc_speech=False)
        self.assertTrue(result.energy_speech)
        self.assertTrue(result.speech)

    def test_quiet_room_noise_is_not_speech(self):
        gate = AdaptiveSpeechGate(
            absolute_threshold_dbfs=-50.0,
            margin_db=10.0,
            initial_noise_floor_dbfs=-60.0,
        )
        chunk = np.full(480, 20, dtype=np.int16)
        result = gate.classify(chunk, webrtc_speech=False)
        self.assertFalse(result.energy_speech)
        self.assertFalse(result.speech)

    def test_webrtc_detection_always_wins(self):
        gate = AdaptiveSpeechGate(enabled=False)
        chunk = np.zeros(480, dtype=np.int16)
        result = gate.classify(chunk, webrtc_speech=True)
        self.assertTrue(result.speech)
        self.assertFalse(result.energy_speech)

    def test_noise_floor_adapts_upward(self):
        gate = AdaptiveSpeechGate(
            absolute_threshold_dbfs=-50.0,
            margin_db=10.0,
            initial_noise_floor_dbfs=-70.0,
            noise_alpha=0.2,
        )
        chunk = np.full(480, 60, dtype=np.int16)
        before = gate.noise_floor_dbfs
        for _ in range(12):
            gate.classify(chunk, webrtc_speech=False)
        self.assertGreater(gate.noise_floor_dbfs, before)


if __name__ == "__main__":
    unittest.main()
