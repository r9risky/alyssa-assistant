import collections
import contextlib
import os
import queue
import sys
import threading
import time
from typing import Optional

import numpy as np
import sounddevice as sd
import webrtcvad

from config import AUDIO_SETTINGS as config
import nameutil
import transcribe
import wake_word

if getattr(sys, "frozen", False):
    _BASE_DIR = os.path.dirname(sys.executable)
else:
    _BASE_DIR = os.path.dirname(__file__)


class VoiceActivityDetector:
    """Hybrid Voice Activity Detector using Silero VAD v5 with graceful WebRTC fallback."""

    def __init__(
        self,
        sample_rate: int = 16000,
        aggressiveness: int = 2,
        threshold: float = 0.50,
    ):
        self.sample_rate = sample_rate
        self.threshold = threshold
        self.engine = "webrtc"
        self._session = None
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._sr_tensor = np.array([sample_rate], dtype=np.int64)
        self._webrtc_vad = webrtcvad.Vad(aggressiveness)

        configured_engine = str(getattr(config, "VAD_ENGINE", "auto") or "auto").strip().lower()
        if configured_engine != "webrtc":
            self._try_init_silero()

    def _try_init_silero(self):
        try:
            import onnxruntime as ort
            candidates = [
                os.path.join(_BASE_DIR, "assets", "silero_vad.onnx"),
                os.path.join(os.path.dirname(__file__), "assets", "silero_vad.onnx"),
            ]
            model_path = next((p for p in candidates if os.path.exists(p)), None)
            if model_path is not None:
                opts = ort.SessionOptions()
                opts.inter_op_num_threads = 1
                opts.intra_op_num_threads = 1
                opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                self._session = ort.InferenceSession(
                    model_path,
                    sess_options=opts,
                    providers=["CPUExecutionProvider"],
                )
                self.engine = "silero"
        except Exception:
            self.engine = "webrtc"

    def reset(self):
        self._state = np.zeros((2, 1, 128), dtype=np.float32)

    def is_speech(self, chunk_pcm: np.ndarray | bytes) -> bool:
        if self.engine == "silero" and self._session is not None:
            try:
                if isinstance(chunk_pcm, bytes):
                    samples = np.frombuffer(chunk_pcm, dtype=np.int16).astype(np.float32) / 32768.0
                else:
                    samples = (
                        chunk_pcm.astype(np.float32) / 32768.0
                        if chunk_pcm.dtype == np.int16
                        else chunk_pcm.astype(np.float32)
                    )

                n = samples.size
                if n < 512:
                    padded = np.zeros(512, dtype=np.float32)
                    padded[:n] = samples
                    input_tensor = padded[np.newaxis, :]
                else:
                    input_tensor = samples[:512][np.newaxis, :]

                out, self._state = self._session.run(
                    None,
                    {"input": input_tensor, "state": self._state, "sr": self._sr_tensor},
                )
                prob = float(out[0][0])
                thresh = float(getattr(config, "SILERO_VAD_THRESHOLD", self.threshold))
                return prob >= thresh
            except Exception:
                pass

        # WebRTC VAD fallback (requires 10/20/30ms frames of int16 PCM)
        if isinstance(chunk_pcm, np.ndarray):
            pcm_bytes = chunk_pcm.astype(np.int16).tobytes()
        else:
            pcm_bytes = chunk_pcm
        usable_bytes = min(len(pcm_bytes), 960)
        usable_bytes = usable_bytes - (usable_bytes % 320)
        if usable_bytes < 320:
            return False
        return self._webrtc_vad.is_speech(pcm_bytes[:usable_bytes], self.sample_rate)


# Extra wait past MAX_RECORD_SECONDS before treating a mic read as hung.
_WATCHDOG_GRACE_SECONDS = 5.0

# Serializes PortAudio re-init across reaper threads (see _reap_and_recover).
_recovery_lock = threading.Lock()

# Open InputStream count across all threads. A stalled worker's stream
# stays counted until stream.read() returns. _reap_and_recover waits for
# zero before resetting PortAudio.
_active_streams = 0
_active_streams_lock = threading.Lock()


# First lookup only: retry the configured mic for this long (USB/BT may
# not have re-paired yet at logon). Later disappearances fail immediately.
_STARTUP_DEVICE_WAIT_SECONDS = 20.0
_startup_device_wait_used = False


def _find_configured_device(needle):
    """First input index whose name matches *needle*. Prefers
    check_input_settings() success; falls back to the first name match if
    every variant fails (Windows lists the same device per host API)."""
    fallback = None
    for index, device in enumerate(sd.query_devices()):
        if device["max_input_channels"] <= 0 or needle not in device["name"].lower():
            continue
        if fallback is None:
            fallback = index
        try:
            sd.check_input_settings(
                device=index, channels=1, dtype="int16", samplerate=config.SAMPLE_RATE
            )
        except Exception:
            continue
        return index
    return fallback


def _configured_input_device():
    """Return the first compatible input whose name matches the setting."""
    configured = getattr(config, "MICROPHONE_DEVICE", None)
    if not configured or str(configured).lower() == "default":
        return None
    if isinstance(configured, int):
        return configured

    needle = str(configured).lower()
    index = _find_configured_device(needle)
    if index is not None:
        return index

    global _startup_device_wait_used
    if not _startup_device_wait_used:
        _startup_device_wait_used = True
        deadline = time.time() + _STARTUP_DEVICE_WAIT_SECONDS
        print(
            f"(Microphone {configured!r} not found yet - retrying for up "
            f"to {_STARTUP_DEVICE_WAIT_SECONDS:.0f}s in case it's still "
            "reconnecting after startup...)"
        )
        while time.time() < deadline:
            time.sleep(1.0)
            # Re-scan PortAudio's device list — query_devices() alone only
            # reflects devices seen at init. Safe: no InputStream open yet.
            with _recovery_lock:
                try:
                    sd._terminate()
                    sd._initialize()
                except Exception as e:
                    print(f"(device re-scan failed: {e})")
            index = _find_configured_device(needle)
            if index is not None:
                print(f"(Found microphone {configured!r} after waiting.)")
                return index
        print(
            f"(Gave up waiting for microphone {configured!r} after "
            f"{_STARTUP_DEVICE_WAIT_SECONDS:.0f}s.)"
        )

    raise RuntimeError(f"No compatible microphone matching {configured!r} was found")


@contextlib.contextmanager
def _open_stream(**kwargs):
    """sd.InputStream(**kwargs), tracked so _reap_and_recover knows if any stream is live."""
    global _active_streams
    kwargs.setdefault("device", _configured_input_device())
    with sd.InputStream(**kwargs) as stream:
        with _active_streams_lock:
            _active_streams += 1
        try:
            yield stream
        finally:
            with _active_streams_lock:
                _active_streams -= 1

# Adaptive silence (ADAPTIVE_SILENCE_* in config). Session-only WPS EMA;
# resets to the config default on restart.
_rate_lock = threading.Lock()
_words_per_second_ema = None  # None until we have at least one real sample
_FRAME_MS = 32  # 32ms matches 512 samples at 16kHz for Silero VAD v5 and WebRTC fallback
_last_speech_frames = 0  # from the most recent recording; read by update_speaking_rate()


def _current_silence_seconds() -> float:
    """Silence-before-done threshold for the next recording, from the WPS EMA.
    Falls back to config.SILENCE_SECONDS if adaptive is off or no sample yet."""
    if not getattr(config, "ADAPTIVE_SILENCE_ENABLED", True):
        return config.SILENCE_SECONDS

    with _rate_lock:
        wps = _words_per_second_ema
    if wps is None:
        return config.SILENCE_SECONDS

    slow_wps = getattr(config, "ADAPTIVE_SILENCE_SLOW_WPS", 1.3)
    fast_wps = getattr(config, "ADAPTIVE_SILENCE_FAST_WPS", 3.3)
    min_seconds = getattr(config, "ADAPTIVE_SILENCE_MIN_SECONDS", 0.45)
    max_seconds = getattr(config, "ADAPTIVE_SILENCE_MAX_SECONDS", 1.6)
    slow_value = min(config.SILENCE_SECONDS * 1.2, max_seconds)
    fast_value = min_seconds

    if wps <= slow_wps:
        return slow_value
    if wps >= fast_wps:
        return fast_value

    # Linear interpolation between the slow and fast reference points.
    t = (wps - slow_wps) / (fast_wps - slow_wps)
    return slow_value + t * (fast_value - slow_value)


def update_speaking_rate(word_count: int) -> None:
    """After transcribing, pass Whisper's word count to refine the WPS EMA.
    No-op if adaptive timing is disabled."""
    global _words_per_second_ema

    with _rate_lock:
        speech_frames = _last_speech_frames
    if word_count <= 0 or speech_frames <= 0:
        return  # nothing usable (e.g. a hallucinated/empty transcript)

    speech_seconds = speech_frames * _FRAME_MS / 1000.0
    if speech_seconds < 0.5:
        return  # too short a sample to trust (e.g. a bare "yes")

    sample_wps = word_count / speech_seconds
    # Clamp bogus samples (VAD noise / hallucination) so one shot can't swing the EMA.
    sample_wps = max(0.3, min(sample_wps, 6.0))

    alpha = getattr(config, "ADAPTIVE_SILENCE_EMA_ALPHA", 0.3)
    with _rate_lock:
        if _words_per_second_ema is None:
            _words_per_second_ema = sample_wps
        else:
            _words_per_second_ema = alpha * sample_wps + (1 - alpha) * _words_per_second_ema


def _record_command_blocking(
    result_q: "queue.Queue",
    text_queue: "Optional[queue.Queue]" = None,
    bypass_wake_word: bool = False,
) -> None:
    """Blocking record on a worker so the caller can timeout (see record_command).
    Puts (audio_or_None, error_or_None) on result_q, or never returns if stream.read() hangs.

    If text_queue is given (typed chat), abandon the pass as soon as a message arrives."""
    try:
        vad = VoiceActivityDetector(
            sample_rate=config.SAMPLE_RATE,
            aggressiveness=getattr(config, "VAD_AGGRESSIVENESS", 2),
            threshold=float(getattr(config, "SILERO_VAD_THRESHOLD", 0.50)),
        )
        frame_ms = _FRAME_MS
        frame_size = int(config.SAMPLE_RATE * frame_ms / 1000)

        silence_seconds = _current_silence_seconds()
        silence_frames_needed = int(silence_seconds * 1000 / frame_ms)
        max_frames = int(config.MAX_RECORD_SECONDS * 1000 / frame_ms)
        # Require a short run of speech frames, not one blip (click/cough).
        min_speech_frames = max(
            1, int(getattr(config, "MIN_SPEECH_MS", 120) / frame_ms)
        )
        pre_roll_ms = int(getattr(config, "PRE_ROLL_BUFFER_MS", 400))
        pre_roll_limit = max(2, int(pre_roll_ms / frame_ms))
        pre_roll_frames = collections.deque(maxlen=pre_roll_limit)

        wake_mode = str(getattr(config, "WAKE_WORD_MODE", "always_listening") or "always_listening").lower()
        use_wake_word = (not bypass_wake_word) and (wake_mode == "wake_word")
        if (not bypass_wake_word) and (wake_mode == "push_to_talk"):
            result_q.put((None, None))
            return

        ww_detector = wake_word.get_wake_word_detector() if use_wake_word else None
        if ww_detector is not None:
            ww_detector.reset()

        if getattr(config, "DEBUG_PRINT_TRANSCRIPTS", True):
            mode_str = f"Wake-word: {config.WAKE_WORD_NAME}" if use_wake_word else f"Listen mode: {wake_mode}"
            print(
                f"({mode_str} | VAD: {vad.engine.upper()} | Pause threshold: {silence_seconds:.2f}s | "
                f"Pre-roll: {pre_roll_ms}ms)"
            )
        if use_wake_word:
            print(f"Waiting for wake word ('{config.WAKE_WORD_NAME}')...")
        else:
            print("Listening for your command...")

        frames = []
        silent_run = 0
        speech_run = 0
        heard_speech = False
        total_speech_frames = 0
        streaming_stt = transcribe.start_streaming()
        stt_frames = []

        with _open_stream(
            samplerate=config.SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=frame_size,
        ) as stream:
            for _ in range(max_frames):
                if text_queue is not None and not text_queue.empty():
                    # Typed message — stop now (heard_speech stays False); drop partial audio.
                    print("Typed message arrived - pausing mic listen.")
                    break

                chunk, _ = stream.read(frame_size)
                chunk = chunk.flatten()
                is_speech = vad.is_speech(chunk)

                if not heard_speech:
                    pre_roll_frames.append(chunk)
                    if use_wake_word and ww_detector is not None:
                        triggered, score = ww_detector.process_frame(chunk)
                        if triggered:
                            heard_speech = True
                            frames = list(pre_roll_frames)
                            total_speech_frames = len(frames)
                            ww_detector.play_chime()
                            if getattr(config, "DEBUG_PRINT_TRANSCRIPTS", True):
                                print(f"(Wake word detected, confidence: {score:.2f} - recording command...)")
                            if streaming_stt is not None:
                                try:
                                    streaming_stt.send(np.concatenate(frames).tobytes())
                                except Exception as e:
                                    streaming_stt.disconnect()
                                    streaming_stt = None
                    else:
                        if is_speech:
                            speech_run += 1
                            if speech_run >= min_speech_frames:
                                heard_speech = True
                                frames = list(pre_roll_frames)
                                total_speech_frames = speech_run
                                if streaming_stt is not None:
                                    try:
                                        streaming_stt.send(np.concatenate(frames).tobytes())
                                    except Exception as e:
                                        print(f"(realtime STT stream failed, using local Whisper: {e})")
                                        streaming_stt.disconnect()
                                        streaming_stt = None
                        else:
                            speech_run = 0
                else:
                    frames.append(chunk)
                    if streaming_stt is not None:
                        stt_frames.append(chunk)
                        if len(stt_frames) >= 4:  # ~128 ms
                            try:
                                streaming_stt.send(np.concatenate(stt_frames).tobytes())
                                stt_frames.clear()
                            except Exception as e:
                                print(f"(realtime STT stream failed, using local Whisper: {e})")
                                streaming_stt.disconnect()
                                streaming_stt = None

                    if is_speech:
                        speech_run += 1
                        silent_run = 0
                        total_speech_frames += 1
                    else:
                        silent_run += 1
                        speech_run = 0

                    if silent_run >= silence_frames_needed:
                        break

        print("Done listening.")

        if streaming_stt is not None:
            try:
                if stt_frames:
                    streaming_stt.send(np.concatenate(stt_frames).tobytes())
                streamed_text = streaming_stt.finish()
                if heard_speech and streamed_text:
                    transcribe.cache_streaming_transcript(streamed_text)
            except Exception as e:
                print(f"(realtime STT finalization failed, using local Whisper: {e})")
                streaming_stt.disconnect()

        if not heard_speech or not frames:
            result_q.put((None, None))
            return

        global _last_speech_frames
        with _rate_lock:
            _last_speech_frames = total_speech_frames

        audio = np.concatenate(frames).astype(np.float32) / 32768.0
        result_q.put((audio, None))
    except Exception as e:
        result_q.put((None, e))


def listen_for_barge_in(
    stop_speaking_event: threading.Event,
    playback_done_event: threading.Event,
    text_queue: "Optional[queue.Queue]" = None,
) -> Optional[np.ndarray]:
    """Listens for speech interruptions while assistant TTS is playing."""
    if not getattr(config, "ALLOW_INTERRUPTIONS", True):
        return None

    require_name = getattr(config, "BARGE_IN_REQUIRE_NAME", True)
    global _last_speech_frames

    try:
        vad = VoiceActivityDetector(
            sample_rate=config.SAMPLE_RATE,
            aggressiveness=getattr(config, "BARGE_IN_VAD_AGGRESSIVENESS", 3),
            threshold=float(getattr(config, "SILERO_VAD_THRESHOLD", 0.55)),
        )
        frame_ms = _FRAME_MS
        frame_size = int(config.SAMPLE_RATE * frame_ms / 1000)
        min_speech_frames = max(
            1, int(getattr(config, "BARGE_IN_MIN_SPEECH_MS", 150) / frame_ms)
        )
        silence_seconds = _current_silence_seconds()
        silence_frames_needed = int(silence_seconds * 1000 / frame_ms)
        max_frames = int(config.MAX_RECORD_SECONDS * 1000 / frame_ms)
        pre_roll_ms = int(getattr(config, "PRE_ROLL_BUFFER_MS", 400))
        pre_roll_limit = max(2, int(pre_roll_ms / frame_ms))

        with _open_stream(
            samplerate=config.SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=frame_size,
        ) as stream:
            # With BARGE_IN_REQUIRE_NAME, speech without her name doesn't
            # stop her — retry while she's still talking.
            while not playback_done_event.is_set():
                # Phase 1: wait for enough sustained speech with circular pre-roll buffer.
                frames = []
                pre_roll_frames = collections.deque(maxlen=pre_roll_limit)
                speech_run = 0
                triggered = False
                vad.reset()

                while not playback_done_event.is_set():
                    if text_queue is not None and not text_queue.empty():
                        stop_speaking_event.set()
                        return None
                    chunk, _ = stream.read(frame_size)
                    chunk = chunk.flatten()
                    pre_roll_frames.append(chunk)
                    is_speech = vad.is_speech(chunk)
                    if is_speech:
                        speech_run += 1
                        if speech_run >= min_speech_frames:
                            triggered = True
                            frames = list(pre_roll_frames)
                            if not require_name:
                                # Any sustained speech stops her immediately.
                                stop_speaking_event.set()
                                print("(Interrupted - listening...)")
                            break
                    else:
                        speech_run = 0

                if not triggered:
                    return None  # she finished speaking (or a typed msg arrived) before you cut in

                # Phase 2: keep recording until pause, preserving pre-roll frames.
                streaming_stt = transcribe.start_streaming()
                if streaming_stt is not None and frames:
                    try:
                        streaming_stt.send(np.concatenate(frames).tobytes())
                    except Exception:
                        streaming_stt.disconnect()
                        streaming_stt = None
                silent_run = 0
                total_speech_frames = speech_run
                for _ in range(max_frames):
                    chunk, _ = stream.read(frame_size)
                    chunk = chunk.flatten()
                    frames.append(chunk)
                    if streaming_stt is not None:
                        try:
                            streaming_stt.send(chunk.tobytes())
                        except Exception:
                            streaming_stt.disconnect()
                            streaming_stt = None
                    if vad.is_speech(chunk):
                        silent_run = 0
                        total_speech_frames += 1
                    else:
                        silent_run += 1
                    if silent_run >= silence_frames_needed:
                        break

                if not frames:
                    continue

                audio = np.concatenate(frames).astype(np.float32) / 32768.0
                if streaming_stt is not None:
                    try:
                        streamed_text = streaming_stt.finish()
                        if streamed_text:
                            transcribe.cache_streaming_transcript(streamed_text)
                    except Exception:
                        streaming_stt.disconnect()

                if not require_name:
                    with _rate_lock:
                        _last_speech_frames = total_speech_frames
                    return audio

                # Phrase-gated: only interrupt if name + "stop" were said.
                heard = transcribe.transcribe(audio)
                if nameutil.is_stop_request(heard):
                    stop_speaking_event.set()
                    print(f"(Interrupted - heard stop request for {config.ASSISTANT_NAME!r})")
                    with _rate_lock:
                        _last_speech_frames = total_speech_frames
                    return audio

                print(f"(Heard {heard!r} while talking - not a stop request, continuing)")
                # Loop back and keep watching, as long as she's still talking.

            return None  # she finished talking without a stop request
    except Exception as e:
        print(f"(barge-in listener failed, continuing without it: {e})")
        return None


def _reap_and_recover(worker: threading.Thread) -> None:
    """After a stalled mic read is abandoned. Daemon thread.

    Must NOT call sd._terminate()/_initialize() while ANY stream is open —
    the outer loop opens fresh streams immediately. Racing PortAudio teardown
    against a live stream corrupts the heap. Wait for the worker to finish,
    then _active_streams == 0, before touching global state. If the device is
    gone forever this waits forever, harmlessly, in the background."""
    worker.join()
    while True:
        with _active_streams_lock:
            if _active_streams == 0:
                break
        time.sleep(0.2)
    with _recovery_lock:
        try:
            sd._terminate()
            sd._initialize()
        except Exception as e:
            print(f"(deferred device re-scan failed: {e})")


def record_command(
    text_queue: "Optional[queue.Queue]" = None,
    bypass_wake_word: bool = False,
) -> Optional[np.ndarray]:
    """Record audio as float32 for Whisper, or None if no speech / mic hung /
    a typed message arrived on text_queue.

    Worker-thread timeout: sd.InputStream.read() can block forever. If it
    doesn't return within MAX_RECORD_SECONDS + grace, give up; a reaper
    thread waits for the stalled read before PortAudio re-scan
    (see _reap_and_recover — must not happen while any stream is open).

    text_queue: typed-chat queue; a message interrupts an in-progress listen."""
    timeout = config.MAX_RECORD_SECONDS + _WATCHDOG_GRACE_SECONDS

    result_q: "queue.Queue" = queue.Queue(maxsize=1)
    worker = threading.Thread(
        target=_record_command_blocking,
        args=(result_q, text_queue, bypass_wake_word),
        daemon=True,
    )
    worker.start()
    worker.join(timeout)

    if worker.is_alive():
        print(
            f"WARNING: Mic read didn't return within {timeout:.0f}s - the "
            "audio device may have stalled (disconnected, reclaimed by "
            "another app, driver hiccup). Giving up on this listening pass; "
            "the stuck thread will be abandoned in the background since "
            "PortAudio gives no safe way to force it to stop mid-read. "
            "Device recovery will happen once that thread actually finishes."
        )
        # Do NOT touch PortAudio here — worker may still be in stream.read();
        # hand recovery to a reaper that waits first (see _reap_and_recover).
        threading.Thread(
            target=_reap_and_recover, args=(worker,), daemon=True
        ).start()
        return None

    audio, error = result_q.get()
    if error is not None:
        raise error
    return audio
