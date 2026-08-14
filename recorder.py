"""Records a command from the microphone until you go quiet."""

import contextlib
import queue
import threading
import time
from typing import Optional

import numpy as np
import sounddevice as sd
import webrtcvad

import config
import nameutil
import transcribe

# How much longer than MAX_RECORD_SECONDS to wait before deciding the mic
# read itself has hung (device unplugged, driver stall, etc).
_WATCHDOG_GRACE_SECONDS = 5.0

# Serializes PortAudio re-init recovery across reaper threads (see _reap_and_recover).
_recovery_lock = threading.Lock()

# Counts InputStreams currently open on ANY thread (a normal listen pass, a
# barge-in listener, or a fresh pass started right after a stall). A stalled
# worker's stream doesn't decrement this until stream.read() actually
# returns, so as long as it's stuck, its stream still counts as "open."
# _reap_and_recover waits for this to hit zero before resetting PortAudio -
# see _open_stream() and the docstring below for why that matters.
_active_streams = 0
_active_streams_lock = threading.Lock()


def _configured_input_device():
    """Return the first compatible input whose name matches the setting."""
    configured = getattr(config, "MICROPHONE_DEVICE", None)
    if not configured or str(configured).lower() == "default":
        return None
    if isinstance(configured, int):
        return configured

    needle = str(configured).lower()
    for index, device in enumerate(sd.query_devices()):
        if device["max_input_channels"] <= 0 or needle not in device["name"].lower():
            continue
        try:
            sd.check_input_settings(
                device=index, channels=1, dtype="int16", samplerate=config.SAMPLE_RATE
            )
        except Exception:
            continue
        return index
    raise RuntimeError(f"No compatible microphone matching {configured!r} was found")


@contextlib.contextmanager
def _open_stream(**kwargs):
    """sd.InputStream(**kwargs), tracked so _reap_and_recover can tell
    whether any stream - not just the one that stalled - is still live."""
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

# --- Adaptive silence timeout (fast/slow talkers) ---
# See ADAPTIVE_SILENCE_* in config.py. Tracks a running words-per-second
# estimate to shrink/stretch the "pause before done" threshold per person.
# In-memory/session-only - resets to the config default on restart.
_rate_lock = threading.Lock()
_words_per_second_ema = None  # None until we have at least one real sample
_FRAME_MS = 30  # must match the frame_ms used in _record_command_blocking
_last_speech_frames = 0  # from the most recent recording; read by update_speaking_rate()


def _current_silence_seconds() -> float:
    """The silence-before-done threshold to use for the NEXT recording,
    based on the running speaking-rate estimate. Falls back to the flat
    config.SILENCE_SECONDS if adaptive timing is disabled or we don't have
    a sample yet."""
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
    """Call after transcribing a command, passing Whisper's word count, to
    refine the running speaking-rate estimate via an exponential moving
    average. Safe to call even when adaptive timing is disabled (no-op)."""
    global _words_per_second_ema

    with _rate_lock:
        speech_frames = _last_speech_frames
    if word_count <= 0 or speech_frames <= 0:
        return  # nothing usable (e.g. a hallucinated/empty transcript)

    speech_seconds = speech_frames * _FRAME_MS / 1000.0
    if speech_seconds < 0.5:
        return  # too short a sample to trust (e.g. a bare "yes")

    sample_wps = word_count / speech_seconds
    # Clamp obviously-bogus samples (VAD noise, a Whisper hallucination)
    # so they can't wildly swing the average in one shot.
    sample_wps = max(0.3, min(sample_wps, 6.0))

    alpha = getattr(config, "ADAPTIVE_SILENCE_EMA_ALPHA", 0.3)
    with _rate_lock:
        if _words_per_second_ema is None:
            _words_per_second_ema = sample_wps
        else:
            _words_per_second_ema = alpha * sample_wps + (1 - alpha) * _words_per_second_ema


def _record_command_blocking(result_q: "queue.Queue", text_queue: "Optional[queue.Queue]" = None) -> None:
    """The actual (blocking) recording work. Runs on a worker thread so the
    caller can enforce a timeout - see record_command() below. Puts an
    (audio_or_None, error_or_None) tuple on result_q when done, or never
    returns if stream.read() itself hangs.

    If text_queue is given (the desktop companion's typed-chat box), this
    checks it every ~30ms and abandons the recording pass early the moment
    a typed message shows up."""
    try:
        vad = webrtcvad.Vad(getattr(config, "VAD_AGGRESSIVENESS", 2))
        frame_ms = _FRAME_MS
        frame_size = int(config.SAMPLE_RATE * frame_ms / 1000)

        silence_seconds = _current_silence_seconds()
        silence_frames_needed = int(silence_seconds * 1000 / frame_ms)
        max_frames = int(config.MAX_RECORD_SECONDS * 1000 / frame_ms)
        # Require a short sustained run of speech frames (not just one) to
        # filter out stray noise blips (click, cough, chair creak).
        min_speech_frames = max(
            1, int(getattr(config, "MIN_SPEECH_MS", 120) / frame_ms)
        )

        if getattr(config, "DEBUG_PRINT_TRANSCRIPTS", True):
            print(f"(Pause-before-done threshold this pass: {silence_seconds:.2f}s)")
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
                    # Typed message arrived - stop now (heard_speech stays
                    # False, like an ordinary "nothing said" pass); partial
                    # audio is dropped.
                    print("Typed message arrived - pausing mic listen.")
                    break

                chunk, _ = stream.read(frame_size)
                chunk = chunk.flatten()
                frames.append(chunk)
                if streaming_stt is not None:
                    stt_frames.append(chunk)
                    if len(stt_frames) >= 4:  # 120 ms: low latency without tiny WS frames
                        try:
                            streaming_stt.send(np.concatenate(stt_frames).tobytes())
                            stt_frames.clear()
                        except Exception as e:
                            print(f"(realtime STT stream failed, using local Whisper: {e})")
                            streaming_stt.disconnect()
                            streaming_stt = None

                is_speech = vad.is_speech(chunk.tobytes(), config.SAMPLE_RATE)

                if is_speech:
                    speech_run += 1
                    silent_run = 0
                    total_speech_frames += 1
                    if speech_run >= min_speech_frames:
                        heard_speech = True
                else:
                    silent_run += 1
                    speech_run = 0

                if heard_speech and silent_run >= silence_frames_needed:
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

        if not heard_speech:
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
        vad = webrtcvad.Vad(getattr(config, "BARGE_IN_VAD_AGGRESSIVENESS", 3))
        frame_ms = _FRAME_MS
        frame_size = int(config.SAMPLE_RATE * frame_ms / 1000)
        min_speech_frames = max(
            1, int(getattr(config, "BARGE_IN_MIN_SPEECH_MS", 150) / frame_ms)
        )
        silence_seconds = _current_silence_seconds()
        silence_frames_needed = int(silence_seconds * 1000 / frame_ms)
        max_frames = int(config.MAX_RECORD_SECONDS * 1000 / frame_ms)

        with _open_stream(
            samplerate=config.SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=frame_size,
        ) as stream:
            # Attempt loop: with BARGE_IN_REQUIRE_NAME on, a burst of speech
            # that turns out not to contain her name doesn't stop her - we
            # just go back to watching the mic for another attempt, as long
            # as she's still talking.
            while not playback_done_event.is_set():
                # Phase 1: wait for enough sustained speech to count as a
                # real attempt (not a cough, chair creak, or speaker bleed).
                frames = []
                speech_run = 0
                triggered = False
                while not playback_done_event.is_set():
                    if text_queue is not None and not text_queue.empty():
                        stop_speaking_event.set()
                        return None
                    chunk, _ = stream.read(frame_size)
                    chunk = chunk.flatten()
                    is_speech = vad.is_speech(chunk.tobytes(), config.SAMPLE_RATE)
                    if is_speech:
                        speech_run += 1
                        frames.append(chunk)
                        if speech_run >= min_speech_frames:
                            triggered = True
                            if not require_name:
                                # Old behavior: any sustained speech stops
                                # her immediately, no name check needed.
                                stop_speaking_event.set()
                                print("(Interrupted - listening...)")
                            break
                    else:
                        speech_run = 0
                        frames = []  # discard - just a blip, not the start of real speech

                if not triggered:
                    return None  # she finished speaking (or a typed msg arrived) before you cut in

                # Phase 2: keep recording the rest of what you're saying, same
                # shape as _record_command_blocking's own loop, until you pause.
                streaming_stt = transcribe.start_streaming()
                if streaming_stt is not None:
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
                    if vad.is_speech(chunk.tobytes(), config.SAMPLE_RATE):
                        silent_run = 0
                        total_speech_frames += 1
                    else:
                        silent_run += 1
                    if silent_run >= silence_frames_needed:
                        break

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

                # Name-gated: transcribe what you said and only actually
                # interrupt her if she was named. This means she keeps
                # talking a little longer than the old instant-cutoff
                # behavior (through the transcription round trip), which is
                # the necessary trade-off - there's no way to know your name
                # wasn't said without transcribing first.
                heard = transcribe.transcribe(audio)
                if nameutil.contains_name(heard):
                    stop_speaking_event.set()
                    print(f"(Interrupted - heard {config.ASSISTANT_NAME!r} - listening...)")
                    with _rate_lock:
                        _last_speech_frames = total_speech_frames
                    return audio

                print(f"(Heard {heard!r} while talking - not {config.ASSISTANT_NAME}, not stopping)")
                # Loop back and keep watching, as long as she's still talking.

            return None  # she finished talking during/after phase 2, never named
    except Exception as e:
        print(f"(barge-in listener failed, continuing without it: {e})")
        return None


def _reap_and_recover(worker: threading.Thread) -> None:
    """Runs on its own daemon thread after a stalled mic read is abandoned.

    Must NOT call sd._terminate()/_initialize() while ANY stream is open -
    not just `worker`'s own. The outer loop doesn't wait around for a stall
    to resolve; it moves on and opens fresh InputStreams for the next listen
    pass (and for barge-in) right away. If one of those later streams is
    live on another thread when this fires, racing a global PortAudio
    teardown/rebuild against it corrupts the heap and crashes the whole
    process - silently, with no traceback, often minutes after the stall
    that caused it. So this waits for the stuck worker to finish, then for
    _active_streams to actually hit zero, before touching global state. If
    the device is gone forever, this waits forever too, harmlessly, in the
    background."""
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


def record_command(text_queue: "Optional[queue.Queue]" = None) -> Optional[np.ndarray]:
    """Records audio and returns it as a float32 numpy array for Whisper,
    or None if no real speech was heard, the mic hung, or a typed message
    arrived on text_queue and interrupted the listen.

    Runs on a worker thread with a timeout, since sd.InputStream.read() can
    block forever if the audio device stalls (previously this froze Alyssa
    silently with no recovery short of a restart). If the read doesn't
    return within a grace period past MAX_RECORD_SECONDS, this gives up and
    hands control back to the outer loop; a background thread separately
    waits for the stalled read to finish before nudging PortAudio to
    re-scan devices - see _reap_and_recover() for why that can't happen
    immediately.

    text_queue, when given, is the desktop companion's typed-chat queue -
    lets a typed message interrupt an in-progress mic listen."""
    timeout = config.MAX_RECORD_SECONDS + _WATCHDOG_GRACE_SECONDS

    result_q: "queue.Queue" = queue.Queue(maxsize=1)
    worker = threading.Thread(
        target=_record_command_blocking, args=(result_q, text_queue), daemon=True
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
        # Do NOT touch PortAudio's global state here - the worker may still
        # be blocked inside stream.read(); hand recovery to a reaper thread
        # that waits for it to finish first (see _reap_and_recover).
        threading.Thread(
            target=_reap_and_recover, args=(worker,), daemon=True
        ).start()
        return None

    audio, error = result_q.get()
    if error is not None:
        raise error
    return audio
