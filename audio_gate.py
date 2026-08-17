"""Pure audio-level helpers used by the microphone recorder.

This module intentionally has no sounddevice/PortAudio dependency so the speech gate
can be unit-tested without audio hardware. WebRTC VAD remains the primary detector;
the adaptive energy gate is a fallback for microphones/virtual devices whose audio
WebRTC VAD consistently classifies as non-speech.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


def dbfs_int16(chunk: np.ndarray) -> float:
    """Return RMS level in dBFS for an int16 PCM chunk.

    Digital silence returns -120 dBFS rather than negative infinity so callers can
    safely compare/log the value.
    """
    samples = np.asarray(chunk, dtype=np.float64)
    if samples.size == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(samples * samples)))
    if rms <= 0.0:
        return -120.0
    return max(-120.0, 20.0 * math.log10(rms / 32768.0))


@dataclass(frozen=True)
class SpeechGateResult:
    speech: bool
    webrtc_speech: bool
    energy_speech: bool
    level_dbfs: float
    threshold_dbfs: float
    noise_floor_dbfs: float


class AdaptiveSpeechGate:
    """Combine WebRTC VAD with a slowly adapting RMS-energy fallback.

    WebRTC VAD is excellent for ordinary microphones but can reject processed or
    virtual-device audio (SteelSeries Sonar, aggressive noise suppression, etc.).
    The fallback accepts a frame when it is both loud enough in absolute terms and
    sufficiently above the learned room/device noise floor.

    The noise floor is session-persistent when the same gate instance is reused.
    It learns only from frames that WebRTC says are non-speech, and learns slowly,
    so a person beginning to talk is detected before their voice can be absorbed
    into the floor estimate.
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        absolute_threshold_dbfs: float = -50.0,
        margin_db: float = 10.0,
        initial_noise_floor_dbfs: float = -60.0,
        noise_alpha: float = 0.03,
    ) -> None:
        self.enabled = bool(enabled)
        self.absolute_threshold_dbfs = float(absolute_threshold_dbfs)
        self.margin_db = max(0.0, float(margin_db))
        self.noise_floor_dbfs = float(initial_noise_floor_dbfs)
        self.noise_alpha = min(1.0, max(0.001, float(noise_alpha)))

    def classify(self, chunk: np.ndarray, webrtc_speech: bool) -> SpeechGateResult:
        level = dbfs_int16(chunk)
        threshold = max(
            self.absolute_threshold_dbfs,
            self.noise_floor_dbfs + self.margin_db,
        )
        energy_speech = self.enabled and level >= threshold
        speech = bool(webrtc_speech or energy_speech)

        # Learn only from frames that neither detector considers speech. This
        # prevents a quiet voice that WebRTC misses from gradually being absorbed
        # into the noise-floor estimate during a long sentence.
        if not webrtc_speech and not energy_speech and level > -119.0:
            target = min(-20.0, max(-90.0, level))
            self.noise_floor_dbfs = (
                (1.0 - self.noise_alpha) * self.noise_floor_dbfs
                + self.noise_alpha * target
            )

        return SpeechGateResult(
            speech=speech,
            webrtc_speech=bool(webrtc_speech),
            energy_speech=bool(energy_speech),
            level_dbfs=level,
            threshold_dbfs=threshold,
            noise_floor_dbfs=self.noise_floor_dbfs,
        )
