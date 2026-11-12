"""Alyssa as a Discord bot: she joins your voice channel as her own account and talks there.

Setup (Settings > Assistant > Discord bot):
1. https://discord.com/developers/applications > New Application > Bot > Reset Token, copy it.
2. OAuth2 > URL Generator: scope "bot", permissions "Connect" and "Speak". Open the link and
   add her to your server.
3. Paste the token and your Discord user ID (Discord Settings > Advanced > Developer Mode,
   then right-click your name > Copy User ID) into Settings.

Speaking needs discord.py 2.7+ (it handles Discord's voice encryption through `davey`).

Listening, two ways:
- Bot ears (best: knows who's talking): needs a voice-receive extension that can decrypt
  Discord's DAVE voice, installed by hand (see README). Experimental.
- PC ears (backup): records what this PC plays while you're in the call too. Used when bot
  ears aren't installed, fail to start, or go deaf (the PC hears talking, the bot hears nothing).
Either way speech is transcribed on this PC, and the AI is only called when someone says her
name (see call_ears.handle_friend_line), so listening costs no tokens.
"""
from __future__ import annotations

import asyncio
import threading
import time

import numpy as np

import config
import credential_store

try:
    import discord
except ImportError:  # optional: only needed when the bot is switched on
    discord = None
try:
    from discord.ext import voice_recv
except ImportError:
    voice_recv = None

TOKEN_SECRET = "DISCORD_BOT_TOKEN"
FRAME_BYTES = 3840  # 20 ms of 48 kHz stereo s16, what discord.py's encoder reads
_MAX_UNDERRUN_FRAMES = 15  # keep playing silence this long while streamed audio catches up

_lock = threading.Lock()
_client = None
_loop: asyncio.AbstractEventLoop | None = None
_thread: threading.Thread | None = None
_status = "off"
_audio = bytearray()
_audio_lock = threading.Lock()
_listener = None
_cant_speak = ""  # why playing in the channel failed; her voice goes to the speakers until she rejoins


# ---------------------------------------------------------------- audio out

def to_discord_pcm(pcm: bytes, rate: int, channels: int) -> bytes:
    """s16 PCM at any rate/channel count -> 48 kHz stereo s16 (Discord's format)."""
    samples = np.frombuffer(pcm[: len(pcm) - len(pcm) % (2 * channels)], dtype=np.int16)
    if not samples.size:
        return b""
    mono = samples.reshape(-1, channels).mean(axis=1) if channels > 1 else samples.astype(np.float32)
    if rate != 48000:
        n_out = int(len(mono) * 48000 / rate)
        mono = np.interp(np.linspace(0, len(mono) - 1, n_out), np.arange(len(mono)), mono)
    volume = float(getattr(config, "CALL_VIRTUAL_MIC_VOLUME", 1.0))
    mono = np.clip(mono * volume, -32768, 32767).astype(np.int16)
    return np.repeat(mono, 2).tobytes()


def decode_file(path: str) -> bytes:
    """Any audio file (mp3/wav) -> 48 kHz stereo s16, via PyAV (installed with faster-whisper)."""
    import av

    out = []
    with av.open(path) as container:
        resampler = av.AudioResampler(format="s16", layout="stereo", rate=48000)
        for frame in container.decode(audio=0):
            out.extend(r.to_ndarray().tobytes() for r in resampler.resample(frame))
        out.extend(r.to_ndarray().tobytes() for r in resampler.resample(None))
    volume = float(getattr(config, "CALL_VIRTUAL_MIC_VOLUME", 1.0))
    pcm = b"".join(out)
    if abs(volume - 1.0) > 0.02:
        pcm = np.clip(np.frombuffer(pcm, np.int16) * volume, -32768, 32767).astype(np.int16).tobytes()
    return pcm


if discord is not None:
    class _QueueSource(discord.AudioSource):
        """Plays whatever is in _audio; ends after a short silence once it runs dry."""

        def __init__(self):
            self._underruns = 0

        def read(self) -> bytes:
            with _audio_lock:
                if len(_audio) >= FRAME_BYTES:
                    frame = bytes(_audio[:FRAME_BYTES])
                    del _audio[:FRAME_BYTES]
                    self._underruns = 0
                    return frame
                if _audio:
                    frame = bytes(_audio).ljust(FRAME_BYTES, b"\0")
                    _audio.clear()
                    return frame
            self._underruns += 1
            return b"\0" * FRAME_BYTES if self._underruns <= _MAX_UNDERRUN_FRAMES else b""


def _voice_client():
    vcs = getattr(_client, "voice_clients", None) or []
    return vcs[0] if vcs and vcs[0].is_connected() else None


def connected() -> bool:
    """True while she's in a voice channel as the bot; her speech then goes there."""
    return _client is not None and _voice_client() is not None


def can_speak() -> bool:
    """True while her voice should go to the channel (connected, and playing there works)."""
    return not _cant_speak and connected()


def play_pcm(pcm48_stereo: bytes) -> bool:
    """Queue 48 kHz stereo s16 audio to say in the channel. False if Discord won't play it."""
    global _cant_speak, _status
    if not pcm48_stereo:
        return True
    with _audio_lock:
        _audio.extend(pcm48_stereo)
    try:
        _run(_ensure_playing(), timeout=5)
        return True
    except Exception as e:  # e.g. Opus missing: say why and fall back to the speakers
        stop_audio()
        _cant_speak = f"{type(e).__name__}: {e}"
        _status = f"in the call but can't speak there ({_cant_speak})"
        print(f"[discord] Couldn't play audio in the voice channel: {_cant_speak}")
        return False


async def _ensure_playing():
    vc = _voice_client()
    if vc is None:
        raise RuntimeError("not in a voice channel")
    if not vc.is_playing():
        vc.play(_QueueSource())


def busy() -> bool:
    with _audio_lock:
        if _audio:
            return True
    vc = _voice_client()
    return vc is not None and vc.is_playing()


def stop_audio() -> None:
    with _audio_lock:
        _audio.clear()


def play_file(path: str, stop_event=None) -> bool | None:
    """Say an audio file in the channel and wait. True if it finished, False if cut off,
    None if Discord couldn't play it (the caller then plays it on the speakers)."""
    if not play_pcm(decode_file(path)):
        return None
    while busy():
        if stop_event is not None and stop_event.is_set():
            stop_audio()
            return False
        time.sleep(0.02)
    return True


# ---------------------------------------------------------------- lifecycle

def owner_id() -> int:
    try:
        return int(str(getattr(config, "DISCORD_OWNER_ID", "") or "0").strip())
    except ValueError:
        return 0


def status() -> str:
    return _status


def can_listen() -> bool:
    return voice_recv is not None


def start() -> str | None:
    """Log the bot in if it's switched on. Returns a problem to show, or None."""
    global _client, _loop, _thread, _status
    if not getattr(config, "DISCORD_BOT_ENABLED", False):
        stop()
        return None
    if discord is None:
        _status = "discord.py isn't installed"
        return "The Discord bot needs discord.py: run pip install \"discord.py[voice]\"."
    token = credential_store.get_secret(TOKEN_SECRET).strip()
    if not token:
        _status = "no token"
        return "Paste your Discord bot token in Settings first."
    with _lock:
        if _thread is not None and _thread.is_alive():
            return None
        intents = discord.Intents.none()
        intents.guilds = True
        intents.voice_states = True  # to find the voice channel you're in
        _loop = asyncio.new_event_loop()
        _status = "connecting"

        def run():
            global _client, _status
            asyncio.set_event_loop(_loop)
            client = discord.Client(intents=intents)
            _client = client

            @client.event
            async def on_ready():
                global _status
                _status = f"online as {client.user}"
                print(f"[discord] Logged in as {client.user}.")

            try:
                _loop.run_until_complete(client.start(token))
            except discord.LoginFailure:
                _status = "the token was rejected"
                print("[discord] Login failed: check the bot token in Settings.")
            except Exception as e:  # network down etc.: report, don't kill Alyssa
                _status = f"stopped ({e})"
                print(f"[discord] Bot stopped: {e}")
            finally:
                _client = None

        _thread = threading.Thread(target=run, daemon=True, name="discord-bot")
        _thread.start()
    return None


def stop() -> None:
    global _status
    client, loop, thread = _client, _loop, _thread
    if client is not None and loop is not None and loop.is_running():
        asyncio.run_coroutine_threadsafe(client.close(), loop)
    if thread is not None and thread is not threading.current_thread():
        thread.join(timeout=5)
    _status = "off"


def _run(coro, timeout: float = 20.0):
    if _client is None or _loop is None or not _loop.is_running():
        raise RuntimeError("The Discord bot isn't online yet.")
    return asyncio.run_coroutine_threadsafe(coro, _loop).result(timeout)


def invite_url() -> str:
    """Link that adds the bot to a server with Connect + Speak, once it has logged in."""
    user = getattr(_client, "user", None)
    if user is None:
        return ""
    return f"https://discord.com/oauth2/authorize?client_id={user.id}&scope=bot&permissions=3145728"


def ready() -> bool:
    return _client is not None and _client.is_ready()


# ---------------------------------------------------------------- joining

def _norm(text: str) -> str:
    return "".join(ch for ch in (text or "").lower() if ch.isalnum())


def find_channel(guilds, owner: int, server: str = "", channel: str = ""):
    """The voice channel to join: the one you're in, else the named one. None if unclear."""
    wanted_server, wanted_channel = _norm(server), _norm(channel)
    named = []
    for guild in guilds:
        if wanted_server and wanted_server not in _norm(guild.name):
            continue
        for vc in guild.voice_channels:
            if owner and owner in vc.voice_states:
                return vc
            if wanted_channel and wanted_channel in _norm(vc.name):
                named.append(vc)
    return named[0] if len(named) == 1 or (named and wanted_server) else None


async def _join(server: str, channel: str) -> str:
    global _cant_speak
    target = find_channel(_client.guilds, owner_id(), server, channel)
    if target is None:
        if not _client.guilds:
            return "I'm not in any server yet. Use the invite link from Settings to add me."
        return "I couldn't tell which voice channel to join. Hop into one first, or tell me the server and channel."
    vc = _voice_client()
    if vc is not None:
        await vc.move_to(target)
    else:
        cls = voice_recv.VoiceRecvClient if voice_recv is not None else discord.VoiceClient
        vc = await target.connect(cls=cls)  # never self-deafen: friends think she can't hear them
    _cant_speak = ""
    _start_listening(vc)
    return f"I'm in #{target.name} on {target.guild.name}."


def join(server: str = "", channel: str = "") -> str:
    """Join your current voice channel (or the named one). Returns a sentence to say."""
    try:
        return _run(_join(server, channel))
    except Exception as e:
        return f"I couldn't join the voice channel: {e}"


async def _leave() -> str:
    vc = _voice_client()
    if vc is None:
        return "I wasn't in a voice channel."
    await vc.disconnect()
    _stop_listening()
    return "Left the voice channel."


def leave() -> str:
    stop_audio()
    try:
        return _run(_leave())
    except Exception as e:
        return f"I couldn't leave the voice channel: {e}"


# ---------------------------------------------------------------- listening

_pc_listener = None
_listen_mode = "off"  # "bot" (her Discord connection hears each friend), "pc" (backup), "off"


def listen_mode() -> str:
    return _listen_mode


def _start_listening(vc) -> None:
    """Listen through the bot when the receive extension works; otherwise hear the PC's audio.

    In bot mode a PC-audio meter keeps running (no transcription, no tokens) so that if the
    call is clearly audible on this PC but the bot gets no voice packets, we switch over.
    """
    global _listener, _pc_listener, _listen_mode
    _stop_listening()
    if not getattr(config, "DISCORD_LISTEN", True):
        return
    if voice_recv is not None and hasattr(vc, "listen"):
        try:
            _listener = FriendListener()
            vc.listen(voice_recv.BasicSink(_listener.on_audio))
            _listen_mode = "bot"
            _pc_listener = PCListener(transcribe=False, on_speech=_check_fallback)
            return
        except Exception as e:
            print(f"[discord] Bot listening failed ({e}); using the PC's audio instead.")
            if _listener is not None:
                _listener.stop()
    _pc_listener = PCListener(transcribe=True)
    _listen_mode = "pc"


def _stop_listening() -> None:
    global _listener, _pc_listener, _listen_mode
    for listener in (_listener, _pc_listener):
        if listener is not None:
            listener.stop()
    _listener = _pc_listener = None
    _listen_mode = "off"


FALLBACK_AFTER_SECONDS = 30.0


def should_fall_back(pc_speech_times, last_bot_packet: float, now: float) -> bool:
    """The PC clearly heard people talking for a while, but the bot got nothing."""
    recent = [t for t in pc_speech_times if now - t <= FALLBACK_AFTER_SECONDS]
    return len(recent) >= 3 and now - last_bot_packet > FALLBACK_AFTER_SECONDS


def _check_fallback(pc_listener) -> None:
    global _listen_mode
    bot = _listener
    if _listen_mode != "bot" or bot is None:
        return
    if not should_fall_back(pc_listener.speech_times, bot.last_packet, time.monotonic()):
        return
    print("[discord] The bot isn't hearing the call (voice decryption may have broken); "
          "switching to the PC's audio.")
    bot.stop()
    vc = _voice_client()
    if vc is not None and _loop is not None and hasattr(vc, "stop_listening"):
        _loop.call_soon_threadsafe(vc.stop_listening)
    pc_listener.transcribe = True
    _listen_mode = "pc"


class FriendListener:
    """Cuts each friend's audio into utterances and hands them to call_ears off the audio thread.

    Discord only sends packets while someone talks, so a short gap in packets ends an utterance.
    """

    GAP_SECONDS = 0.8
    MIN_SECONDS = 0.5
    MAX_SECONDS = 15.0

    def __init__(self, handle=None):
        self._buffers: dict[int, tuple[str, bytearray, float]] = {}
        self._lock = threading.Lock()
        self._handle = handle
        self._stopped = threading.Event()
        self.last_packet = time.monotonic()
        threading.Thread(target=self._loop, daemon=True, name="discord-listen").start()

    def on_audio(self, user, data) -> None:
        if user is None or getattr(user, "bot", False) or user.id == owner_id():
            return  # your own voice reaches her through your mic as usual
        pcm = getattr(data, "pcm", b"")
        if not pcm:
            return
        self.last_packet = time.monotonic()
        with self._lock:
            name, buf, _ = self._buffers.get(user.id, (user.display_name, bytearray(), 0.0))
            buf.extend(pcm)
            self._buffers[user.id] = (name, buf, time.monotonic())

    def finished(self, now: float) -> list[tuple[str, bytes]]:
        """Utterances whose speaker went quiet (or talked too long), removed from the buffers."""
        done = []
        with self._lock:
            for uid, (name, buf, last) in list(self._buffers.items()):
                seconds = len(buf) / (48000 * 4)
                if now - last >= self.GAP_SECONDS or seconds >= self.MAX_SECONDS:
                    del self._buffers[uid]
                    if seconds >= self.MIN_SECONDS:
                        done.append((name, bytes(buf)))
        return done

    def _loop(self) -> None:
        while not self._stopped.wait(0.2):
            for name, pcm in self.finished(time.monotonic()):
                try:
                    (self._handle or _handle_utterance)(name, pcm)
                except Exception as e:
                    print(f"[discord] Couldn't handle what {name} said: {e}")

    def stop(self) -> None:
        self._stopped.set()


def to_whisper_audio(pcm48_stereo: bytes) -> np.ndarray:
    """48 kHz stereo s16 -> 16 kHz mono float32, what faster-whisper takes."""
    samples = np.frombuffer(pcm48_stereo[: len(pcm48_stereo) // 12 * 12], dtype=np.int16)
    mono = samples.reshape(-1, 2).mean(axis=1)
    return (mono.reshape(-1, 3).mean(axis=1) / 32768.0).astype(np.float32)


def _handle_utterance(name: str, pcm: bytes) -> None:
    _transcribe_and_handle(name, to_whisper_audio(pcm))


def _transcribe_and_handle(name: str, audio: np.ndarray) -> None:
    import call_ears
    import transcribe

    if not audio.size or float(np.sqrt(np.mean(audio ** 2))) < 0.01:
        return  # breathing / background hum: don't even transcribe
    with transcribe._model_lock:  # local Whisper only: no cloud STT, no tokens
        text = transcribe._transcribe_impl_locked(audio)
    if text and text.strip():
        call_ears.handle_friend_line(name, text.strip())


class EnergySegmenter:
    """Cuts a 16 kHz mono stream into speech segments by loudness (no ML, nearly free)."""

    THRESHOLD = 0.02
    GAP_SECONDS = 0.8
    MIN_SECONDS = 0.5
    MAX_SECONDS = 15.0

    def __init__(self):
        self._chunks: list[np.ndarray] = []
        self._quiet = 0.0

    def feed(self, block: np.ndarray, muted: bool = False) -> list[np.ndarray]:
        """Add one block; returns any finished segments. Muted blocks (her own voice) are dropped."""
        seconds = len(block) / 16000
        loud = not muted and float(np.sqrt(np.mean(block ** 2))) >= self.THRESHOLD
        done = []
        if loud:
            self._chunks.append(block)
            self._quiet = 0.0
        elif self._chunks:
            self._quiet += seconds
            if not muted:
                self._chunks.append(block)
        total = sum(len(c) for c in self._chunks) / 16000
        if self._chunks and (self._quiet >= self.GAP_SECONDS or total >= self.MAX_SECONDS or muted):
            if total - self._quiet >= self.MIN_SECONDS:
                done.append(np.concatenate(self._chunks))
            self._chunks, self._quiet = [], 0.0
        return done


class PCListener:
    """Backup ears: records what this PC plays (WASAPI loopback) while you're in the call.

    It can't tell friends apart and also hears game sound, so it's only used when bot
    listening isn't available or stops working. With transcribe=False it only notes when it
    hears speech, for the fallback check.
    """

    BLOCK = 1600  # 0.1 s at 16 kHz

    def __init__(self, transcribe: bool = True, on_speech=None):
        self.transcribe = transcribe
        self.speech_times: list[float] = []
        self._on_speech = on_speech
        self._stopped = threading.Event()
        self._muted_until = 0.0
        threading.Thread(target=self._run, daemon=True, name="discord-pc-ears").start()

    def stop(self) -> None:
        self._stopped.set()

    def _run(self) -> None:
        try:
            import soundcard as sc

            speaker = sc.default_speaker()
            mic = sc.get_microphone(id=str(speaker.name), include_loopback=True)
        except Exception as e:  # not Windows, no audio device, package missing
            print(f"[discord] Can't listen to the PC's audio: {e}")
            return
        segmenter = EnergySegmenter()
        with mic.recorder(samplerate=16000, channels=1, blocksize=self.BLOCK) as recorder:
            while not self._stopped.is_set():
                block = recorder.record(numframes=self.BLOCK)[:, 0].astype(np.float32)
                now = time.monotonic()
                if busy():
                    self._muted_until = now + 0.5  # her own voice comes back through your Discord
                for segment in segmenter.feed(block, muted=now < self._muted_until):
                    self.speech_times = [t for t in self.speech_times if now - t < 120] + [now]
                    if self._on_speech is not None:
                        self._on_speech(self)
                    if self.transcribe:
                        threading.Thread(
                            target=_transcribe_and_handle, args=("Someone in the call", segment), daemon=True
                        ).start()
