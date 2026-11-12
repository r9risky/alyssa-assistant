"""Text-to-speech streaming and audio playback."""

import contextlib
import os
import queue
import re
import threading
import time

import sounddevice as sd

try:
    import numpy as np
except ImportError:
    np = None

from config import AUDIO_SETTINGS as config
import telemetry

from voice_synthesis import (
    websocket_connect, _get_loop, _eleven_realtime_tts,
    _synthesize_to_temp_file,
)

import pygame

_mixer_lock = threading.Lock()
_mixer_ready = False
# pygame.mixer.music is one global stream; two threads loading/stopping it at once
# crashes SDL natively (e.g. a tool speaking into a call while her reply plays).
_play_lock = threading.RLock()
# One whole utterance at a time across threads (her reply, watcher alerts, replies to
# friends in a call), so two voices never overlap. main.speak holds it too.
speech_lock = threading.RLock()
_playing = 0  # files/streams playing right now; PC call ears ignore audio while > 0

# pygame's music clock stops when the output device stalls or vanishes; get_busy()
# then stayed True forever and she never listened again.
_PLAYBACK_STALL_SECONDS = 3.0


_playing_lock = threading.Lock()


@contextlib.contextmanager
def _counting_playback():
    global _playing
    with _playing_lock:
        _playing += 1
    try:
        yield
    finally:
        with _playing_lock:
            _playing -= 1


def is_playing() -> bool:
    """True while her voice is playing on this PC."""
    return _playing > 0


def _configured_output_device():
    """sounddevice output index matching AUDIO_OUTPUT_DEVICE, or None for default.
    Same name-matching as recorder._configured_input_device()."""
    configured = getattr(config, "AUDIO_OUTPUT_DEVICE", None)
    if not configured or str(configured).lower() == "default":
        return None
    if isinstance(configured, int):
        return configured
    needle = str(configured).lower()
    try:
        devices = sd.query_devices() if sd is not None else []
    except Exception:
        devices = []
    for index, device in enumerate(devices):
        name = device.get("name", "")
        if device.get("max_output_channels", 0) > 0 and (needle in name.lower() or name.lower() in needle):
            return index
    return None  # falls back to default rather than failing playback


def _configured_output_device_name():
    """Like _configured_output_device(), but the matched device's exact name
    (pygame mixer takes an SDL2 name, not a sounddevice index). Checks
    pygame._sdl2.audio first, falling back to sounddevice query_devices()."""
    configured = getattr(config, "AUDIO_OUTPUT_DEVICE", None)
    if not configured or str(configured).lower() == "default":
        return None
    needle = str(configured).lower()
    try:
        import pygame._sdl2.audio as sdl2_audio
        # SDL lists devices only once its audio is up; before that this came back
        # empty and the fallback gave sounddevice's 31-character cut-off name
        # ("SteelSeries Sonar - Gaming (Ste"), which the mixer can't open.
        probe = not pygame.mixer.get_init()
        if probe:
            pygame.mixer.init()
        try:
            sdl_names = sdl2_audio.get_audio_device_names(False)
        finally:
            if probe:
                pygame.mixer.quit()
        for name in sdl_names:
            if needle in name.lower() or name.lower() in needle:
                return name
    except Exception:
        pass

    try:
        devices = sd.query_devices() if sd is not None else []
    except Exception:
        devices = []
    for device in devices:
        name = device.get("name", "")
        if device.get("max_output_channels", 0) > 0 and (needle in name.lower() or name.lower() in needle):
            return name
    return None


def _configured_virtual_mic_device():
    """sounddevice output index matching CALL_VIRTUAL_MIC_DEVICE (e.g. 'CABLE Input'),
    or None if disabled, not in call mode, or device not found."""
    if not getattr(config, "CALL_MODE_ENABLED", False):
        return None
    configured = getattr(config, "CALL_VIRTUAL_MIC_DEVICE", None)
    if not configured or str(configured).lower() in ("none", "off", "disabled", ""):
        return None
    if isinstance(configured, int):
        return configured
    needle = str(configured).lower()
    try:
        devices = sd.query_devices() if sd is not None else []
    except Exception:
        devices = []
    for index, device in enumerate(devices):
        name = device.get("name", "")
        if device.get("max_output_channels", 0) > 0 and (needle in name.lower() or name.lower() in needle):
            return index
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
    """Reopen pygame mixer on the current AUDIO_OUTPUT_DEVICE (Settings live change)."""
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
    """Normalizes whitespace for TTS input. Ellipses become a plain period:
    neural voices hold a long trailing pause on "..." (e.g. "Opening chrome...")."""
    text = re.sub(r"\.{3,}|\u2026", ".", text or "")
    return " ".join(text.split())


# Words that commonly precede a period without ending a sentence.
_SENTENCE_ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "st", "jr", "sr", "prof",
    "vs", "etc", "e.g", "i.e", "approx",
}


def _split_sentences(text: str) -> list:
    """Split *text* into sentence-sized chunks for pipelined playback.
    Does not split decimals ("3.5") or abbreviations ("Dr.", "etc.").
    Conservative: under-splitting costs pipeline benefit; over-splitting speaks a fragment."""
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


def _group_sentences(sentences: list, max_chars: int = 220) -> list:
    """First sentence alone (fast first audio), the rest merged into longer chunks:
    every chunk seam restarts the voice and adds an audible gap."""
    if len(sentences) <= 2:
        return sentences
    groups = [sentences[0]]
    current = ""
    for sentence in sentences[1:]:
        if current and len(current) + 1 + len(sentence) > max_chars:
            groups.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}" if current else sentence
    if current:
        groups.append(current)
    return groups


def _volume_str_to_gain(volume_str: str) -> float:
    """config.EDGE_TTS_VOLUME ('+0%', '-20%', ...) → pygame mixer gain 0.0–1.0.

    Edge's own %-volume is unreliable; this applies the same % as real playback
    gain so the slider is always audible. Attenuates only — pygame can't boost
    without distortion — so 0% and any positive % play at 1.0; only negative
    values quiet. Positive still goes to Edge TTS in case it boosts."""
    try:
        percent = int(str(volume_str).strip().rstrip("%"))
    except (TypeError, ValueError):
        percent = 0
    return max(0.0, min(1.0, 1.0 + percent / 50.0))


_NO_AUDIO_YET = object()


class StreamingSpeaker:
    """Turns LLM deltas into clause-sized TTS work while generation continues."""

    _BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+|(?<=[,;:])\s+")
    _SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")

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
        self._chunks_emitted = 0
        self._realtime = (
            getattr(config, "TTS_STREAMING_ENABLED", True)
            and getattr(config, "TTS_PROVIDER", "edge") == "elevenlabs"
            and websocket_connect is not None
        )
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
            # File-based TTS (Edge) restarts the voice at every chunk, so only the
            # first chunk may end at a comma (fast first audio); later ones end at
            # sentences. The realtime socket keeps prosody across chunks itself.
            boundary_re = (
                self._BOUNDARY_RE
                if self._realtime or self._chunks_emitted == 0
                else self._SENTENCE_RE
            )
            match = boundary_re.search(self._pending)
            if match is None:
                break
            end = match.end()
            hard_boundary = self._pending[match.start() - 1] in ".!?"
            # Sentence punctuation flushes immediately. Soft clause punctuation
            # waits for TTS_CLAUSE_MIN_CHARACTERS; a later .!? still ends the chunk.
            while not hard_boundary and end < minimum:
                next_match = boundary_re.search(self._pending, end)
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
                self._chunks_emitted += 1
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
        try:
            if self._realtime:
                self._run_eleven_realtime()
            else:
                self._run_file_pipeline()
        finally:
            if self.on_playback_end is not None:
                self.on_playback_end()

    def _run_file_pipeline(self):
        """Synthesize the next chunk while the current one plays (no network
        round trip between chunks), merging any text that queued up meanwhile."""
        ready = queue.Queue(maxsize=1)  # one synthesized chunk of lookahead
        synth_thread = threading.Thread(target=self._synthesize_ahead, args=(ready,), daemon=True)
        synth_thread.start()
        try:
            while not self.interrupted:
                try:
                    path = ready.get(timeout=0.05)
                except queue.Empty:
                    continue
                if path is None:
                    return
                try:
                    self._start_playback()
                    if not _play(path, self.stop_event):
                        self.stop_event.set()
                except Exception as e:
                    print(f"(voice playback failed, continuing silently: {e})")
                finally:
                    _remove_quietly(path)
        finally:
            # On interrupt, return now; a daemon drains the in-flight synthesis and its temp files.
            threading.Thread(target=self._drain_ready, args=(ready, synth_thread), daemon=True).start()

    def _synthesize_ahead(self, ready):
        try:
            done = False
            while not done and not self.interrupted:
                chunk = self._chunks.get()
                if chunk is None:
                    return
                parts = [chunk]
                while True:
                    try:
                        extra = self._chunks.get_nowait()
                    except queue.Empty:
                        break
                    if extra is None:
                        done = True
                        break
                    parts.append(extra)
                try:
                    ready.put(_synthesize_to_temp_file(" ".join(parts)))
                except Exception as e:
                    print(f"(voice synthesis failed for one chunk, skipping: {e})")
        finally:
            ready.put(None)

    @staticmethod
    def _drain_ready(ready, synth_thread):
        while synth_thread.is_alive() or not ready.empty():
            try:
                _remove_quietly(ready.get(timeout=0.1))
            except queue.Empty:
                pass

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
            if self._playback_started:
                # Replaying the whole reply from the top made her say it twice.
                print(f"(realtime TTS failed mid-reply, stopping there: {exc})")
            elif not self.interrupted:
                print(f"(realtime TTS failed, falling back to file playback: {exc})")
                _speak_one(
                    self.text,
                    self._start_playback,
                    stop_event=self.stop_event,
                )

    def _queue_audio(self, chunk):
        if not self.interrupted:
            if chunk and getattr(config, "VOICE_DSP_ENABLED", False):
                try:
                    import voice_dsp
                    chunk = voice_dsp.process_pcm16_mono(chunk, sample_rate=24000)
                except Exception:
                    pass
            self._audio.put(chunk)

    def _next_audio(self):
        # Polls so a socket that goes quiet without a final message can't park this
        # thread forever (it would also keep is_playing() True and mute call ears).
        try:
            return self._audio.get(timeout=0.1)
        except queue.Empty:
            return _NO_AUDIO_YET

    def _finish_audio(self):
        self._audio.put(None)

    def _fail_audio(self, exc):
        self._audio_error = exc
        self._audio.put(None)

    def _play_pcm(self):
        with _counting_playback():
            if _discord_bot_connected():
                return self._play_pcm_to_discord()
            return self._play_pcm_to_speakers()

    def _play_pcm_to_speakers(self):
        buffer_bytes = max(
            1,
            int(24000 * 2 * getattr(config, "TTS_AUDIO_BUFFER_MS", 100) / 1000),
        )
        buffered = bytearray()
        stream = None
        vmic_stream = None
        vmic_device = _configured_virtual_mic_device()
        vmic_vol = float(getattr(config, "CALL_VIRTUAL_MIC_VOLUME", 1.0))

        def _open_vmic():
            if vmic_device is not None:
                try:
                    s = sd.RawOutputStream(
                        samplerate=24000,
                        channels=1,
                        dtype="int16",
                        blocksize=buffer_bytes // 2,
                        latency="low",
                        device=vmic_device,
                    )
                    s.start()
                    return s
                except Exception:
                    return None
            return None

        def _write_vmic(data: bytes):
            if vmic_stream is not None and data:
                try:
                    if abs(vmic_vol - 1.0) < 0.02:
                        vmic_stream.write(data)
                    else:
                        import numpy as np
                        arr = (np.frombuffer(data, dtype=np.int16).astype(np.float32) * vmic_vol)
                        vmic_stream.write(np.clip(arr, -32768, 32767).astype(np.int16).tobytes())
                except Exception:
                    pass

        try:
            while not self.interrupted:
                chunk = self._next_audio()
                if chunk is _NO_AUDIO_YET:
                    continue
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
                            vmic_stream = _open_vmic()
                            self._start_playback()
                        raw_data = bytes(buffered)
                        stream.write(raw_data)
                        _write_vmic(raw_data)
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
                    vmic_stream = _open_vmic()
                    self._start_playback()
                if stream is not None and buffered:
                    raw_data = bytes(buffered)
                    stream.write(raw_data)
                    _write_vmic(raw_data)
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
            if vmic_stream is not None:
                try:
                    vmic_stream.abort() if self.interrupted else vmic_stream.stop()
                finally:
                    vmic_stream.close()
            self._audio_done.set()

    def _play_pcm_to_discord(self):
        """Streamed (ElevenLabs realtime) audio, sent to the Discord voice channel."""
        import discord_bot

        started = False
        queued_seconds = 0.0
        try:
            while not self.interrupted:
                chunk = self._next_audio()
                if chunk is _NO_AUDIO_YET:
                    continue
                if chunk is None:
                    break
                queued_seconds += len(chunk) / (24000 * 2)
                if not discord_bot.play_pcm(discord_bot.to_discord_pcm(chunk, 24000, 1)):
                    # Before playback started this falls back to file playback on the speakers.
                    raise RuntimeError(f"Discord voice playback failed ({discord_bot._cant_speak})")
                if not started:
                    self._start_playback()
                    started = True
            discord_bot.wait_for_playback(queued_seconds, self.stop_event)
            if self.interrupted:
                discord_bot.stop_audio()
        except Exception as exc:
            self._audio_error = exc
        finally:
            self._audio_done.set()


def _discord_bot_connected() -> bool:
    try:
        import discord_bot
        return discord_bot.can_speak()
    except Exception:
        return False


def _remove_quietly(path):
    if path:
        try:
            os.remove(path)
        except OSError:
            pass


def warm_up():
    """Throwaway synthesis + mixer init so the first reply doesn't pay startup
    cost (event loop, audio device, Edge handshake). Safe from a background
    thread; idempotent.

    Skips the network round trip when TTS_PROVIDER is "elevenlabs" (paid quota).
    Event loop and mixer still start."""
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


def _play_file_to_virtual_mic(path: str, stop_event=None):
    vmic_device = _configured_virtual_mic_device()
    if vmic_device is None:
        return
    try:
        pcm_bytes = None
        sr = 24000
        ch = 1

        if path.lower().endswith(".wav"):
            try:
                import wave
                with wave.open(path, "rb") as wf:
                    if wf.getsampwidth() == 2:
                        sr = wf.getframerate()
                        ch = wf.getnchannels()
                        pcm_bytes = wf.readframes(wf.getnframes())
            except Exception:
                pcm_bytes = None

        if pcm_bytes is None:
            # Universal decoding via Pygame Sound (handles mp3, wav, ogg)
            try:
                import pygame
                snd = pygame.mixer.Sound(path)
                arr = pygame.sndarray.array(snd)
                if arr is not None and arr.size > 0:
                    init_res = pygame.mixer.get_init()
                    if init_res:
                        sr = init_res[0]
                    if arr.ndim > 1:
                        ch = arr.shape[1]
                    else:
                        ch = 1
                    pcm_bytes = arr.astype(np.int16).tobytes()
            except Exception:
                pcm_bytes = None

        if pcm_bytes is None:
            # Fallback or MP3 decoding via PyAV
            try:
                import av
                container = av.open(path)
                resampler = av.AudioResampler(format="s16", layout="mono", rate=24000)
                chunks = []
                for frame in container.decode(audio=0):
                    for r in resampler.resample(frame):
                        chunks.append(r.to_ndarray().tobytes())
                container.close()
                if chunks:
                    pcm_bytes = b"".join(chunks)
                    sr = 24000
                    ch = 1
            except Exception:
                pass

        if not pcm_bytes:
            return

        vmic_vol = float(getattr(config, "CALL_VIRTUAL_MIC_VOLUME", 1.0))
        target_sr = sr
        if sd is not None:
            try:
                dev_info = sd.query_devices(vmic_device, "output")
                native_sr = int(dev_info.get("default_samplerate", 0) or 0)
                if native_sr > 0 and native_sr != sr:
                    target_sr = native_sr
            except Exception:
                target_sr = sr

        if target_sr != sr and pcm_bytes:
            import numpy as np
            raw_arr = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
            if ch > 1:
                raw_arr = raw_arr.reshape(-1, ch)
                target_len = int(round(len(raw_arr) * target_sr / float(sr)))
                cols = []
                for c in range(ch):
                    idx = np.linspace(0, len(raw_arr) - 1, target_len, dtype=np.float32)
                    cols.append(np.interp(idx, np.arange(len(raw_arr), dtype=np.float32), raw_arr[:, c]))
                pcm_bytes = np.column_stack(cols).astype(np.int16).tobytes()
            else:
                target_len = int(round(len(raw_arr) * target_sr / float(sr)))
                idx = np.linspace(0, len(raw_arr) - 1, target_len, dtype=np.float32)
                pcm_bytes = np.interp(idx, np.arange(len(raw_arr), dtype=np.float32), raw_arr).astype(np.int16).tobytes()
            sr = target_sr

        vmic_stream = None
        for try_rate in (sr, 48000, 44100):
            try:
                if try_rate != sr and pcm_bytes:
                    import numpy as np
                    raw_arr = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
                    target_len = int(round(len(raw_arr) * try_rate / float(sr)))
                    idx = np.linspace(0, len(raw_arr) - 1, target_len, dtype=np.float32)
                    pcm_bytes = np.interp(idx, np.arange(len(raw_arr), dtype=np.float32), raw_arr).astype(np.int16).tobytes()
                    sr = try_rate
                vmic_stream = sd.RawOutputStream(
                    samplerate=sr,
                    channels=ch,
                    dtype="int16",
                    device=vmic_device,
                )
                vmic_stream.start()
                break
            except Exception:
                vmic_stream = None

        if vmic_stream is None:
            return

        try:
            chunk_size = sr * ch * 2 // 10  # ~100ms
            offset = 0
            while not (stop_event and stop_event.is_set()) and offset < len(pcm_bytes):
                data = pcm_bytes[offset : offset + chunk_size]
                offset += chunk_size
                if not data:
                    break
                if abs(vmic_vol - 1.0) < 0.02:
                    vmic_stream.write(data)
                else:
                    import numpy as np
                    arr = (np.frombuffer(data, dtype=np.int16).astype(np.float32) * vmic_vol)
                    vmic_stream.write(np.clip(arr, -32768, 32767).astype(np.int16).tobytes())
        finally:
            try:
                vmic_stream.stop()
            finally:
                vmic_stream.close()
    except Exception:
        pass


def _play(path: str, stop_event=None) -> bool:
    """Play *path* to completion, or stop when *stop_event* is set.
    True if finished on its own, False if cut off."""
    with _play_lock:
        return _play_locked(path, stop_event)


def _play_locked(path: str, stop_event=None) -> bool:
    with _counting_playback():
        return _play_file_locked(path, stop_event)


def _play_file_locked(path: str, stop_event=None) -> bool:
    _ensure_mixer()
    music = pygame.mixer.music
    play_path = path
    dsp_temp = None
    if getattr(config, "VOICE_DSP_ENABLED", False):
        try:
            import voice_dsp
            dsp_temp = voice_dsp.process_audio_file(path)
            if dsp_temp:
                play_path = dsp_temp
        except Exception as e:
            print(f"(voice DSP processing failed, falling back to original: {e})")

    vmic_thread = None
    try:
        if _discord_bot_connected():
            # She's in a Discord voice channel as the bot: speak there instead of the speakers.
            import discord_bot
            played = discord_bot.play_file(play_path, stop_event)
            if played is not None:
                return played
        music.load(play_path)
        volume = getattr(config, "EDGE_TTS_VOLUME", "+0%")
        music.set_volume(_volume_str_to_gain(volume))
        music.play()
        if _configured_virtual_mic_device() is not None:
            vmic_thread = threading.Thread(
                target=_play_file_to_virtual_mic, args=(play_path, stop_event), daemon=True
            )
            vmic_thread.start()
        last_pos, last_progress = -1, time.monotonic()
        while music.get_busy():
            current_volume = getattr(config, "EDGE_TTS_VOLUME", "+0%")
            if current_volume != volume:
                volume = current_volume
                music.set_volume(_volume_str_to_gain(volume))
            if stop_event is not None and stop_event.is_set():
                return False
            pos, now = music.get_pos(), time.monotonic()
            if pos != last_pos:
                last_pos, last_progress = pos, now
            elif now - last_progress > _PLAYBACK_STALL_SECONDS:
                print("(audio output stalled - skipping the rest of this line)")
                return True
            time.sleep(0.02)
        return True
    finally:
        try:
            music.stop()
        finally:
            try:
                music.unload()
            except Exception:
                pass
            if vmic_thread is not None:
                try:
                    # It trails the speakers a little; let it finish so the next line
                    # doesn't start over its tail in the call.
                    vmic_thread.join(timeout=2.0)
                except Exception:
                    pass
            if dsp_temp:
                try:
                    os.remove(dsp_temp)
                except OSError:
                    def _deferred_unlink(p):
                        for _ in range(8):
                            time.sleep(0.25)
                            try:
                                if os.path.exists(p):
                                    os.remove(p)
                                break
                            except OSError:
                                pass
                    threading.Thread(target=_deferred_unlink, args=(dsp_temp,), daemon=True).start()


def _speak_one(text: str, on_playback_start=None, on_playback_end=None, stop_event=None) -> bool:
    """Synthesize the whole reply, then play. Single-sentence / pipeline-off fallback.
    True if interrupted (stop_event fired before/during playback)."""
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

    Current + one lookahead synthesize concurrently. On interrupt, unstarted
    work stays cancelled; running daemon jobs finish and temps are removed in
    the background.
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
    """Synthesize and play *text*. Callbacks wrap actual playback, not synthesis,
    so animation starts when audio is about to be heard (synthesis can take a
    noticeable network round trip).

    Multi-sentence replies pipeline unless TTS_PIPELINE_SENTENCES is False.

    stop_event: another thread can cut playback (see main.speak /
    recorder.listen_for_barge_in). Checked before each sentence and polled
    during play. True if cut short, False if finished / no stop_event.
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
        sentences = _group_sentences(_split_sentences(text))
    else:
        sentences = [text]

    if len(sentences) <= 1:
        return _speak_one(text, _timed_on_playback_start, on_playback_end, stop_event)
    else:
        return _speak_pipelined(sentences, _timed_on_playback_start, on_playback_end, stop_event)
