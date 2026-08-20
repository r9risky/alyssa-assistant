"""Text-to-speech provider synthesis and realtime ElevenLabs transport."""

import asyncio
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

import config

async def _synthesize(text: str, out_path: str):
    """Dispatches to whichever provider TTS_PROVIDER (config.py) selects.
    Runs on the shared background loop either way - the ElevenLabs path is
    a blocking `requests` call under the hood, so it's handed to a thread
    pool executor rather than awaited directly, same effect as `await
    communicate.save()` below from the caller's point of view."""
    provider = getattr(config, "TTS_PROVIDER", "edge")
    if provider == "elevenlabs":
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _synthesize_elevenlabs, text, out_path)
    else:
        await _synthesize_edge(text, out_path)

async def _synthesize_edge(text: str, out_path: str):
    communicate = edge_tts.Communicate(
        text,
        config.EDGE_TTS_VOICE,
        rate=getattr(config, "EDGE_TTS_RATE", "+0%"),
        volume=getattr(config, "EDGE_TTS_VOLUME", "+0%"),
        pitch=getattr(config, "EDGE_TTS_PITCH", "+0Hz"),
    )
    await communicate.save(out_path)


# Reused across every ElevenLabs call instead of opening a fresh
# requests.post() each time. requests.Session() keeps the underlying
# TCP/TLS connection to api.elevenlabs.io alive (HTTP keep-alive) and
# pools it, so back-to-back sentences (see _speak_pipelined above) skip
# the ~100-300ms handshake that a brand-new connection pays every time -
# this alone was a big chunk of the "laggy" feeling versus Edge TTS,
# which edge_tts pools internally already.
_eleven_session = requests.Session()


def _synthesize_elevenlabs(text: str, out_path: str):
    """Blocking call - only ever run via run_in_executor above, never
    directly on the shared event loop thread.

    Uses ElevenLabs' streaming endpoint and writes chunks as they arrive.
    pygame still requires a completed file before playback, so this is not
    end-to-end audio streaming; short first sentences are what reduce
    time-to-first-audio through _speak_pipelined().
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
        # (connect timeout, per-chunk read timeout) - streaming responses
        # are read incrementally below, so the timeout has to apply per
        # chunk rather than to the whole response like the old call.
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


# Cache of the full Edge voice catalog (~400 voices) - fetched once per
# process since it's a real network call and the catalog itself rarely
# changes within a session. Only populated on demand (the Settings
# "Browse all voices" picker), never at startup.
_edge_voices_cache = None


async def _fetch_edge_voices_async():
    return await edge_tts.list_voices()


def list_edge_voices(force_refresh: bool = False) -> list:
    """Returns the full Microsoft Edge neural voice catalog - hundreds of
    voices across roughly 140 locales - as a list of dicts (ShortName,
    Locale, Gender, FriendlyName, ...). Cached after the first call; pass
    force_refresh=True to bypass that and hit the network again. Runs a
    blocking network call the first time (or on a forced refresh), so
    callers driving a GUI should call this from a background thread, not
    the GUI thread. Raises on failure - deliberately not swallowed here,
    since a silently-empty list would look like Edge TTS itself has no
    voices rather than "the request failed"."""
    global _edge_voices_cache
    if _edge_voices_cache is not None and not force_refresh:
        return _edge_voices_cache
    voices = _run_on_loop(_fetch_edge_voices_async())
    voices = sorted(voices, key=lambda v: (v.get("Locale", ""), v.get("ShortName", "")))
    _edge_voices_cache = voices
    return voices


def list_elevenlabs_voices(api_key: str) -> list:
    """Fetches the given account's available ElevenLabs voices - both
    ElevenLabs' premade library and any voices the account has cloned or
    designed itself. A blocking network call; run off the GUI thread.
    Returns a list of {"voice_id", "name", "category"} dicts. Raises on
    failure (bad key, network error, etc.) - callers should catch and
    report that rather than showing an empty list as if the account just
    has no voices."""
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


# A single background event loop, reused for every synthesis call instead of
# spinning one up and tearing it down each time (asyncio.run() does this
# under the hood). Creating/closing a loop per sentence adds up across a
# pipelined reply and is pure overhead on top of the actual network request,
# so it's worth keeping one alive for the life of the program.
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


def _run_on_loop(coro):
    fut = asyncio.run_coroutine_threadsafe(coro, _get_loop())
    return fut.result()


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
    """Synthesizes *text* to a new temp .mp3 file and returns its path.
    Raises on failure, cleaning up the temp file itself first."""
    fd, path = tempfile.mkstemp(suffix=".mp3")
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
