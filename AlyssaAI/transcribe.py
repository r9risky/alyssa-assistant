"""
Speech-to-text module using faster-whisper.
"""

import base64
import difflib
import importlib
import json
import os
import queue
import re
import threading
import time
from urllib.parse import urlencode

from faster_whisper import WhisperModel

try:
    from websockets.sync.client import connect as websocket_connect
except ImportError:  # Existing installs keep working until requirements are refreshed.
    websocket_connect = None

from config import AUDIO_SETTINGS as config
import telemetry

_model = None
_model_lock = threading.RLock()
_model_is_on_gpu = False
_model_device = None
_model_compute_type = None
_gpu_fallback_reason = None
_dll_diagnostics = []
_streamed_transcript = None
_streamed_transcript_lock = threading.Lock()


class _RealtimeSTT:
    """One persistent ElevenLabs Scribe socket shared across microphone turns."""

    def __init__(self):
        self._connection = None
        self._connection_lock = threading.Lock()
        self._results = queue.Queue()
        self._partial = ""

    def _connect(self):
        if self._connection is not None:
            return self._connection
        api_key = getattr(config, "ELEVENLABS_API_KEY", "")
        if not api_key or websocket_connect is None:
            raise RuntimeError("realtime STT is not configured")
        params = urlencode(
            {
                "model_id": getattr(config, "STT_REALTIME_MODEL", "scribe_v2_realtime"),
                "audio_format": f"pcm_{config.SAMPLE_RATE}",
                "language_code": getattr(config, "STT_LANGUAGE", "en"),
                "commit_strategy": "manual",
            }
        )
        url = f"wss://api.elevenlabs.io/v1/speech-to-text/realtime?{params}"
        with self._connection_lock:
            if self._connection is None:
                self._connection = websocket_connect(
                    url,
                    additional_headers={"xi-api-key": api_key},
                    open_timeout=5,
                    close_timeout=1,
                )
                threading.Thread(target=self._receive, daemon=True).start()
        return self._connection

    def _receive(self):
        connection = self._connection
        try:
            for raw in connection:
                event = json.loads(raw)
                kind = event.get("message_type")
                if kind == "partial_transcript":
                    self._partial = event.get("text", "")
                    if getattr(config, "DEBUG_PRINT_TRANSCRIPTS", True) and self._partial:
                        print(f"(STT partial: {self._partial!r})")
                elif kind in ("committed_transcript", "final_transcript"):
                    self._results.put(event.get("text", ""))
                elif kind and (kind.endswith("error") or kind in {"error", "rate_limited"}):
                    self._results.put(RuntimeError(event.get("error") or kind))
        except Exception as exc:
            self._results.put(exc)
        finally:
            with self._connection_lock:
                if self._connection is connection:
                    self._connection = None

    def begin_turn(self):
        while True:
            try:
                self._results.get_nowait()
            except queue.Empty:
                break
        self._partial = ""
        self._connect()

    def send(self, pcm_bytes: bytes):
        self._connect().send(
            json.dumps(
                {
                    "message_type": "input_audio_chunk",
                    "audio_base_64": base64.b64encode(pcm_bytes).decode("ascii"),
                }
            )
        )

    def finish(self) -> str:
        # Commit with 100 ms of silence so even very short commands finalize.
        silence = bytes(int(config.SAMPLE_RATE * 0.1) * 2)
        self._connect().send(
            json.dumps(
                {
                    "message_type": "input_audio_chunk",
                    "audio_base_64": base64.b64encode(silence).decode("ascii"),
                    "commit": True,
                }
            )
        )
        result = self._results.get(
            timeout=max(0.1, float(getattr(config, "STT_FINAL_TIMEOUT_SECONDS", 2.0)))
        )
        if isinstance(result, Exception):
            raise result
        return _apply_vocabulary_corrections(result.strip())

    def disconnect(self):
        with self._connection_lock:
            connection, self._connection = self._connection, None
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


_realtime_stt = _RealtimeSTT()


def start_streaming():
    """Return the pre-warmed realtime STT stream, or None for local Whisper."""
    provider = str(getattr(config, "STT_PROVIDER", "local")).lower()
    enabled = provider == "elevenlabs_realtime" or (
        provider == "auto" and bool(getattr(config, "ELEVENLABS_API_KEY", ""))
    )
    if not enabled or websocket_connect is None:
        return None
    try:
        _realtime_stt.begin_turn()
        return _realtime_stt
    except Exception as exc:
        print(f"(realtime STT unavailable, using local Whisper: {exc})")
        _realtime_stt.disconnect()
        return None


def cache_streaming_transcript(text: str) -> None:
    global _streamed_transcript
    with _streamed_transcript_lock:
        _streamed_transcript = text or None


def _consume_streaming_transcript():
    global _streamed_transcript
    with _streamed_transcript_lock:
        text, _streamed_transcript = _streamed_transcript, None
    return text


def _add_nvidia_dll_dirs():
    """Adds pip-installed NVIDIA DLL directories to DLL search path on Windows."""
    global _dll_diagnostics
    _dll_diagnostics = []
    if os.name != "nt":
        _dll_diagnostics.append("not on Windows - this step is a no-op here (Linux/Mac find CUDA libs the normal way).")
        return
    pkgs = [
        ("nvidia.cublas", "nvidia-cublas-cu12"),
        ("nvidia.cudnn", "nvidia-cudnn-cu12"),
        ("nvidia.cuda_runtime", "nvidia-cuda-runtime-cu12"),
    ]
    for pkg_name, pip_name in pkgs:
        try:
            pkg = importlib.import_module(pkg_name)
        except Exception as e:
            _dll_diagnostics.append(
                f"{pkg_name}: couldn't import it ({e}) - the pip package for it "
                f"isn't installed in this environment. Run: pip install {pip_name}"
            )
            continue
        # These nvidia-*-cu12 wheels ship as PEP 420 namespace packages (no
        # __init__.py), so pkg.__file__ is None - use __path__ instead.
        pkg_dirs = []
        if getattr(pkg, "__file__", None):
            pkg_dirs.append(os.path.dirname(pkg.__file__))
        elif getattr(pkg, "__path__", None):
            pkg_dirs.extend(list(pkg.__path__))
        if not pkg_dirs:
            _dll_diagnostics.append(
                f"{pkg_name}: imported fine, but it has neither __file__ nor "
                "__path__ so its install folder can't be located - the pip "
                "package layout may have changed."
            )
            continue
        bin_dir = next((d for d in (os.path.join(p, "bin") for p in pkg_dirs) if os.path.isdir(d)), None)
        if bin_dir is None:
            _dll_diagnostics.append(
                f"{pkg_name}: imported from {pkg_dirs}, but there's no 'bin' "
                "subfolder in any of those - the DLLs aren't where this "
                "expects them. The pip package layout may have changed."
            )
            continue
        try:
            os.add_dll_directory(bin_dir)  # Python 3.8+, the actually-reliable way
            _dll_diagnostics.append(f"{pkg_name}: found and added DLL search path {bin_dir}")
        except (OSError, AttributeError) as e:
            _dll_diagnostics.append(
                f"{pkg_name}: found {bin_dir}, but os.add_dll_directory() itself "
                f"failed ({e})"
            )
        # Belt-and-suspenders for anything still using PATH search instead
        # of add_dll_directory.
        if bin_dir not in os.environ.get("PATH", ""):
            os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")


_add_nvidia_dll_dirs()  # once at import time, before anything touches ctranslate2/CUDA
if any("couldn't import it" in line or "no 'bin'" in line or "failed (" in line for line in _dll_diagnostics):
    # Only print if something actually looks wrong.
    print("[transcribe] NVIDIA DLL setup check found a problem:")
    for line in _dll_diagnostics:
        print(f"[transcribe]   {line}")


def _resolve_device_and_compute_type():
    """Works out (device, compute_type) to hand to WhisperModel from
    config.WHISPER_DEVICE/WHISPER_COMPUTE_TYPE (default "auto"). Detects an
    NVIDIA GPU via ctranslate2 directly, no torch dependency needed."""
    device = str(getattr(config, "WHISPER_DEVICE", "auto") or "auto").lower()
    compute_type = str(getattr(config, "WHISPER_COMPUTE_TYPE", "auto") or "auto").lower()

    if device == "auto":
        try:
            import ctranslate2
            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            device = "cpu"  # no CUDA build / no GPU / detection failed - CPU always works
        print(f"[transcribe] WHISPER_DEVICE=auto resolved to '{device}'")

    if compute_type == "auto":
        compute_type = "float16" if device == "cuda" else "int8"

    return device, compute_type


def _get_model():
    global _model, _model_is_on_gpu, _model_device, _model_compute_type, _gpu_fallback_reason
    # Lock rather than "check, then set": preload() runs on a background
    # thread at startup, and without this a real command arriving before
    # that finishes could race it into building a second WhisperModel.
    if _model is None:
        with _model_lock:
            if _model is None:
                print("Loading Whisper model (first run only, may take a moment)...")
                _gpu_fallback_reason = None  # clear any stale reason from a previous attempt
                device, compute_type = _resolve_device_and_compute_type()
                # 0 = auto: use most of the CPU's cores, leaving a little
                # headroom for the LLM call, GUI, etc. Only matters on CPU.
                cpu_threads = int(getattr(config, "WHISPER_CPU_THREADS", 0) or 0)
                if cpu_threads <= 0:
                    cpu_threads = max(4, (os.cpu_count() or 4) - 1)
                num_workers = max(1, int(getattr(config, "WHISPER_NUM_WORKERS", 1) or 1))
                try:
                    _model = WhisperModel(
                        config.WHISPER_MODEL_SIZE,
                        device=device,
                        compute_type=compute_type,
                        cpu_threads=cpu_threads,
                        num_workers=num_workers,
                    )
                    _model_is_on_gpu = (device == "cuda")
                    _model_device = device
                    _model_compute_type = compute_type
                except Exception as e:
                    if device == "cuda":
                        # Broken/missing CUDA or cuDNN is the most common
                        # cause - fall back to CPU instead of crashing on
                        # startup. Note: ctranslate2 sometimes doesn't touch
                        # those DLLs until the first real transcription
                        # rather than here - see the retry in transcribe().
                        print(f"[transcribe] GPU load failed ({e}); falling back to CPU")
                        _gpu_fallback_reason = str(e) or e.__class__.__name__
                        _model = WhisperModel(
                            config.WHISPER_MODEL_SIZE,
                            device="cpu",
                            compute_type="int8",
                            cpu_threads=cpu_threads,
                            num_workers=num_workers,
                        )
                        _model_is_on_gpu = False
                        _model_device = "cpu"
                        _model_compute_type = "int8"
                    else:
                        raise
                # Use the tracked globals, not the local device/compute_type
                # vars - those still hold the originally attempted values
                # even after a fallback to CPU above.
                print(f"Whisper model loaded ({config.WHISPER_MODEL_SIZE} on {_model_device}/{_model_compute_type}).")
    return _model


def _force_reload_on_cpu():
    """Drops the current (GPU) model and forces every future _get_model()
    call this run to load fresh on CPU. Used when a GPU model loads fine
    but fails on its first actual transcription (ctranslate2 sometimes only
    touches CUDA/cuBLAS/cuDNN lazily, at that point rather than construction)."""
    global _model, _model_is_on_gpu, _model_device, _model_compute_type, _gpu_fallback_reason
    with _model_lock:
        _model = None
        _model_is_on_gpu = False
        _model_device = None
        _model_compute_type = None
        _gpu_fallback_reason = "GPU failed during transcription (see console log)"
        config.WHISPER_DEVICE = "cpu"  # stop _get_model() from retrying (and failing) the GPU


def reload_model():
    """Drops whatever Whisper model is loaded so the next _get_model() call
    rebuilds from config.py's current WHISPER_MODEL_SIZE/DEVICE/COMPUTE_TYPE.

    Needed because _get_model() caches its model for the process lifetime -
    without this, changing the model/device/compute type live in Settings
    would patch config.py but have no effect until a full restart. See
    overlay.py's _apply_assistant_live(), which calls this via
    reload_model_async() below.

    Also undoes any config.WHISPER_DEVICE = "cpu" override left by
    _force_reload_on_cpu(), so choosing GPU again in Settings actually retries it."""
    global _model, _model_is_on_gpu, _model_device, _model_compute_type, _gpu_fallback_reason
    with _model_lock:
        _model = None
        _model_is_on_gpu = False
        _model_device = None
        _model_compute_type = None
        _gpu_fallback_reason = None


def reload_model_async():
    """Same as reload_model(), but also starts loading the new model on a
    background thread, so the next command doesn't pay the load time inline.
    Never blocks - safe to call from the GUI thread."""
    def reload_and_preload():
        reload_model()
        _get_model()

    threading.Thread(target=reload_and_preload, daemon=True).start()


def preload():
    """Loads the Whisper model now instead of lazily on first use. Call on
    a background thread at startup (see main.py) so it's already warm by
    your first command. Safe to call more than once - only the first load does anything."""
    if start_streaming() is None:
        _get_model()


def get_engine_status() -> str:
    """Human-readable description of the speech-recognition engine actually
    in use: model size, device, precision. Reflects what's really running,
    since WHISPER_DEVICE/COMPUTE_TYPE can be "auto" and a GPU load can
    silently fall back to CPU. Safe to call before the model has loaded."""
    model_size = getattr(config, "WHISPER_MODEL_SIZE", "?")

    if _model is None:
        # Not loaded yet - report what it would resolve to without triggering the load.
        device, compute_type = _resolve_device_and_compute_type()
        where = "GPU (CUDA)" if device == "cuda" else "CPU"
        return (
            f"Whisper model '{model_size}' hasn't loaded yet - based on "
            f"current settings it will run on the {where} using {compute_type} "
            "precision once needed."
        )

    where = "GPU (CUDA)" if _model_is_on_gpu else "CPU"
    base = (
        f"Whisper model '{model_size}' is loaded and running on the "
        f"{where} using {_model_compute_type} precision."
    )
    if not _model_is_on_gpu and _gpu_fallback_reason:
        # Distinguishes "configured for CPU" from "GPU was tried and failed".
        base += f" (GPU was tried and failed, so this fell back to CPU: {_gpu_fallback_reason})"
        # Point at the specific broken nvidia-*-cu12 package, if that's the cause.
        broken = [
            line for line in _dll_diagnostics
            if "couldn't import it" in line or "no 'bin'" in line or "failed (" in line
        ]
        if broken:
            base += " Likely cause: " + " | ".join(broken)
    return base


def _apply_vocabulary_corrections(text: str) -> str:
    """Fixes known mishearings (game names, app names, etc.) using
    config.py's VOCABULARY_CORRECTIONS list. Two passes per entry:
      1. Exact substring match against the listed mishearings.
      2. Fuzzy match: slides a word-window roughly the correct phrase's
         phrase across the text and swaps in the correct phrase wherever it's
         a close match - this catches mishearings you haven't listed yet."""
    corrections = getattr(config, "VOCABULARY_CORRECTIONS", [])

    for correct, mishearings in corrections:
        for wrong in mishearings:
            text = re.sub(re.escape(wrong), correct, text, flags=re.IGNORECASE)

        if correct.lower() in text.lower():
            continue  # already correct (or just fixed above) - don't run fuzzy pass over it

        target_len = len(correct.split())
        words = text.split()
        result = []
        i = 0
        while i < len(words):
            matched = False
            for wlen in {target_len - 1, target_len, target_len + 1, target_len + 2}:
                if wlen < 1 or i + wlen > len(words):
                    continue
                candidate = " ".join(words[i : i + wlen])
                ratio = difflib.SequenceMatcher(
                    None, candidate.lower(), correct.lower()
                ).ratio()
                if ratio >= 0.72:
                    result.append(correct)
                    i += wlen
                    matched = True
                    break
            if not matched:
                result.append(words[i])
                i += 1
        text = " ".join(result)

    return text


def transcribe(audio) -> str:
    _t0 = time.time()
    result_text = _consume_streaming_transcript()
    if result_text is None:
        result_text = _transcribe_impl(audio)
    telemetry.log(f"[timing] transcribe: {time.time() - _t0:.2f}s")
    return result_text


def _transcribe_impl(audio) -> str:
    # Keep the cached model and its device metadata on the same generation
    # until inference and any GPU fallback finish. Settings reloads wait here.
    with _model_lock:
        return _transcribe_impl_locked(audio)


def _transcribe_impl_locked(audio) -> str:
    model = _get_model()
    try:
        segments, _ = model.transcribe(
            audio,
            language="en",
            beam_size=1,  # greedy on the first pass - fast; low-confidence segments
                          # automatically retry at higher quality via temperature below
            temperature=(0.0, 0.2, 0.4, 0.6, 0.8, 1.0),
            compression_ratio_threshold=2.4,
            # faster-whisper's default. Was tried at a stricter -0.85 so
            # borderline mishearings retry, but on always-listening mic
            # clips (which often have room noise) that tripped the retry
            # path constantly - a much bigger speed cost than model size.
            log_prob_threshold=getattr(config, "WHISPER_LOG_PROB_THRESHOLD", -1.0),
            # Stops one uncertain segment from dragging down the next one's
            # decoding (each segment normally biases the one after it) - no
            # speed cost, avoids an error-cascade on accented speech.
            condition_on_previous_text=False,
            # Trims silence/noise before decoding - usually a wash or slight
            # speedup, and stops dead air from getting hallucinated into junk text.
            vad_filter=True,
            initial_prompt=getattr(config, "WHISPER_INITIAL_PROMPT", None),
        )
        segments = list(segments)  # force evaluation now, inside the try - faster-whisper
                                    # decodes lazily, so a GPU DLL failure can surface here
    except Exception as e:
        if _model_is_on_gpu:
            # Likely a missing/broken CUDA, cuBLAS, or cuDNN DLL -
            # ctranslate2 often only touches those on the first real
            # transcription rather than at load time. Recover by reloading
            # on CPU and retrying, instead of taking down the listening loop.
            print(f"[transcribe] GPU transcription failed ({e}); reloading on CPU and retrying")
            for line in _dll_diagnostics:
                print(f"[transcribe]   {line}")
            if not any(
                "couldn't import it" in line or "no 'bin'" in line or "failed (" in line
                for line in _dll_diagnostics
            ):
                # All 3 packages look fine, so it's likely a CUDA-major-
                # version mismatch: cublas64_12.dll needs CUDA 12
                # specifically.
                print(
                    "[transcribe]   All 3 nvidia-*-cu12 packages look correctly "
                    "installed, so this is likely a CUDA version mismatch, not a "
                    "missing package - run `nvidia-smi` and check the driver "
                    "supports CUDA 12.x, and `pip show nvidia-cublas-cu12` to "
                    "confirm a 12.x build is actually installed (not an older "
                    "CUDA-11 wheel left over from a previous install)."
                )
            _force_reload_on_cpu()
            model = _get_model()
            segments, _ = model.transcribe(
                audio,
                language="en",
                beam_size=1,
                temperature=(0.0, 0.2, 0.4, 0.6, 0.8, 1.0),
                compression_ratio_threshold=2.4,
                log_prob_threshold=getattr(config, "WHISPER_LOG_PROB_THRESHOLD", -1.0),
                condition_on_previous_text=False,
                vad_filter=True,
                initial_prompt=getattr(config, "WHISPER_INITIAL_PROMPT", None),
            )
        else:
            raise

    # Whisper is prone to hallucinating short filler words ("You", "Bye",
    # "Thanks for watching") on mostly-silent/noisy audio - drop segments
    # that look like this rather than treating them as a real command.
    kept = []
    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue
        if segment.no_speech_prob > 0.6:
            continue  # model itself thinks this was probably non-speech
        if segment.avg_logprob < -1.0:
            continue  # very low confidence in what it transcribed
        kept.append(text)

    result = " ".join(kept).strip()
    return _apply_vocabulary_corrections(result)
