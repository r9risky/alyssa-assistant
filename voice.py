"""
Text-to-speech synthesis supporting Edge TTS and ElevenLabs providers.
"""

import asyncio
import os
import re
import tempfile
import threading
import time

import edge_tts
import requests

import config

import pygame

_mixer_lock = threading.Lock()
_mixer_ready = False


def _ensure_mixer():
    global _mixer_ready
    if _mixer_ready:
        return
    with _mixer_lock:
        if _mixer_ready:
            return
        pygame.mixer.init()
        _mixer_ready = True


def _smooth_speech_text(text: str) -> str:
    """Normalizes whitespace for TTS input."""
    return " ".join((text or "").split())


# Words that commonly precede a period without ending a sentence ("Dr.
# Smith", "etc."), checked when deciding sentence-split points below.
_SENTENCE_ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "st", "jr", "sr", "prof",
    "vs", "etc", "e.g", "i.e", "approx",
}


def _split_sentences(text: str) -> list:
    """Splits *text* into sentence-sized chunks for pipelined playback,
    without splitting a decimal ("3.5") or abbreviation ("Dr.", "etc.").
    Under-splitting just costs a little pipelining benefit; over-splitting
    would speak a fragment as a whole sentence, so boundaries are conservative."""
    boundaries = []
    for m in re.finditer(r"[.!?]+(?=\s|$)", text):
        end = m.end()
        if m.group(0) == ".":
            before = text[m.start() - 1] if m.start() > 0 else ""
            after = text[end : end + 1]
            if before.isdigit() and after.isdigit():
                continue  # decimal number, e.g. "3.5" - not a sentence end
            word_before = re.search(r"(\w+)$", text[: m.start()])
            if word_before and word_before.group(1).lower() in _SENTENCE_ABBREVIATIONS:
                continue  # abbreviation, e.g. "Dr." - not a sentence end
        boundaries.append(end)

    sentences = []
    start = 0
    for end in boundaries:
        piece = text[start:end].strip()
        if piece:
            sentences.append(piece)
        start = end
    tail = text[start:].strip()
    if tail:
        sentences.append(tail)
    return sentences


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


def _volume_str_to_gain(volume_str: str) -> float:
    """Converts config.EDGE_TTS_VOLUME ('+0%', '-20%', ...) into a
    pygame.mixer.music playback gain (0.0-1.0).

    Edge TTS's own %-volume prosody knob is passed to Communicate() below,
    but it's notoriously unreliable on many neural voices - some barely
    change loudness at all no matter what value is sent, which is why the
    Settings volume slider could look like it "does nothing." Applying the
    same percentage as real playback gain here guarantees the slider is
    always audible, on top of whatever Edge TTS itself does with its own
    knob. This can only attenuate, never boost - pygame can't play a clip
    louder than its source without introducing distortion - so 0% and any
    positive percentage both play at full (1.0) gain; only negative values
    (quieter) have an effect here. A positive value still reaches Edge
    TTS's own volume parameter in case that provides some boost."""
    try:
        percent = int(str(volume_str).strip().rstrip("%"))
    except (TypeError, ValueError):
        percent = 0
    return max(0.0, min(1.0, 1.0 + percent / 50.0))


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


def warm_up():
    """Fires a throwaway synthesis (and, if pygame is available, initializes
    the mixer) so the first real reply doesn't eat the one-time cost of
    spinning up the event loop thread, opening the audio device, and doing
    Edge TTS's initial connection/handshake. Safe to call from a background
    thread at startup; safe to call more than once.

    Skips the actual network round trip when TTS_PROVIDER is "elevenlabs" -
    unlike Edge TTS, ElevenLabs' API isn't free, so a throwaway "." on every
    single startup would quietly spend a sliver of the account's character
    quota for no real benefit. The event loop thread and mixer still get
    started either way."""
    try:
        _ensure_mixer()
        _get_loop()
        if getattr(config, "TTS_PROVIDER", "edge") == "elevenlabs":
            return
        path = _synthesize_to_temp_file("Hello")
        os.remove(path)
    except Exception as e:
        print(f"(voice warm-up failed, continuing: {e})")


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


def _play(path: str, stop_event=None) -> bool:
    """Plays *path* to completion, or stops as soon as *stop_event* is set.
    Returns True if playback finished on its own, False if cut off."""
    _ensure_mixer()
    music = pygame.mixer.music
    music.load(path)
    try:
        volume = getattr(config, "EDGE_TTS_VOLUME", "+0%")
        music.set_volume(_volume_str_to_gain(volume))
        music.play()
        while music.get_busy():
            current_volume = getattr(config, "EDGE_TTS_VOLUME", "+0%")
            if current_volume != volume:
                volume = current_volume
                music.set_volume(_volume_str_to_gain(volume))
            if stop_event is not None and stop_event.is_set():
                return False
            time.sleep(0.02)
        return True
    finally:
        try:
            music.stop()
        finally:
            music.unload()


def _speak_one(text: str, on_playback_start=None, on_playback_end=None, stop_event=None) -> bool:
    """Synthesize the whole reply, then play it. Used for single-sentence
    replies and as the fallback when pipelining is off. Returns True if
    interrupted (stop_event fired before/during playback)."""
    try:
        path = _synthesize_to_temp_file(text)
    except Exception as e:
        print(f"(voice playback failed, continuing silently: {e})")
        if on_playback_end is not None:
            on_playback_end()
        return False

    interrupted = False
    try:
        if stop_event is not None and stop_event.is_set():
            interrupted = True
        else:
            if on_playback_start is not None:
                on_playback_start()
            interrupted = not _play(path, stop_event)
    except Exception as e:
        print(f"(voice playback failed, continuing silently: {e})")
    finally:
        if on_playback_end is not None:
            on_playback_end()
        try:
            os.remove(path)
        except OSError:
            pass
    return interrupted


def _speak_pipelined(sentences: list, on_playback_start=None, on_playback_end=None, stop_event=None) -> bool:
    """Speaks multiple sentences back to back, synthesizing each one on a
    background thread while the previous one plays (one sentence of
    lookahead), so you hear the reply as soon as the first sentence is
    ready instead of waiting for the whole thing. A sentence that fails to
    synthesize is skipped rather than losing the whole reply.

    Stops at the first opportunity if stop_event fires; synthesis threads
    already in flight for later sentences finish in the background (daemon
    threads) and their temp files are cleaned up once they do. Returns True
    if playback was interrupted."""
    paths = [None] * len(sentences)
    errors = [None] * len(sentences)
    ready = [threading.Event() for _ in sentences]
    threads = [None] * len(sentences)

    def synth(i):
        try:
            paths[i] = _synthesize_to_temp_file(sentences[i])
        except Exception as e:
            errors[i] = e
        finally:
            ready[i].set()

    def ensure_started(i):
        if 0 <= i < len(sentences) and threads[i] is None:
            threads[i] = threading.Thread(target=synth, args=(i,), daemon=True)
            threads[i].start()

    def cleanup_when_ready():
        # Runs on its own thread so an interrupted speak() call can return
        # immediately instead of waiting on synthesis that's still in
        # flight for sentences we're no longer going to play.
        for i in range(played_through, len(sentences)):
            if threads[i] is None:
                continue
            ev = ready[i]
            ev.wait()
            if paths[i]:
                try:
                    os.remove(paths[i])
                except OSError:
                    pass

    ensure_started(0)
    started_playback = False
    interrupted = False
    played_through = 0
    try:
        for i, _sentence in enumerate(sentences):
            if stop_event is not None and stop_event.is_set():
                interrupted = True
                break
            ensure_started(i + 1)  # stay one sentence ahead of what's playing
            ready[i].wait()
            played_through = i + 1
            if errors[i] is not None:
                print(f"(voice synthesis failed for one sentence, skipping: {errors[i]})")
                continue
            if not started_playback and on_playback_start is not None:
                on_playback_start()
            started_playback = True
            try:
                if not _play(paths[i], stop_event):
                    interrupted = True
                    break
            except Exception as e:
                print(f"(voice playback failed, continuing silently: {e})")
    finally:
        if on_playback_end is not None:
            on_playback_end()
        for path in paths[:played_through]:
            if path:
                try:
                    os.remove(path)
                except OSError:
                    pass
        if played_through < len(sentences):
            threading.Thread(target=cleanup_when_ready, daemon=True).start()
    return interrupted


def speak(text: str, on_playback_start=None, on_playback_end=None, stop_event=None) -> bool:
    """Synthesize and play *text*, optionally reporting real playback bounds.

    Synthesis can take a noticeable network round trip. Callbacks deliberately
    wrap actual playback rather than synthesis so a companion animation starts
    when audio is about to be heard, not while Alyssa is silently waiting for
    Edge TTS to create the file.

    A reply with more than one sentence is pipelined (see _speak_pipelined) -
    set TTS_PIPELINE_SENTENCES = False in config.py to always synthesize the
    whole reply as one request instead, the original behavior.

    stop_event, if given, is a threading.Event another thread can set (see
    main.py's speak() wrapper and recorder.listen_for_barge_in) to cut
    playback off as soon as you start talking over her - checked before
    starting each sentence and polled during playback itself. Returns True
    if playback was cut short this way, False if it finished normally (or
    stop_event was never given).
    """
    text = _smooth_speech_text(text)
    if not text:
        return False

    _t_speak_start = time.time()

    def _timed_on_playback_start():
        print(f"[timing] time-to-first-audio: {time.time() - _t_speak_start:.2f}s")
        if on_playback_start is not None:
            on_playback_start()

    if getattr(config, "TTS_PIPELINE_SENTENCES", True):
        sentences = _split_sentences(text)
    else:
        sentences = [text]

    if len(sentences) <= 1:
        return _speak_one(text, _timed_on_playback_start, on_playback_end, stop_event)
    else:
        return _speak_pipelined(sentences, _timed_on_playback_start, on_playback_end, stop_event)
