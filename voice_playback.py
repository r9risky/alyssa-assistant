"""Text-to-speech streaming and audio playback."""

import os
import queue
import re
import threading
import time

import sounddevice as sd

from config import AUDIO_SETTINGS as config
import telemetry

from voice_synthesis import (
    websocket_connect, _get_loop, _eleven_realtime_tts,
    _synthesize_to_temp_file,
)

import pygame

_mixer_lock = threading.Lock()
_mixer_ready = False


def _configured_output_device():
    """Return the sounddevice output index matching AUDIO_OUTPUT_DEVICE, or
    None to use the system default - same name-matching convention as
    recorder._configured_input_device()."""
    configured = getattr(config, "AUDIO_OUTPUT_DEVICE", None)
    if not configured or str(configured).lower() == "default":
        return None
    if isinstance(configured, int):
        return configured
    needle = str(configured).lower()
    for index, device in enumerate(sd.query_devices()):
        if device["max_output_channels"] > 0 and needle in device["name"].lower():
            return index
    return None  # falls back to default rather than failing playback


def _configured_output_device_name():
    """Like _configured_output_device(), but returns the matched device's
    exact name - pygame's mixer takes a device name, not a sounddevice
    index."""
    configured = getattr(config, "AUDIO_OUTPUT_DEVICE", None)
    if not configured or str(configured).lower() == "default":
        return None
    needle = str(configured).lower()
    for device in sd.query_devices():
        if device["max_output_channels"] > 0 and needle in device["name"].lower():
            return device["name"]
    return None


def _ensure_mixer():
    global _mixer_ready
    if _mixer_ready:
        return
    with _mixer_lock:
        if _mixer_ready:
            return
        _init_mixer_locked()
        _mixer_ready = True


def _init_mixer_locked():
    device_name = _configured_output_device_name()
    if device_name:
        try:
            pygame.mixer.init(devicename=device_name)
            return
        except Exception as e:
            print(f"(couldn't open output device {device_name!r}, using default: {e})")
    pygame.mixer.init()


def reinit_mixer():
    """Reopens the pygame mixer on the currently configured output device -
    called from Settings when AUDIO_OUTPUT_DEVICE changes, so picking a new
    speaker/headset takes effect immediately instead of needing a restart."""
    global _mixer_ready
    with _mixer_lock:
        if _mixer_ready:
            try:
                pygame.mixer.quit()
            except Exception:
                pass
        _init_mixer_locked()
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


class StreamingSpeaker:
    """Turns LLM deltas into clause-sized TTS work while generation continues."""

    _BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+|(?<=[,;:])\s+")

    def __init__(
        self,
        on_playback_start=None,
        on_playback_end=None,
        stop_event=None,
    ):
        self.on_playback_start = on_playback_start
        self.on_playback_end = on_playback_end
        self.stop_event = stop_event or threading.Event()
        self.text = ""
        self._pending = ""
        self._chunks = queue.Queue()
        self._audio = queue.Queue()
        self._audio_done = threading.Event()
        self._audio_error = None
        self._playback_started = False
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    @property
    def interrupted(self):
        return self.stop_event.is_set()

    def feed(self, delta: str):
        if not delta or self.interrupted:
            return
        self.text += delta
        self._pending += delta
        minimum = max(1, int(getattr(config, "TTS_CLAUSE_MIN_CHARACTERS", 28)))
        while True:
            match = self._BOUNDARY_RE.search(self._pending)
            if match is None:
                break
            end = match.end()
            hard_boundary = self._pending[match.start() - 1] in ".!?"
            # Sentence punctuation is latency-sensitive and flushes at once.
            # Soft clause punctuation waits for the configured minimum, but a
            # later sentence boundary still ends the chunk immediately.
            while not hard_boundary and end < minimum:
                next_match = self._BOUNDARY_RE.search(self._pending, end)
                if next_match is None:
                    end = None
                    break
                end = next_match.end()
                hard_boundary = self._pending[next_match.start() - 1] in ".!?"
            if end is None:
                break
            chunk, self._pending = self._pending[:end], self._pending[end:]
            chunk = _smooth_speech_text(chunk)
            if chunk:
                self._chunks.put(chunk)

    def finish(self):
        tail = _smooth_speech_text(self._pending)
        self._pending = ""
        if tail:
            self._chunks.put(tail)
        self._chunks.put(None)
        self._worker.join(timeout=60)
        if self._worker.is_alive():
            self.stop_event.set()
            _eleven_realtime_tts.cancel(self)
        return self.interrupted

    def _start_playback(self):
        if self._playback_started:
            return
        self._playback_started = True
        if self.on_playback_start is not None:
            self.on_playback_start()

    def _run(self):
        realtime = (
            getattr(config, "TTS_STREAMING_ENABLED", True)
            and getattr(config, "TTS_PROVIDER", "edge") == "elevenlabs"
            and websocket_connect is not None
        )
        try:
            if realtime:
                self._run_eleven_realtime()
            else:
                self._run_file_pipeline()
        finally:
            if self.on_playback_end is not None:
                self.on_playback_end()

    def _run_file_pipeline(self):
        while not self.interrupted:
            chunk = self._chunks.get()
            if chunk is None:
                return
            interrupted = _speak_one(
                chunk,
                self._start_playback,
                stop_event=self.stop_event,
            )
            if interrupted:
                self.stop_event.set()
                return

    def _run_eleven_realtime(self):
        playback = threading.Thread(target=self._play_pcm, daemon=True)
        playback.start()
        try:
            _eleven_realtime_tts.start(self)
            while not self.interrupted:
                chunk = self._chunks.get()
                if chunk is None:
                    _eleven_realtime_tts.send(" ", flush=True)
                    break
                _eleven_realtime_tts.send(chunk + " ")
            while not self.interrupted and not self._audio_done.wait(0.02):
                pass
            if self.interrupted:
                self._finish_audio()
                _eleven_realtime_tts.cancel(self)
            playback.join(timeout=2)
            if self._audio_error and not self._playback_started:
                raise self._audio_error
        except Exception as exc:
            if not self.interrupted:
                print(f"(realtime TTS failed, falling back to file playback: {exc})")
                _speak_one(
                    self.text,
                    self._start_playback,
                    stop_event=self.stop_event,
                )

    def _queue_audio(self, chunk):
        if not self.interrupted:
            self._audio.put(chunk)

    def _finish_audio(self):
        self._audio.put(None)

    def _fail_audio(self, exc):
        self._audio_error = exc
        self._audio.put(None)

    def _play_pcm(self):
        buffer_bytes = max(
            1,
            int(24000 * 2 * getattr(config, "TTS_AUDIO_BUFFER_MS", 100) / 1000),
        )
        buffered = bytearray()
        stream = None
        try:
            while not self.interrupted:
                chunk = self._audio.get()
                if chunk is None:
                    if buffered:
                        if stream is None:
                            stream = sd.RawOutputStream(
                                samplerate=24000,
                                channels=1,
                                dtype="int16",
                                blocksize=buffer_bytes // 2,
                                latency="low",
                                device=_configured_output_device(),
                            )
                            stream.start()
                            self._start_playback()
                        stream.write(bytes(buffered))
                    return
                buffered.extend(chunk)
                if stream is None and len(buffered) >= buffer_bytes:
                    stream = sd.RawOutputStream(
                        samplerate=24000,
                        channels=1,
                        dtype="int16",
                        blocksize=buffer_bytes // 2,
                        latency="low",
                        device=_configured_output_device(),
                    )
                    stream.start()
                    self._start_playback()
                if stream is not None and buffered:
                    stream.write(bytes(buffered))
                    buffered.clear()
        except Exception as exc:
            self._audio_error = exc
            self._audio_done.set()
        finally:
            if stream is not None:
                try:
                    stream.abort() if self.interrupted else stream.stop()
                finally:
                    stream.close()
            self._audio_done.set()


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
            if getattr(config, "TTS_STREAMING_ENABLED", True):
                _eleven_realtime_tts.prewarm()
            return
        path = _synthesize_to_temp_file("Hello")
        os.remove(path)
    except Exception as e:
        print(f"(voice warm-up failed, continuing: {e})")


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
    """Speak sentences with at most two synthesis requests in flight.

    The current sentence and one lookahead sentence synthesize concurrently.
    As playback advances, the next job starts. On interruption, work that has
    not started stays cancelled; already-running daemon jobs finish and their
    temporary files are removed in the background.
    """
    if stop_event is not None and stop_event.is_set():
        if on_playback_end is not None:
            on_playback_end()
        return True

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

    ensure_started(0)
    ensure_started(1)

    def cleanup_when_ready():
        for i in range(played_through, len(sentences)):
            if threads[i] is None:
                continue
            ready[i].wait()
            if paths[i]:
                try:
                    os.remove(paths[i])
                except OSError:
                    pass

    started_playback = False
    interrupted = False
    played_through = 0
    try:
        for i, _sentence in enumerate(sentences):
            if stop_event is not None and stop_event.is_set():
                interrupted = True
                break
            while not ready[i].wait(0.05):
                if stop_event is not None and stop_event.is_set():
                    interrupted = True
                    break
            if interrupted:
                break

            played_through = i + 1
            ensure_started(i + 2)
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
        telemetry.log(f"[timing] time-to-first-audio: {time.time() - _t_speak_start:.2f}s")
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
