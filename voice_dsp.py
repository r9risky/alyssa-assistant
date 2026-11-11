"""Capri-inspired Voice DSP Effects (Pitch, Speed, Robot, Glitch, Voice Layers) & Typing Blip."""

import io
import os
import tempfile
import threading
import wave

import numpy as np

from config import AUDIO_SETTINGS as config


def apply_speed_factor(samples: np.ndarray, speed: float) -> np.ndarray:
    """Resample 1D float32 audio array by `speed` factor (0.5x to 2.0x)."""
    speed = max(0.5, min(2.0, float(speed)))
    if abs(speed - 1.0) < 1e-3 or samples.size < 4:
        return samples
    new_len = max(1, int(round(samples.size / speed)))
    idx = np.linspace(0, samples.size - 1, new_len, dtype=np.float32)
    return np.interp(idx, np.arange(samples.size, dtype=np.float32), samples).astype(np.float32)


def apply_pitch_shift(samples: np.ndarray, semitones: float, window_size: int = 1024) -> np.ndarray:
    """Shift pitch by `semitones` (-12 to +12) while keeping original duration via windowed overlap-add."""
    semitones = max(-12.0, min(12.0, float(semitones)))
    if abs(semitones) < 1e-2 or samples.size < 64:
        return samples
    factor = float(2.0 ** (semitones / 12.0))
    n = samples.size
    resampled = apply_speed_factor(samples, factor)
    if resampled.size == n:
        return resampled
    # Stretch/compress back to original length n using overlap-add chunks
    win = max(64, min(window_size, n // 2))
    hop_out = max(16, win // 2)
    out = np.zeros(n, dtype=np.float32)
    weight = np.zeros(n, dtype=np.float32)
    hann = np.hanning(win).astype(np.float32)

    out_pos = 0
    while out_pos + win <= n:
        in_pos = int(round((out_pos / max(1, n - win)) * max(0, resampled.size - win)))
        chunk = resampled[in_pos : in_pos + win]
        if chunk.size < win:
            padded = np.zeros(win, dtype=np.float32)
            padded[: chunk.size] = chunk
            chunk = padded
        out[out_pos : out_pos + win] += chunk * hann
        weight[out_pos : out_pos + win] += hann
        out_pos += hop_out

    mask = weight > 1e-4
    out[mask] /= weight[mask]
    if not np.all(mask):
        idx = np.linspace(0, resampled.size - 1, n, dtype=np.float32)
        fallback = np.interp(idx, np.arange(resampled.size, dtype=np.float32), resampled).astype(np.float32)
        out[~mask] = fallback[~mask]
    return out


def apply_robot_effect(samples: np.ndarray, sample_rate: int = 24000, amount: int = 0) -> np.ndarray:
    """Apply cyberpunk talk-box / ring-modulator robot effect (0 to 100%)."""
    amt = max(0, min(100, int(amount)))
    if amt <= 0 or samples.size == 0:
        return samples
    mix = amt / 100.0
    t = np.arange(samples.size, dtype=np.float32) / float(max(1, sample_rate))
    # Dual carrier wave (95 Hz + 190 Hz harmonic) + short metallic comb reflection
    carrier = 0.65 * np.sin(2.0 * np.pi * 95.0 * t) + 0.35 * np.sin(2.0 * np.pi * 190.0 * t)
    modulated = samples * carrier
    delay = max(1, int(sample_rate * 0.0045))  # ~4.5ms metallic comb delay
    if samples.size > delay:
        comb = np.copy(modulated)
        comb[delay:] += 0.45 * modulated[:-delay]
        modulated = comb
    return ((1.0 - mix) * samples + mix * modulated).astype(np.float32)


def apply_glitch_effect(samples: np.ndarray, sample_rate: int = 24000, amount: int = 0) -> np.ndarray:
    """Apply cyberpunk bitcrush + deterministic micro-stutter glitch effect (0 to 100%)."""
    amt = max(0, min(100, int(amount)))
    if amt <= 0 or samples.size == 0:
        return samples
    mix = amt / 100.0
    out = np.copy(samples)
    # 1. Bitcrush quantization
    levels = max(8.0, 128.0 * (1.0 - 0.85 * mix))
    crushed = np.round(out * levels) / levels
    out = (1.0 - 0.5 * mix) * out + (0.5 * mix) * crushed

    # 2. Periodic micro-stutter slice repeat
    slice_len = max(64, int(sample_rate * 0.028))  # 28ms slice
    stride = max(slice_len * 3, int(sample_rate * max(0.12, 0.45 - 0.30 * mix)))
    pos = stride
    while pos + slice_len * 2 <= out.size:
        out[pos + slice_len : pos + slice_len * 2] = out[pos : pos + slice_len]
        pos += stride
    return out.astype(np.float32)


def apply_voice_layers(
    samples: np.ndarray,
    layer1_semitones: float = -3.0,
    layer2_semitones: float = 4.0,
) -> np.ndarray:
    """Mix two pitch-shifted chorus/harmony layers underneath the main voice."""
    if samples.size < 64:
        return samples
    l1 = apply_pitch_shift(samples, layer1_semitones)
    l2 = apply_pitch_shift(samples, layer2_semitones)
    mixed = 0.72 * samples + 0.20 * l1 + 0.18 * l2
    return np.clip(mixed, -1.0, 1.0).astype(np.float32)


def process_samples(samples: np.ndarray, sample_rate: int = 24000) -> np.ndarray:
    """Apply all enabled config.VOICE_* DSP effects to normalized float32 samples (-1.0..1.0)."""
    if not getattr(config, "VOICE_DSP_ENABLED", False) or samples.size == 0:
        return samples
    out = samples.astype(np.float32)
    pitch = float(getattr(config, "VOICE_PITCH_SEMITONES", 0.0) or 0.0)
    if abs(pitch) >= 0.05:
        out = apply_pitch_shift(out, pitch)
    speed = float(getattr(config, "VOICE_SPEED_FACTOR", 1.0) or 1.0)
    if abs(speed - 1.0) >= 0.02:
        out = apply_speed_factor(out, speed)
    if getattr(config, "VOICE_LAYERS_ENABLED", False):
        l1 = float(getattr(config, "VOICE_LAYER_1_PITCH", -3.0))
        l2 = float(getattr(config, "VOICE_LAYER_2_PITCH", 4.0))
        out = apply_voice_layers(out, l1, l2)
    robot = int(getattr(config, "VOICE_ROBOT_AMOUNT", 0) or 0)
    if robot > 0:
        out = apply_robot_effect(out, sample_rate=sample_rate, amount=robot)
    glitch = int(getattr(config, "VOICE_GLITCH_AMOUNT", 0) or 0)
    if glitch > 0:
        out = apply_glitch_effect(out, sample_rate=sample_rate, amount=glitch)
    return np.clip(out, -1.0, 1.0).astype(np.float32)


def process_pcm16_mono(pcm_bytes: bytes, sample_rate: int = 24000) -> bytes:
    """Process 16-bit signed mono PCM bytes through the Voice DSP chain."""
    if not pcm_bytes or not getattr(config, "VOICE_DSP_ENABLED", False):
        return pcm_bytes
    usable = len(pcm_bytes) - (len(pcm_bytes) % 2)
    if usable <= 0:
        return pcm_bytes
    samples = np.frombuffer(pcm_bytes[:usable], dtype=np.int16).astype(np.float32) / 32768.0
    processed = process_samples(samples, sample_rate=sample_rate)
    int_samples = np.clip(processed * 32767.0, -32768, 32767).astype(np.int16)
    return int_samples.tobytes()


def synthesize_typing_blip(
    sample_rate: int = 24000,
    duration_ms: int = 18,
    freq_hz: float = 880.0,
) -> bytes:
    """Generate a subtle cyberpunk UI typing blip as 16-bit mono PCM bytes."""
    n = max(1, int(sample_rate * duration_ms / 1000.0))
    t = np.arange(n, dtype=np.float32) / float(sample_rate)
    envelope = np.exp(-t * 140.0)
    wave_pcm = 0.18 * np.sin(2.0 * np.pi * freq_hz * t) * envelope
    return np.clip(wave_pcm * 32767.0, -32768, 32767).astype(np.int16).tobytes()


def process_audio_file(input_path: str) -> str | None:
    """Decode input_path, apply voice DSP effects (pitch, speed, robot, glitch, layers),
    and write to a temporary WAV file. Returns the path to the temp WAV, or None if DSP is disabled or fails."""
    if not getattr(config, "VOICE_DSP_ENABLED", False):
        return None
    if not input_path or not os.path.isfile(input_path):
        return None

    try:
        sample_rate = 24000
        samples = None

        # 1. Try decoding via PyAV first (handles mp3, wav, ogg, etc.)
        try:
            import av
            container = av.open(input_path)
            resampler = av.AudioResampler(format="s16", layout="mono", rate=sample_rate)
            frames = []
            for frame in container.decode(audio=0):
                for r in resampler.resample(frame):
                    frames.append(r.to_ndarray().flatten())
            container.close()
            if frames:
                raw_int16 = np.concatenate(frames)
                samples = raw_int16.astype(np.float32) / 32768.0
        except Exception:
            samples = None

        # 2. Fallback for .wav if PyAV is unavailable or errored
        if samples is None and input_path.lower().endswith(".wav"):
            try:
                with wave.open(input_path, "rb") as wf:
                    sr = wf.getframerate()
                    ch = wf.getnchannels()
                    sw = wf.getsampwidth()
                    raw = wf.readframes(wf.getnframes())
                    if sw == 2 and raw:
                        arr = np.frombuffer(raw, dtype=np.int16)
                        if ch > 1:
                            arr = arr.reshape(-1, ch).mean(axis=1).astype(np.int16)
                        samples = arr.astype(np.float32) / 32768.0
                        sample_rate = sr
            except Exception:
                samples = None

        if samples is None or samples.size == 0:
            return None

        # Process through DSP chain
        processed = process_samples(samples, sample_rate=sample_rate)
        int_samples = np.clip(processed * 32767.0, -32768, 32767).astype(np.int16)

        # Write to temporary WAV
        temp_wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        temp_wav_path = temp_wav.name
        temp_wav.close()

        with wave.open(temp_wav_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(int_samples.tobytes())

        return temp_wav_path
    except Exception as exc:
        print(f"(voice DSP process_audio_file failed: {exc})")
        return None


_typing_blip_sound = None
_typing_blip_lock = threading.Lock()


def play_typing_blip():
    """Play subtle typing blip sound if TYPING_SOUND_ENABLED is True."""
    if not getattr(config, "TYPING_SOUND_ENABLED", False):
        return
    global _typing_blip_sound
    try:
        import pygame
        if not pygame.mixer.get_init():
            try:
                import voice_playback
                voice_playback._ensure_mixer()
            except Exception:
                pygame.mixer.init(frequency=24000, size=-16, channels=1, buffer=512)
        if _typing_blip_sound is None:
            with _typing_blip_lock:
                if _typing_blip_sound is None:
                    raw_pcm = synthesize_typing_blip(sample_rate=24000, duration_ms=18, freq_hz=880.0)
                    bio = io.BytesIO()
                    with wave.open(bio, "wb") as wf:
                        wf.setnchannels(1)
                        wf.setsampwidth(2)
                        wf.setframerate(24000)
                        wf.writeframes(raw_pcm)
                    bio.seek(0)
                    _typing_blip_sound = pygame.mixer.Sound(bio)
                    _typing_blip_sound.set_volume(0.35)
        if _typing_blip_sound is not None:
            _typing_blip_sound.play()
    except Exception:
        pass
