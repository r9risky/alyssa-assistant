"""Speech-to-text via faster-whisper."""

import base64
import difflib
import glob
import importlib
import io
import json
import os
import queue
import re
import threading
import time
import wave
from urllib.parse import urlencode

import numpy as np
import requests
import faster_whisper
from faster_whisper import WhisperModel

try:
    from websockets.sync.client import connect as websocket_connect
except ImportError:  # Existing installs keep working until requirements are refreshed.
    websocket_connect = None

from config import AUDIO_SETTINGS as config
import nameutil
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
_partial_transcript_hooks = []
_hooks_lock = threading.Lock()


def register_partial_transcript_hook(hook):
    """Register a callback hook for streaming or intermediate transcripts."""
    with _hooks_lock:
        if hook not in _partial_transcript_hooks:
            _partial_transcript_hooks.append(hook)


def unregister_partial_transcript_hook(hook):
    """Unregister a previously registered transcript hook."""
    with _hooks_lock:
        if hook in _partial_transcript_hooks:
            _partial_transcript_hooks.remove(hook)


def _notify_partial_transcript_hooks(text: str):
    """Dispatch transcript updates to all registered hooks."""
    if not text:
        return
    with _hooks_lock:
        hooks = list(_partial_transcript_hooks)
    for hook in hooks:
        try:
            hook(text)
        except Exception as e:
            print(f"(Partial transcript hook error: {e})")


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
                    _notify_partial_transcript_hooks(self._partial)
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
    if text:
        _notify_partial_transcript_hooks(text)


_early_transcript = None  # (thread, result list) from recorder's early transcription


def set_early_transcript(thread, result) -> None:
    """Hand over a transcription the recorder started before you finished the
    pause; the next transcribe() call waits for it instead of starting over."""
    global _early_transcript
    _early_transcript = (thread, result)


def _consume_early_transcript():
    global _early_transcript
    job, _early_transcript = _early_transcript, None
    if job is None:
        return None
    thread, result = job
    thread.join()
    return result[0] if result else None


def _consume_streaming_transcript():
    global _streamed_transcript
    with _streamed_transcript_lock:
        text, _streamed_transcript = _streamed_transcript, None
    return text


def _add_nvidia_dll_dirs():
    """Add pip-installed NVIDIA DLL directories to the Windows DLL search path."""
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
        # nvidia-*-cu12 wheels are PEP 420 namespace packages (__file__ is None); use __path__.
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
        # Also prepend PATH for anything still using PATH search.
        if bin_dir not in os.environ.get("PATH", ""):
            os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")


_add_nvidia_dll_dirs()  # once at import, before anything touches ctranslate2/CUDA
if any("couldn't import it" in line or "no 'bin'" in line or "failed (" in line for line in _dll_diagnostics):
    # Only print if something actually looks wrong.
    print("[transcribe] NVIDIA DLL setup check found a problem:")
    for line in _dll_diagnostics:
        print(f"[transcribe]   {line}")


def _resolve_device_and_compute_type():
    """(device, compute_type) for WhisperModel from WHISPER_DEVICE/COMPUTE_TYPE
    (default "auto"). Detects NVIDIA GPU via ctranslate2; no torch needed."""
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
    # Lock: preload() is on a background thread; a command arriving first
    # could race into a second WhisperModel.
    if _model is None:
        with _model_lock:
            if _model is None:
                print("Loading Whisper model (first run only, may take a moment)...")
                _gpu_fallback_reason = None
                device, compute_type = _resolve_device_and_compute_type()
                # 0 = auto: most CPU cores, leave a little headroom. CPU only.
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
                        # Broken/missing CUDA/cuDNN — fall back to CPU.
                        # ctranslate2 may not touch those DLLs until first
                        # transcription; see the retry in transcribe().
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
                # Use tracked globals, not locals — those still hold the
                # originally attempted values after a CPU fallback.
                print(f"Whisper model loaded ({config.WHISPER_MODEL_SIZE} on {_model_device}/{_model_compute_type}).")
    return _model


def _force_reload_on_cpu():
    """Drop the GPU model; future _get_model() loads on CPU this run.
    Used when GPU construction succeeds but first transcription fails
    (ctranslate2 sometimes touches CUDA/cuBLAS/cuDNN lazily)."""
    global _model, _model_is_on_gpu, _model_device, _model_compute_type, _gpu_fallback_reason
    with _model_lock:
        _model = None
        _model_is_on_gpu = False
        _model_device = None
        _model_compute_type = None
        _gpu_fallback_reason = "GPU failed during transcription (see console log)"
        config.WHISPER_DEVICE = "cpu"  # stop _get_model() from retrying GPU


def reload_model():
    """Drop the loaded Whisper model so the next _get_model() rebuilds from
    current WHISPER_MODEL_SIZE/DEVICE/COMPUTE_TYPE.

    _get_model() caches for process lifetime; Settings live changes need this.
    Also undoes any WHISPER_DEVICE = "cpu" override from _force_reload_on_cpu
    so choosing GPU again actually retries."""
    global _model, _model_is_on_gpu, _model_device, _model_compute_type, _gpu_fallback_reason
    with _model_lock:
        _model = None
        _model_is_on_gpu = False
        _model_device = None
        _model_compute_type = None
        _gpu_fallback_reason = None


def reload_model_async():
    """reload_model() then preload on a background thread. Never blocks; GUI-safe."""
    def reload_and_preload():
        reload_model()
        _get_model()

    threading.Thread(target=reload_and_preload, daemon=True).start()


def preload():
    """Load Whisper now instead of lazily. Call on a background thread at startup.
    Idempotent — only the first load does anything. Returns a friendly error
    message if the model couldn't load (e.g. offline on first run), else None;
    the next transcription retries the load."""
    if start_streaming() is not None:
        return None
    try:
        _get_model()
    except Exception as e:
        message = (
            f"I couldn't load my speech model ({config.WHISPER_MODEL_SIZE}), so I can't "
            "hear you yet. It downloads once on first run, so check your internet "
            "connection. I'll try again next time you talk, and you can still type to me."
        )
        print(f"[transcribe] {message} Details: {e!r}")
        return message
    return None


def build_contextual_prompt() -> str:
    """Whisper initial prompt: base prompt, assistant name, user nickname, and vocabulary corrections."""
    parts = []
    base_prompt = getattr(config, "WHISPER_INITIAL_PROMPT", "")
    if base_prompt:
        parts.append(base_prompt.strip().rstrip("."))

    name = getattr(config, "ASSISTANT_NAME", "Alyssa")
    if name and name not in base_prompt:
        parts.append(f"Assistant: {name}")

    nickname = getattr(config, "USER_NICKNAME", "")
    if nickname:
        parts.append(f"User: {nickname}")

    # No active-window title here: Whisper echoes its prompt on near-silent audio, so
    # titles came back as "commands", and the prompt is also sent to Groq when enabled.

    vocab = getattr(config, "VOCABULARY_CORRECTIONS", [])
    vocab_words = []
    for item in vocab:
        if isinstance(item, (list, tuple)) and len(item) > 0:
            target = item[0]
            if target and target not in base_prompt:
                vocab_words.append(target)
    if vocab_words:
        parts.append(f"Vocabulary: {', '.join(vocab_words)}")

    prompt = ". ".join(p for p in parts if p)
    if prompt and not prompt.endswith("."):
        prompt += "."
    return prompt


def _audio_to_wav_bytes(audio) -> bytes:
    """Convert audio input (numpy float32/int16 array or raw pcm bytes) into 16kHz mono WAV bytes."""
    if isinstance(audio, np.ndarray):
        if audio.dtype in (np.float32, np.float64):
            pcm16 = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16)
        else:
            pcm16 = audio.astype(np.int16)
        raw_pcm = pcm16.tobytes()
    elif isinstance(audio, (bytes, bytearray)):
        raw_pcm = bytes(audio)
    else:
        raise TypeError(f"Unsupported audio type for WAV conversion: {type(audio)}")

    sample_rate = int(getattr(config, "SAMPLE_RATE", 16000))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(raw_pcm)
    return buf.getvalue()


_groq_session = None
_groq_session_lock = threading.Lock()


def _get_groq_session() -> requests.Session:
    global _groq_session
    if _groq_session is None:
        with _groq_session_lock:
            if _groq_session is None:
                _groq_session = requests.Session()
    return _groq_session


def _transcribe_with_groq(audio, prompt: str = "") -> str:
    """Transcribe audio via Groq Whisper Cloud API (~90ms round-trip).
    Falls back to exception if key is missing, network fails, or non-200 status.
    """
    api_key = getattr(config, "GROQ_API_KEY", "")
    if not api_key:
        raise ValueError("Groq API key is not configured")

    wav_bytes = _audio_to_wav_bytes(audio)
    model = getattr(config, "STT_GROQ_MODEL", "whisper-large-v3-turbo")
    lang = getattr(config, "STT_LANGUAGE", "en")

    files = {
        "file": ("audio.wav", wav_bytes, "audio/wav"),
    }
    data = {
        "model": model,
        "language": lang,
        "response_format": "json",
    }
    if prompt:
        data["prompt"] = prompt

    headers = {
        "Authorization": f"Bearer {api_key}",
    }
    session = _get_groq_session()
    resp = session.post(
        "https://api.groq.com/openai/v1/audio/transcriptions",
        headers=headers,
        files=files,
        data=data,
        timeout=5.0,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Groq API error ({resp.status_code}): {resp.text}")

    raw_text = resp.json().get("text", "").strip()
    return _apply_vocabulary_corrections(raw_text)


def get_engine_status() -> str:
    """Human-readable STT engine in use (model, device, precision, provider).
    Reflects reality: Groq Turbo Cloud, "auto", and GPU→CPU fallback. Safe before the model has loaded."""
    model_size = getattr(config, "WHISPER_MODEL_SIZE", "?")
    provider = str(getattr(config, "STT_PROVIDER", "auto")).lower()
    groq_key = getattr(config, "GROQ_API_KEY", "")
    groq_active = (provider == "groq" or (provider == "auto" and bool(groq_key)))

    device, compute_type = _resolve_device_and_compute_type() if _model is None else (
        ("cuda" if _model_is_on_gpu else "cpu"), _model_compute_type
    )
    where = "GPU (CUDA)" if device == "cuda" else "CPU"

    if groq_active and bool(groq_key):
        groq_model = getattr(config, "STT_GROQ_MODEL", "whisper-large-v3-turbo")
        return (
            f"Hybrid STT: Groq Cloud Whisper ('{groq_model}', ~90ms LPU) active "
            f"with automatic local fallback ('{model_size}' on {where})."
        )

    if _model is None:
        return (
            f"Whisper model '{model_size}' hasn't loaded yet - based on "
            f"current settings it will run on the {where} using {compute_type} "
            "precision once needed."
        )

    base = (
        f"Whisper model '{model_size}' is loaded and running on the "
        f"{where} using {_model_compute_type} precision."
    )
    if not _model_is_on_gpu and _gpu_fallback_reason:
        base += f" (GPU was tried and failed, so this fell back to CPU: {_gpu_fallback_reason})"
        broken = [
            line for line in _dll_diagnostics
            if "couldn't import it" in line or "no 'bin'" in line or "failed (" in line
        ]
        if broken:
            base += " Likely cause: " + " | ".join(broken)
    return base


def _apply_vocabulary_corrections(text: str) -> str:
    """Fix known mishearings via VOCABULARY_CORRECTIONS. Per entry:
    1. Exact substring match against listed mishearings.
    2. Fuzzy: slide a word-window over the text and swap in the correct phrase
       on a close match (catches unlisted mishearings)."""
    corrections = getattr(config, "VOCABULARY_CORRECTIONS", [])

    her_names = {n.lower() for n in nameutil.name_variants()}
    for correct, mishearings in corrections:
        for wrong in mishearings:
            text = re.sub(r"\b" + re.escape(wrong) + r"\b", correct, text, flags=re.IGNORECASE)

        if correct.lower() in text.lower():
            continue  # already correct (or just fixed above) - don't run fuzzy pass over it
        if correct.lower() in her_names:
            # Fuzzy-matching her name turned "Alice", "a listen" etc. into "Alyssa",
            # which made her answer speech that wasn't for her.
            continue

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
        result_text = _consume_early_transcript()
    if result_text is None:
        result_text = _transcribe_impl(audio)
    if _is_prompt_echo(result_text, build_contextual_prompt()):
        print(f"(Ignored Whisper echoing its prompt: {result_text!r})")
        result_text = ""
    if result_text:
        _notify_partial_transcript_hooks(result_text)
    telemetry.log(f"[timing] transcribe: {time.time() - _t0:.2f}s")
    return result_text


def _transcribe_impl(audio) -> str:
    prompt = build_contextual_prompt()
    provider = str(getattr(config, "STT_PROVIDER", "auto")).lower()
    groq_key = getattr(config, "GROQ_API_KEY", "")

    # Groq Cloud Whisper API for ~90ms ultra-fast transcription
    should_try_groq = (provider == "groq") or (provider == "auto" and bool(groq_key))
    if should_try_groq and bool(groq_key):
        try:
            return _transcribe_with_groq(audio, prompt=prompt)
        except Exception as e:
            print(f"(Groq STT failed, falling back to local Whisper: {e})")

    # Hold the cached model + device metadata until inference and any GPU
    # fallback finish. Settings reloads wait here.
    with _model_lock:
        try:
            return _transcribe_impl_locked(audio, initial_prompt=prompt)
        except TypeError:
            return _transcribe_impl_locked(audio)


# Builds before v1.10.0 shipped without faster-whisper's VAD model, so vad_filter
# crashed every local transcription. Our own VAD already trimmed the audio.
_WHISPER_VAD_AVAILABLE = bool(
    glob.glob(os.path.join(os.path.dirname(faster_whisper.__file__), "assets", "silero_vad*.onnx"))
)


# Friends in a Discord call: give way to your own speech instead of queueing behind it.
CALL_LOCK_WAIT_SECONDS = 5.0


def transcribe_call_audio(audio, hint_names=()) -> str:
    """Local Whisper for what people in a Discord call say (no cloud STT, no tokens).

    One pass, no retries at higher temperatures: on game sound those took many seconds
    each and held the model while your own commands waited. If Whisper stays busy with
    your speech for a few seconds, this piece is skipped.
    """
    prompt = build_contextual_prompt()
    names = [n for n in hint_names if n]
    if names:
        prompt = f"{prompt} In the call: {', '.join(names)}.".strip()
    if not _model_lock.acquire(timeout=CALL_LOCK_WAIT_SECONDS):
        return ""
    try:
        text = _transcribe_impl_locked(audio, initial_prompt=prompt, quick=True)
    finally:
        _model_lock.release()
    return "" if _is_prompt_echo(text, prompt) else text


def _transcribe_impl_locked(audio, initial_prompt: str | None = None, quick: bool = False) -> str:
    if initial_prompt is None:
        initial_prompt = build_contextual_prompt()
    model = _get_model()
    try:
        segments, _ = model.transcribe(
            audio,
            language="en",
            beam_size=1,  # greedy first pass; low-confidence retries via temperature
            temperature=0.0 if quick else (0.0, 0.2, 0.4, 0.6, 0.8, 1.0),
            compression_ratio_threshold=2.4,
            # Don't tighten: room-noise clips retried constantly (big speed cost).
            log_prob_threshold=getattr(config, "WHISPER_LOG_PROB_THRESHOLD", -1.0),
            # Don't let one uncertain segment bias the next (accented speech).
            condition_on_previous_text=False,
            # Trim silence/noise; stops dead air from hallucinating into junk.
            vad_filter=_WHISPER_VAD_AVAILABLE,
            initial_prompt=initial_prompt,
        )
        segments = list(segments)  # evaluate now — faster-whisper decodes lazily; GPU DLL failure can surface here
    except Exception as e:
        if _model_is_on_gpu:
            # Missing/broken CUDA/cuBLAS/cuDNN — often only on first real
            # transcription. Reload on CPU and retry instead of crashing the loop.
            print(f"[transcribe] GPU transcription failed ({e}); reloading on CPU and retrying")
            for line in _dll_diagnostics:
                print(f"[transcribe]   {line}")
            if not any(
                "couldn't import it" in line or "no 'bin'" in line or "failed (" in line
                for line in _dll_diagnostics
            ):
                # Packages look fine → likely CUDA major-version mismatch
                # (cublas64_12.dll needs CUDA 12).
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
                temperature=0.0 if quick else (0.0, 0.2, 0.4, 0.6, 0.8, 1.0),
                compression_ratio_threshold=2.4,
                log_prob_threshold=getattr(config, "WHISPER_LOG_PROB_THRESHOLD", -1.0),
                condition_on_previous_text=False,
                vad_filter=_WHISPER_VAD_AVAILABLE,
                initial_prompt=initial_prompt,
            )
        else:
            raise

    # Drop Whisper filler hallucinations on silent/noisy audio.
    kept = []
    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue
        if segment.no_speech_prob > 0.6:
            continue  # model itself thinks this was probably non-speech
        if segment.avg_logprob < -1.0:
            continue  # very low confidence; looser cutoffs let hallucinated commands through
        kept.append(text)

    result = " ".join(kept).strip()
    return _apply_vocabulary_corrections(result)


def _is_prompt_echo(text: str, prompt: str | None) -> bool:
    """Whisper sometimes "hears" its own prompt on noise ("Alyssa, open Chrome,
    open Notepad"). Two or more prompt phrases in a row, and nothing else, is that
    echo, not something you said."""
    if not text or not prompt:
        return False

    def norm(s):
        return " ".join(re.findall(r"[a-z0-9']+", s.lower()))

    said = norm(text)
    her_names = {n.lower() for n in nameutil.name_variants()}
    phrases = [norm(p) for p in re.split(r"[,.:]", prompt)]
    phrases = [p for p in phrases if p not in her_names]
    hits = sum(1 for p in phrases if p and p in said)
    return hits >= 2 and said in norm(prompt)
