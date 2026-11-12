"""Text-to-speech provider synthesis and realtime ElevenLabs transport."""

import asyncio
import concurrent.futures
import base64
import json
import os
import tempfile
import threading
from urllib.parse import urlencode

import edge_tts
import requests

try:
    from websockets.sync.client import connect as websocket_connect
except ImportError:  # Existing installs keep file-based playback until updated.
    websocket_connect = None

from config import AUDIO_SETTINGS as config

async def _synthesize(text: str, out_path: str):
    """Dispatch to TTS_PROVIDER. Runs on the shared background loop.
    ElevenLabs is a blocking requests call, so it's run_in_executor'd."""
    provider = getattr(config, "TTS_PROVIDER", "edge")
    if provider == "elevenlabs":
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _synthesize_elevenlabs, text, out_path)
    elif provider == "kokoro":
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _synthesize_kokoro, text, out_path)
    else:
        await _synthesize_edge(text, out_path)

def _is_cert_error(error: Exception) -> bool:
    return "certificate" in str(error).lower()


def _use_os_trust_store_for_edge_tts():
    """edge_tts pins certifi's CA bundle, which lacks the root that antivirus/corporate
    HTTPS inspection installs in the OS store. Retry against the OS store, still verified."""
    import ssl
    import edge_tts.communicate
    import edge_tts.voices

    ctx = ssl.create_default_context()
    edge_tts.communicate._SSL_CTX = ctx
    edge_tts.voices._SSL_CTX = ctx


async def _synthesize_edge(text: str, out_path: str):
    rate_str = getattr(config, "EDGE_TTS_RATE", "+0%")
    pitch_str = getattr(config, "EDGE_TTS_PITCH", "+0Hz")
    voice_name = getattr(config, "EDGE_TTS_VOICE", "en-US-AriaNeural") or "en-US-AriaNeural"
    for attempt in range(2):
        communicate = edge_tts.Communicate(
            text,
            voice_name,
            rate=rate_str,
            volume=getattr(config, "EDGE_TTS_VOLUME", "+0%"),
            pitch=pitch_str,
        )
        try:
            await communicate.save(out_path)
            return
        except Exception as e:
            if attempt or not _is_cert_error(e):
                raise
            _use_os_trust_store_for_edge_tts()


# --- Kokoro: free local voice (kokoro-onnx). Model files download once. ---
KOKORO_RELEASE_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
KOKORO_FILES = ("kokoro-v1.0.int8.onnx", "voices-v1.0.bin")  # int8: ~90 MB, fast on CPU
_kokoro = None
_kokoro_lock = threading.Lock()


def kokoro_models_dir() -> str:
    import config as config_module  # config.py sits next to the exe in frozen builds
    return os.path.join(os.path.dirname(os.path.abspath(config_module.__file__)), "models", "kokoro")


def _download_file(url: str, dest: str):
    partial = dest + ".part"
    with requests.get(url, stream=True, timeout=30) as response:
        response.raise_for_status()
        with open(partial, "wb") as f:
            for chunk in response.iter_content(chunk_size=1 << 20):
                f.write(chunk)
    os.replace(partial, dest)  # only a complete download gets the real name


def _get_kokoro():
    global _kokoro
    with _kokoro_lock:
        if _kokoro is None:
            try:
                from kokoro_onnx import Kokoro
            except ImportError as exc:
                raise RuntimeError(
                    "The Kokoro voice needs the kokoro-onnx package: pip install kokoro-onnx"
                ) from exc
            folder = kokoro_models_dir()
            os.makedirs(folder, exist_ok=True)
            paths = [os.path.join(folder, name) for name in KOKORO_FILES]
            for name, path in zip(KOKORO_FILES, paths):
                if not os.path.isfile(path):
                    print(f"Downloading Kokoro voice file {name} (first run only)...")
                    _download_file(KOKORO_RELEASE_URL + name, path)
            _kokoro = Kokoro(*paths)
        return _kokoro


def _synthesize_kokoro(text: str, out_path: str):
    import wave

    import numpy as np

    kokoro = _get_kokoro()
    with _kokoro_lock:  # one onnxruntime run at a time
        samples, sample_rate = kokoro.create(
            text,
            voice=getattr(config, "KOKORO_VOICE", "af_heart") or "af_heart",
            speed=1.0,
            lang="en-us",
        )
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
    with wave.open(out_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())


# Reused Session: keep-alive to api.elevenlabs.io so pipelined sentences
# skip the ~100-300ms handshake a fresh connection pays each time.
_eleven_session = requests.Session()


def _synthesize_elevenlabs(text: str, out_path: str):
    """Blocking — only via run_in_executor, never on the shared event-loop thread.

    Streaming endpoint, writes chunks as they arrive. pygame still needs a
    completed file; short first sentences cut time-to-first-audio via pipelining.
    """
    api_key = getattr(config, "ELEVENLABS_API_KEY", "")
    voice_id = getattr(config, "ELEVENLABS_VOICE_ID", "")
    if not api_key or not voice_id:
        raise RuntimeError(
            "TTS_PROVIDER is 'elevenlabs' but ELEVENLABS_API_KEY and/or "
            "ELEVENLABS_VOICE_ID isn't set - fill both in via Settings -> "
            "Assistant -> Voice & Behavior, or config.py."
        )
    resp = _eleven_session.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream",
        headers={"xi-api-key": api_key, "Content-Type": "application/json"},
        json={
            "text": text,
            "model_id": getattr(config, "ELEVENLABS_MODEL", "eleven_multilingual_v2"),
        },
        # (connect timeout, per-chunk read timeout) — streaming is read incrementally.
        timeout=(10, 30),
        stream=True,
    )
    if resp.status_code != 200:
        detail = ""
        try:
            detail = resp.json().get("detail", {}).get("message", "")
        except ValueError:
            pass
        raise RuntimeError(f"ElevenLabs returned an error ({resp.status_code}): {detail or resp.text[:200]}")
    with open(out_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=4096):
            if chunk:
                f.write(chunk)


# Full Edge voice catalog, fetched once per process. Populated on demand
# (Settings picker), never at startup.
_edge_voices_cache = None


async def _fetch_edge_voices_async():
    try:
        return await edge_tts.list_voices()
    except Exception as e:
        if not _is_cert_error(e):
            raise
        _use_os_trust_store_for_edge_tts()
        return await edge_tts.list_voices()


def list_edge_voices(force_refresh: bool = False) -> list:
    """Microsoft Edge neural voice catalog as dicts (ShortName, Locale, Gender, ...).
    Cached after first call; force_refresh=True hits the network again.
    Blocking — call from a background thread, not the GUI thread.
    Raises on failure (empty list would look like "no voices")."""
    global _edge_voices_cache
    if _edge_voices_cache is not None and not force_refresh:
        return _edge_voices_cache
    voices = _run_on_loop(_fetch_edge_voices_async())
    voices = sorted(voices, key=lambda v: (v.get("Locale", ""), v.get("ShortName", "")))
    _edge_voices_cache = voices
    return voices


def list_elevenlabs_voices(api_key: str) -> list:
    """Account's ElevenLabs voices (premade + cloned/designed). Blocking; run off GUI thread.
    Returns [{"voice_id", "name", "category"}, ...]. Raises on failure."""
    resp = requests.get(
        "https://api.elevenlabs.io/v1/voices",
        headers={"xi-api-key": api_key},
        timeout=15,
    )
    if resp.status_code != 200:
        detail = ""
        try:
            detail = resp.json().get("detail", {}).get("message", "")
        except ValueError:
            pass
        raise RuntimeError(f"ElevenLabs returned an error ({resp.status_code}): {detail or resp.text[:200]}")
    data = resp.json()
    return [
        {
            "voice_id": v.get("voice_id", ""),
            "name": v.get("name", ""),
            "category": v.get("category", ""),
        }
        for v in data.get("voices", [])
    ]


# One background event loop for the process lifetime; asyncio.run() per
# sentence is pure overhead on pipelined replies.
_loop = None
_loop_lock = threading.Lock()


def _get_loop():
    global _loop
    if _loop is not None:
        return _loop
    with _loop_lock:
        if _loop is None:
            loop = asyncio.new_event_loop()
            threading.Thread(target=loop.run_forever, daemon=True).start()
            _loop = loop
        return _loop


# A voice server that stops answering mid-line must not hold her speech lock forever
# (everything else that speaks, including your next command, waits on it).
SYNTHESIS_TIMEOUT_SECONDS = 90.0


def _run_on_loop(coro):
    fut = asyncio.run_coroutine_threadsafe(coro, _get_loop())
    try:
        return fut.result(SYNTHESIS_TIMEOUT_SECONDS)
    except concurrent.futures.TimeoutError:
        fut.cancel()
        raise TimeoutError(f"the voice took over {SYNTHESIS_TIMEOUT_SECONDS:.0f}s to answer") from None


class _ElevenRealtimeTTS:
    """Persistent ElevenLabs text-input WebSocket; one reply is active at a time."""

    def __init__(self):
        self._connection = None
        self._lock = threading.Lock()
        self._active = None

    def _ensure_connected(self):
        if self._connection is not None:
            return
        api_key = getattr(config, "ELEVENLABS_API_KEY", "")
        voice_id = getattr(config, "ELEVENLABS_VOICE_ID", "")
        if not api_key or not voice_id or websocket_connect is None:
            raise RuntimeError("ElevenLabs realtime TTS is not configured")
        query = urlencode(
            {
                "model_id": getattr(config, "ELEVENLABS_MODEL", "eleven_flash_v2_5"),
                "output_format": "pcm_24000",
            }
        )
        connection = websocket_connect(
            f"wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input?{query}",
            open_timeout=5,
            close_timeout=1,
        )
        connection.send(
            json.dumps(
                {
                    "text": " ",
                    "voice_settings": {
                        "stability": 0.5,
                        "similarity_boost": 0.8,
                        "use_speaker_boost": False,
                    },
                    "generation_config": {
                        "chunk_length_schedule": [30, 60, 120, 180]
                    },
                    "xi_api_key": api_key,
                }
            )
        )
        self._connection = connection
        threading.Thread(target=self._receive, args=(connection,), daemon=True).start()

    def start(self, speaker):
        with self._lock:
            self._ensure_connected()
            self._active = speaker

    def prewarm(self):
        with self._lock:
            self._ensure_connected()

    def send(self, text, flush=False):
        with self._lock:
            self._ensure_connected()
            self._connection.send(json.dumps({"text": text, "flush": flush}))

    def _receive(self, connection):
        try:
            for raw in connection:
                event = json.loads(raw)
                active = self._active
                if active is None:
                    continue
                if event.get("audio"):
                    active._queue_audio(base64.b64decode(event["audio"]))
                if event.get("isFinal"):
                    active._finish_audio()
        except Exception as exc:
            active = self._active
            if active is not None:
                active._fail_audio(exc)
        finally:
            with self._lock:
                if self._connection is connection:
                    self._connection = None

    def cancel(self, speaker):
        with self._lock:
            if self._active is not speaker:
                return
            connection, self._connection, self._active = self._connection, None, None
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


_eleven_realtime_tts = _ElevenRealtimeTTS()

def _synthesize_to_temp_file(text: str) -> str:
    """Synthesize *text* to a new temp audio file (.mp3, or .wav for Kokoro)
    and return its path. Raises on failure after cleaning up the temp file."""
    suffix = ".wav" if getattr(config, "TTS_PROVIDER", "edge") == "kokoro" else ".mp3"
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    try:
        _run_on_loop(_synthesize(text, path))
    except Exception:
        try:
            os.remove(path)
        except OSError:
            pass
        raise
    return path
