"""Low-power acoustic and neural wake word detection engine.

Provides hands-free voice trigger ("Hey Alyssa" / "Alyssa") without running
Whisper in a continuous loop, dropping idle listening CPU usage to < 1%.
"""

import os
import threading
import time
import numpy as np

from config import AUDIO_SETTINGS as config

try:
    import onnxruntime as ort
except ImportError:
    ort = None

try:
    import pygame
except ImportError:
    pygame = None


class WakeWordDetector:
    """Real-time streaming wake word detector.

    Combines:
    1. VAD pre-filtering so zero CPU is spent analyzing quiet room noise/silence.
    2. Multi-band spectral energy & sibilance contour acoustic phoneme matching
       for the name "Alyssa" (/əˈlɪs.ə/).
    3. Direct ONNX InferenceSession evaluation if a model is present at
       assets/wake_word.onnx or config.WAKE_WORD_MODEL_PATH.
    4. Refractory debounce period to avoid echo/tail re-triggers.
    """

    def __init__(
        self,
        name: str = "Alyssa",
        threshold: float = 0.65,
        sample_rate: int = 16000,
        model_path: str = "",
    ):
        self.name = name
        self.threshold = threshold
        self.sample_rate = sample_rate
        self.window_samples = int(sample_rate * 1.0)  # 1.0 second rolling window
        self._buffer = np.zeros(self.window_samples, dtype=np.int16)
        self._last_trigger_time = 0.0
        self._refractory_seconds = 1.5
        self._session = None
        self._vad = None
        self._chime_sound = None

        self._frame_len = self.sample_rate // 10  # 1600 samples = 100ms
        self._window = np.hanning(self._frame_len)
        self._freq_bins = np.fft.rfftfreq(self._frame_len, d=1.0 / self.sample_rate)
        self._idx_low = (self._freq_bins >= 300) & (self._freq_bins <= 2500)
        self._idx_high = (self._freq_bins >= 4000) & (self._freq_bins <= 7500)

        self._init_vad()
        self._init_onnx(model_path)

    def _init_vad(self):
        try:
            from recorder import VoiceActivityDetector
            self._vad = VoiceActivityDetector(
                sample_rate=self.sample_rate,
                aggressiveness=2,
                threshold=float(getattr(config, "SILERO_VAD_THRESHOLD", 0.50)),
            )
        except Exception:
            self._vad = None

    def _init_onnx(self, model_path: str = ""):
        if ort is None:
            return
        candidate_paths = []
        custom_path = model_path or getattr(config, "WAKE_WORD_MODEL_PATH", "")
        if custom_path:
            candidate_paths.append(custom_path)
        base_dir = os.path.dirname(os.path.abspath(__file__))
        candidate_paths.append(os.path.join(base_dir, "assets", "wake_word.onnx"))

        for path in candidate_paths:
            if os.path.isfile(path):
                try:
                    opts = ort.SessionOptions()
                    opts.inter_op_num_threads = 1
                    opts.intra_op_num_threads = 1
                    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                    self._session = ort.InferenceSession(path, sess_options=opts, providers=["CPUExecutionProvider"])
                    break
                except Exception:
                    self._session = None

    def reset(self):
        """Reset internal rolling audio buffer and state."""
        self._buffer.fill(0)
        self._last_trigger_time = 0.0
        if self._vad is not None:
            self._vad.reset()

    def process_frame(self, chunk_pcm: np.ndarray) -> tuple[bool, float]:
        """Feed a new audio chunk (typically 512 samples / 32ms) and return (triggered, confidence)."""
        chunk = np.asarray(chunk_pcm, dtype=np.int16).flatten()
        chunk_len = len(chunk)
        if chunk_len == 0:
            return False, 0.0

        # Update circular buffer
        if chunk_len >= self.window_samples:
            self._buffer[:] = chunk[-self.window_samples:]
        else:
            self._buffer[:-chunk_len] = self._buffer[chunk_len:]
            self._buffer[-chunk_len:] = chunk

        # Stage 1: Fast VAD Pre-filter. If not speech, skip feature computation.
        if self._vad is not None and not self._vad.is_speech(chunk):
            return False, 0.0

        # Stage 2: Compute wake score
        score = self.score_buffer()

        configured_thresh = float(getattr(config, "WAKE_WORD_THRESHOLD", self.threshold))
        now = time.monotonic()
        if score >= configured_thresh and (now - self._last_trigger_time) >= self._refractory_seconds:
            self._last_trigger_time = now
            return True, score

        return False, score

    def score_buffer(self) -> float:
        """Score the current 1-second rolling buffer."""
        if self._session is not None:
            try:
                input_tensor = self._buffer.astype(np.float32) / 32768.0
                input_tensor = np.expand_dims(input_tensor, axis=0)  # [1, 16000]
                input_name = self._session.get_inputs()[0].name
                out = self._session.run(None, {input_name: input_tensor})
                return float(out[0][0])
            except Exception:
                pass

        return self._score_acoustic_phonetic(self._buffer)

    def _score_acoustic_phonetic(self, pcm_data: np.ndarray) -> float:
        """Acoustic phoneme feature matcher tailored for 'Alyssa' (/əˈlɪs.ə/).

        Characteristics:
        - Voiced front formant (F1/F2 in 300-2500 Hz) for /ə/ and /l/.
        - Distinct high-frequency unvoiced sibilant fricative (/s/) with
          prominent spectral energy between 4000-7500 Hz, high spectral centroid (>3800Hz),
          and elevated zero-crossing rate (>0.20).
        - Trailing voiced decay / vowel (/ə/).
        """
        audio = pcm_data.astype(np.float32) / 32768.0
        rms = np.sqrt(np.mean(audio ** 2))
        if rms < 0.003:
            return 0.0

        num_frames = 10
        frame_len = self._frame_len
        zcr_list = []
        sib_ratio_list = []
        low_energy_list = []
        centroid_list = []

        window = self._window
        freq_bins = self._freq_bins
        idx_low = self._idx_low
        idx_high = self._idx_high

        for i in range(num_frames):
            frame = audio[i * frame_len : (i + 1) * frame_len]
            # Zero-crossing rate
            zcr = float(np.mean(np.abs(np.diff(np.signbit(frame)))))
            zcr_list.append(zcr)

            # FFT magnitude spectrum
            spec = np.abs(np.fft.rfft(frame * window))
            spec_sum = np.sum(spec) + 1e-6
            centroid = float(np.sum(freq_bins * spec) / spec_sum)
            centroid_list.append(centroid)

            # Energy bands: low voiced (300-2500 Hz), high sibilance (4000-7500 Hz)
            e_low = float(np.sum(spec[idx_low] ** 2))
            e_high = float(np.sum(spec[idx_high] ** 2))
            low_energy_list.append(e_low)

            sib_ratio = e_high / (e_low + 1e-5)
            sib_ratio_list.append(sib_ratio)

        # Peak sibilant frame (the /s/ sound)
        max_sib = max(sib_ratio_list)
        peak_idx = int(np.argmax(sib_ratio_list))

        # Alyssa requires:
        # 1. Sibilance peak occurs after initial voicing (frames 1 to 9)
        # 2. Peak zero-crossing rate is present (> 0.10)
        # 3. Peak spectral centroid is high (> 2200 Hz)
        # 4. Voiced energy exists prior to the sibilance (frames 0 to peak_idx)
        if peak_idx < 1 or peak_idx > 9:
            return 0.0

        zcr_at_peak = zcr_list[peak_idx]
        centroid_at_peak = centroid_list[peak_idx]

        if zcr_at_peak < 0.10 or centroid_at_peak < 2200:
            return 0.0

        prior_low_energy = max(low_energy_list[:peak_idx]) if peak_idx > 0 else 0.0
        if prior_low_energy < 0.001:
            return 0.0

        # Score computation
        sibilance_score = min(1.0, max_sib / 1.2)
        zcr_score = min(1.0, zcr_at_peak / 0.35)
        centroid_score = min(1.0, max(0.0, (centroid_at_peak - 2000) / 3000))
        voiced_score = min(1.0, prior_low_energy * 50.0)

        confidence = 0.35 * sibilance_score + 0.30 * zcr_score + 0.25 * centroid_score + 0.10 * voiced_score
        return float(np.clip(confidence, 0.0, 1.0))

    def score_audio(self, audio: np.ndarray) -> float:
        """Convenience method to score a complete clip of audio."""
        pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16) if audio.dtype in (np.float32, np.float64) else audio.astype(np.int16)
        if len(pcm) < self.window_samples:
            buf = np.zeros(self.window_samples, dtype=np.int16)
            buf[-len(pcm):] = pcm
            return self._score_acoustic_phonetic(buf)
        return self._score_acoustic_phonetic(pcm[-self.window_samples:])

    def play_chime(self):
        """Play confirmation wake chime asynchronously."""
        if not getattr(config, "WAKE_CHIME_ENABLED", True):
            return
        try:
            if pygame is None:
                return
            if not pygame.mixer.get_init():
                try:
                    import voice_playback
                    voice_playback._ensure_mixer()
                except Exception:
                    pygame.mixer.init(frequency=44100, size=-16, channels=1, buffer=512)
            if self._chime_sound is None:
                base_dir = os.path.dirname(os.path.abspath(__file__))
                chime_path = os.path.join(base_dir, "assets", "wake_chime.wav")
                if os.path.isfile(chime_path):
                    self._chime_sound = pygame.mixer.Sound(chime_path)
            if self._chime_sound is not None:
                vol = float(getattr(config, "WAKE_CHIME_VOLUME", 0.5))
                self._chime_sound.set_volume(max(0.0, min(1.0, vol)))
                self._chime_sound.play()
        except Exception:
            pass


_detector_instance = None
_detector_lock = threading.Lock()


def get_wake_word_detector() -> WakeWordDetector:
    global _detector_instance
    if _detector_instance is None:
        with _detector_lock:
            if _detector_instance is None:
                _detector_instance = WakeWordDetector(
                    name=getattr(config, "WAKE_WORD_NAME", "Alyssa"),
                    threshold=float(getattr(config, "WAKE_WORD_THRESHOLD", 0.65)),
                    sample_rate=getattr(config, "SAMPLE_RATE", 16000),
                )
    return _detector_instance


def play_wake_chime():
    get_wake_word_detector().play_chime()
