"""Main listening loop and application entrypoint."""
import faulthandler
import queue
import random
import re
import os
import sys
import threading
import time

# PyInstaller: config.py is next to the exe, not bundled; edits take effect without rebuild.
if getattr(sys, "frozen", False):
    sys.path.insert(0, os.path.dirname(sys.executable))
    _config_dir = os.path.dirname(sys.executable)
else:
    _config_dir = os.path.dirname(os.path.abspath(__file__))

# Startup can inherit an arbitrary cwd; chdir to the config dir.
os.chdir(_config_dir)

import startup_logging
_startup_log_path = startup_logging.configure(_config_dir)
print(
    f"Starting Alyssa: executable={sys.executable!r}, app_dir={_config_dir!r}, "
    f"cwd={os.getcwd()!r}, frozen={bool(getattr(sys, 'frozen', False))}"
)

import updater
if "--uninstall-cleanup" in sys.argv:  # run by Alyssa-Setup's uninstaller
    updater.uninstall_cleanup()
    sys.stdout.flush()
    os._exit(0)
# An update downloaded last session finishes now if Alyssa was quit instead of restarted.
if updater.launch_pending_update(_config_dir, sys.executable if getattr(sys, "frozen", False) else ""):
    sys.stdout.flush()
    os._exit(0)

if sys.stdin is None:
    sys.stdin = open(os.devnull, "r")

# Migrate leftover plaintext API-key literals from config.py into the OS
# keyring BEFORE importing config, so get_secret() already sees them.
import credential_store as _cred
_migrated = _cred.migrate_legacy_plaintext_keys(os.path.join(_config_dir, "config.py"))
if _migrated:
    print(f"Moved {', '.join(_migrated)} from config.py into the OS keyring.")

import config
_environment_credentials_present = sorted(
    name
    for name in (
        "GEMINI_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "CUSTOM_LLM_API_KEY",
        "SPOTIFY_CLIENT_ID",
        "SPOTIFY_CLIENT_SECRET",
        "YOUTUBE_API_KEY",
    )
    if os.environ.get(name)
)
print(
    f"Startup configuration: provider={config.LLM_PROVIDER!r}, "
    f"microphone={getattr(config, 'MICROPHONE_DEVICE', 'default')!r}, "
    "environment_credentials_present="
    f"{_environment_credentials_present}"
)


def _apply_startup_console_setting():
    """Hide the Windows console before the rest of Alyssa initializes."""
    if sys.platform != "win32" or not getattr(config, "HIDE_CONSOLE_WINDOW", False):
        return
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
    except Exception:
        pass


_apply_startup_console_setting()

import requests
import recorder
import transcribe
import brain
import actions
import voice
import plugin_loader
import nameutil
import telemetry
import sources
import fondness
import phone_remote

# Serializes speak() between the listen loop, the watcher thread and replies to
# friends in a Discord call, so two voices never overlap. Others wait; nothing is
# dropped. Reentrant: a tool may speak while her reply holds it.
_speak_lock = voice.speech_lock

# Longest the reply waits for the barge-in listener to finish recording what you
# said over her (it stops on its own after MAX_RECORD_SECONDS).
_INTERRUPT_JOIN_GRACE_SECONDS = 2.0


def _join_listener(listener_thread, stop_event):
    """Wait for the barge-in listener. When it cut her off it is still recording what you
    said; returning after 1s dropped that command and opened a second mic stream."""
    if stop_event.is_set():
        timeout = config.MAX_RECORD_SECONDS + _INTERRUPT_JOIN_GRACE_SECONDS
    else:
        timeout = 1.0  # stream.read() can block forever; the listener is a daemon
    listener_thread.join(timeout=timeout)


def _clean_reply_for_speech(text: str) -> str:
    """Removes stray leading punctuation, markdown, emoji and assistant-speak (brain.persona)."""
    from brain.persona import tidy_reply

    return tidy_reply(re.sub(r"^[\s,;:—–-]+", "", (text or ""))).strip()


def speak(text: str, bridge=None):
    """Speak *text*. Returns barge-in audio (numpy array for transcribe.transcribe)
    or None if playback finished uncut / SPEAK_RESPONSES or ALLOW_INTERRUPTIONS is off.

    Background recorder.listen_for_barge_in sets stop_speaking_event (~20ms stop)
    and records the rest as the next command."""
    text = _clean_reply_for_speech(text)
    if not text:
        return None

    with _speak_lock:
        print(
            startup_logging.format_content_log(
                "assistant response received",
                text,
                full_text=f"{config.ASSISTANT_NAME}: {text}",
            )
        )
        phone_remote.log("alyssa", text)
        if bridge is not None:
            if config.SPEAK_RESPONSES:
                bridge.reply_pending_signal.emit()
            bridge.speak_signal.emit(text)
        if not config.SPEAK_RESPONSES:
            return None

        stop_speaking_event = threading.Event()
        playback_done_event = threading.Event()
        interrupt_result = {"audio": None}

        def listen():
            interrupt_result["audio"] = recorder.listen_for_barge_in(
                stop_speaking_event, playback_done_event
            )

        listener_thread = threading.Thread(target=listen, daemon=True)
        listener_thread.start()
        try:
            voice.speak(
                text,
                on_playback_start=(bridge.talk_start_signal.emit if bridge else None),
                on_playback_end=(bridge.talk_end_signal.emit if bridge else None),
                stop_event=stop_speaking_event,
            )
        finally:
            playback_done_event.set()
            _join_listener(listener_thread, stop_speaking_event)

        return interrupt_result["audio"]


def _already_spoken(streamed_text: str, reply: str) -> bool:
    """True if the streamed text already ends with the final reply. Compared without
    spacing and fake tool-call JSON (which the final reply has stripped), since any
    difference there made her read the whole reply a second time."""
    from brain.text_utils import _strip_fake_tool_call

    def norm(text):
        return " ".join(_strip_fake_tool_call(text or "").split())

    return norm(streamed_text).endswith(norm(reply))


def _stream_command_reply(command: str, bridge=None, text_queue=None):
    """Overlap model generation, clause synthesis, playback, and barge-in."""
    with _speak_lock:
        turn_started = time.time()
        stop_event = threading.Event()
        response_done_event = threading.Event()
        interrupt_result = {"audio": None}
        streamed_parts = []
        pending_emitted = False
        last_ui_emit = 0.0

        def listen():
            interrupt_result["audio"] = recorder.listen_for_barge_in(
                stop_event, response_done_event, text_queue=text_queue
            )

        def on_playback_start():
            telemetry.log(
                f"[timing] speech-end-to-first-audio: "
                f"{time.time() - turn_started:.2f}s"
            )
            if bridge is not None:
                bridge.talk_start_signal.emit()

        speaker = voice.StreamingSpeaker(
            on_playback_start=on_playback_start,
            on_playback_end=(bridge.talk_end_signal.emit if bridge else None),
            stop_event=stop_event,
            clean=_clean_reply_for_speech,
            on_chunk_start=(bridge.spoken_signal.emit if bridge else None),
        )
        listener_thread = threading.Thread(target=listen, daemon=True)
        listener_thread.start()
        speaker_finished = False
        sources.clear()
        phone_remote.log("you", command)
        steps = []

        def on_step(label, state):
            if bridge is None:
                return
            if state == "running":
                steps.append([label, state])
            elif steps:
                steps[-1][1] = state
            bridge.steps_signal.emit([tuple(step) for step in steps])

        def show_and_feed(delta):
            nonlocal pending_emitted, last_ui_emit
            if not delta or stop_event.is_set():
                return
            streamed_parts.append(delta)
            speaker.feed(delta)
            if bridge is None:
                return
            if not pending_emitted:
                bridge.reply_pending_signal.emit()
                pending_emitted = True
            now = time.time()
            if re.search(r"[.!?,;:]\s*$", delta) or now - last_ui_emit >= 0.1:
                bridge.speak_signal.emit(_clean_reply_for_speech("".join(streamed_parts)))
                last_ui_emit = now

        def on_partial_reply(text):
            text = _clean_reply_for_speech(text)
            if not text or stop_event.is_set():
                return
            show_and_feed(text + " ")

        try:
            reply = brain.handle_command(
                command,
                on_partial_reply=on_partial_reply,
                on_text_delta=show_and_feed,
                cancel_event=stop_event,
                on_step=on_step,
            )
            streamed_text = _clean_reply_for_speech("".join(streamed_parts))
            clean_reply = _clean_reply_for_speech(reply)
            if (
                clean_reply
                and not _already_spoken(streamed_text, clean_reply)
                and not stop_event.is_set()
            ):
                show_and_feed(clean_reply)
            speaker.finish()
            speaker_finished = True
            visible_reply = clean_reply or streamed_text
            found = sources.take() if visible_reply else []
            phone_remote.log("alyssa", visible_reply, sources=found)
            if visible_reply:
                print(
                    startup_logging.format_content_log(
                        "assistant response received",
                        visible_reply,
                        full_text=f"{config.ASSISTANT_NAME}: {visible_reply}",
                    )
                )
                if bridge is not None:
                    bridge.speak_signal.emit(visible_reply)
                    if found:
                        bridge.sources_signal.emit(found)
            return reply, interrupt_result["audio"]
        finally:
            if steps:
                bridge.steps_signal.emit([])  # the steps only show while she works
            if not speaker_finished:
                stop_event.set()
                speaker.finish()
            response_done_event.set()
            _join_listener(listener_thread, stop_event)


def run_watcher_loop(bridge=None):
    """Background thread parallel to run_assistant_loop. Periodically runs
    each plugin's check_watch() and speaks non-empty results unprompted.

    Plugins own de-duplication; this loop just calls on each interval.
    Reloads are picked up each pass (see plugin_loader.get_watchers)."""
    next_due = {}
    watcher_signature = None

    while True:
        # Reloads replace the watcher list; re-read each pass so enable/disable is immediate.
        watchers = list(plugin_loader.get_watchers())
        signature = tuple((w["name"], id(w["func"])) for w in watchers)
        if signature != watcher_signature:
            watcher_signature = signature
            if watchers:
                print(f"[watcher] Background monitoring active: {', '.join(w['name'] for w in watchers)}")
        active_keys = {id(w) for w in watchers}
        next_due = {key: due for key, due in next_due.items() if key in active_keys}

        now = time.time()
        soonest = 5.0
        for w in watchers:
            key = id(w)
            due = next_due.get(key, 0.0)
            if now < due:
                soonest = min(soonest, due - now)
                continue
            next_due[key] = now + w["interval"]
            try:
                alert = w["func"]()
            except Exception as e:
                print(f"[watcher] '{w['name']}' check failed: {e}")
                alert = None
            if alert:
                print(f"[watcher] {w['name']}: {alert}")
                if getattr(config, "QUIET_MODE", False):
                    continue
                announce = brain.dialogue._natural_announce_reply(
                    "proactive_alert", {"message": alert}
                )
                if announce:
                    speak(announce, bridge)
        time.sleep(max(0.5, min(soonest, 5.0)))


# Randomized so name-only mentions don't repeat the same prompt.
_PAUSE_PROMPTS = [
    "What do you want me to do?",
    "Yes? What can I help with?",
    "I'm listening - what do you need?",
    "Go ahead, what would you like me to do?",
]


def _pause_prompt() -> str:
    return random.choice(_PAUSE_PROMPTS)


# Until this timestamp, a reply is allowed without saying the name again.
# Armed when her last reply ended in a question. 0 = inactive.
_grace_until = 0.0


def _reply_asks_question(reply: str) -> bool:
    return bool(reply) and reply.rstrip().endswith("?")


def _arm_or_clear_grace_period(reply: str):
    """Arm/restart the name-optional grace period if `reply` ends in `?`; else clear it."""
    global _grace_until
    if _reply_asks_question(reply):
        seconds = getattr(config, "FOLLOWUP_GRACE_SECONDS", 20)
        _grace_until = time.time() + max(0, seconds)
    else:
        _grace_until = 0.0


def _grace_period_active() -> bool:
    return time.time() < _grace_until


# Saying "Alyssa" takes about half a second; less speech than this that comes
# back as just her name is Whisper guessing from its prompt.
_MIN_NAME_ONLY_SPEECH_SECONDS = 0.3


def _extract_command(text: str):
    """Command text if she was addressed by name ("hey Alyssa, ..." or
    "..., Alyssa"), else None. A passing mention mid-sentence doesn't count."""
    span = nameutil.find_address_span(text)
    if span is None:
        return None
    return nameutil.strip_name_at_span(text, span)


def _strip_name_mention(text: str) -> str:
    """Like _extract_command but never None — for typed input that skips
    name-gating but still strips a mentioned name so downstream sees the same text as voice."""
    span = nameutil.find_name_span(text)
    if span is None:
        return text
    return nameutil.strip_name_at_span(text, span)


def run_preflight_checks(bridge=None, notify: bool = True) -> bool:
    """Verify the configured LLM provider is reachable before the listen loop.
    Console (bridge is None): print and exit on failure. GUI: report via bridge
    so Settings stays reachable."""

    if notify:
        print("Running startup preflight checks...")

    def _fail(message: str) -> bool:
        if notify:
            print(message)
            if bridge is not None:
                bridge.error_signal.emit(message)
                return False
        if bridge is None:
            sys.exit(1)
        return False

    def _require_key(key_value, provider_name, env_var, bridge_obj):
        if key_value:
            return True
        return _fail(
            f"NOTE: {env_var} isn't set yet.\n"
            f"Set it as an environment variable, e.g. in PowerShell:\n"
            f'    setx {env_var} "your-key-here"\n'
            "(close and reopen your terminal after running that), or paste it "
            "directly into config.py - see the comments there, or right-click "
            "the companion and use the Settings window."
        )

    if config.LLM_PROVIDER == "gemini":
        if not config.GEMINI_API_KEY:
            if notify:
                print(
                    "NOTE: GEMINI_API_KEY isn't set yet.\n"
                    "Set it as an environment variable, e.g. in PowerShell:\n"
                    '    setx GEMINI_API_KEY "your-key-here"\n'
                    "(close and reopen your terminal after running that), or paste it "
                    "directly into config.py - see the comments there, or right-click "
                    "the companion and use the Settings window."
                )
                if bridge is not None:
                    bridge.gemini_key_needed.emit()
                    return False
            if bridge is None:
                sys.exit(1)
            return False
        if notify:
            print("Startup preflight checks passed.")
        return True

    if config.LLM_PROVIDER == "openai":
        result = _require_key(config.OPENAI_API_KEY, "OpenAI", "OPENAI_API_KEY", bridge)
        if result and notify:
            print("Startup preflight checks passed.")
        return result

    if config.LLM_PROVIDER == "anthropic":
        result = _require_key(config.ANTHROPIC_API_KEY, "Anthropic", "ANTHROPIC_API_KEY", bridge)
        if result and notify:
            print("Startup preflight checks passed.")
        return result

    if config.LLM_PROVIDER == "custom_openai":
        if not getattr(config, "CUSTOM_BASE_URL", ""):
            return _fail(
                "NOTE: CUSTOM_BASE_URL isn't set yet.\n"
                "Point it at an OpenAI-compatible endpoint in config.py (e.g. "
                "Groq, OpenRouter, Together, or a local LM Studio/vLLM server), "
                "or right-click the companion and use the Settings window."
            )
        if notify:
            print("Startup preflight checks passed.")
        return True

    try:
        requests.get("http://localhost:11434", timeout=3)
    except requests.exceptions.RequestException as e:
        return _fail(
            "ERROR: Couldn't reach Ollama.\n"
            "Install it from https://ollama.com, then run once in a terminal:\n"
            f"    ollama pull {config.OLLAMA_MODEL}\n"
            "Ollama runs in the background automatically after install - "
            "if this still fails, open the Ollama app manually and try again. "
            f"({e.__class__.__name__})\n\n"
            "Prefer not to run a local model? Right-click the companion, "
            "open Settings, and switch the LLM provider to Gemini instead."
        )

    # Check the model is pulled now rather than 404 mid-loop.
    try:
        tags = requests.get("http://localhost:11434/api/tags", timeout=5).json()
        pulled = {m["name"] for m in tags.get("models", [])}
        wanted = config.OLLAMA_MODEL
        if wanted not in pulled and not any(p.split(":")[0] == wanted.split(":")[0] for p in pulled):
            return _fail(
                f"ERROR: Ollama is running, but the model '{wanted}' isn't "
                "pulled yet.\nRun this once in a terminal:\n"
                f"    ollama pull {wanted}\n"
                f"Or edit OLLAMA_MODEL in config.py to a model you already have: "
                f"{', '.join(sorted(pulled)) or '(none pulled yet)'}"
            )
    except requests.exceptions.RequestException:
        pass  # non-critical check

    # Warn (don't block) if the model doesn't declare tool-calling support.
    try:
        show = requests.post(
            "http://localhost:11434/api/show",
            json={"model": config.OLLAMA_MODEL},
            timeout=5,
        ).json()
        capabilities = show.get("capabilities", [])
        if capabilities and "tools" not in capabilities:
            print(
                f"WARNING: '{config.OLLAMA_MODEL}' doesn't list 'tools' as a "
                "supported capability. It may not reliably run commands "
                "(open apps, type text, etc.) - only chat. If commands keep "
                "silently failing, switch OLLAMA_MODEL in config.py to a "
                "model with confirmed tool support, e.g. qwen2.5:3b or "
                "llama3.2:3b."
            )
    except requests.exceptions.RequestException:
        pass  # non-critical check

    print("Startup preflight checks passed.")
    return True


_gemini_calls_this_session = 0


# If she ever stops responding again, logs/hang-stacks.txt shows where every thread was stuck.
_HANG_DUMP_SECONDS = 300


def _open_hang_dump():
    if _startup_log_path is None:
        return None
    try:
        return open(os.path.join(os.path.dirname(_startup_log_path), "hang-stacks.txt"), "a")
    except OSError:
        return None


def run_assistant_loop(bridge=None):
    """Always-listening loop. Main thread in console mode, background thread with GUI.
    Calls speak(reply, bridge) either way."""
    # Warm Whisper/LLM/TTS before the first command. Before preflight on purpose:
    # Whisper doesn't need a provider/key, so it can load while Settings is open.
    # Calls are idempotent.
    def _preload_whisper():
        error = transcribe.preload()
        if error and bridge is not None:
            bridge.error_signal.emit(error)

    threading.Thread(target=_preload_whisper, daemon=True).start()
    threading.Thread(target=brain.warm_up_connections, daemon=True).start()
    threading.Thread(target=voice.warm_up, daemon=True).start()

    speculative_engine = brain.get_speculative_engine()
    transcribe.register_partial_transcript_hook(speculative_engine.on_partial_transcript)

    text_queue = getattr(bridge, "text_queue", None) if bridge is not None else None

    # In GUI mode, if preflight checks fail (e.g. fresh install without API key),
    # keep the assistant thread alive in standby instead of dying silently.
    # Re-notify when the provider changes (e.g. the setup wizard picked Ollama
    # but its model isn't pulled yet), not every 2s while nothing changed.
    notified_provider = None
    while not run_preflight_checks(bridge, notify=notified_provider != config.LLM_PROVIDER):
        notified_provider = config.LLM_PROVIDER
        if bridge is None:
            return  # In console mode, preflight checks already exited or reported
        try:
            typed_text = text_queue.get(timeout=2.0) if text_queue is not None else None
        except queue.Empty:
            typed_text = None

        if typed_text:
            text = typed_text.strip()
            if text:
                msg = (
                    "I need an API key to think! Please right-click me, open Settings, "
                    "and enter your Gemini (or OpenAI / Anthropic) API key."
                )
                print(f"[preflight] User typed {text!r} but LLM is not configured yet.")
                speak(msg, bridge)

    if getattr(config, "ENABLE_BACKGROUND_WATCHER", True):
        threading.Thread(target=run_watcher_loop, args=(bridge,), daemon=True).start()
    remote_error = phone_remote.start(bridge)
    if remote_error:
        print(f"[phone remote] {remote_error}")
    import discord_bot

    discord_error = discord_bot.start()
    if discord_error:
        print(f"[discord] {discord_error}")

    wake_mode = str(getattr(config, "WAKE_WORD_MODE", "wake_word") or "wake_word").lower()
    print(f"{config.ASSISTANT_NAME} is running (listening mode: {wake_mode}). Press Ctrl+C to quit.")
    creator = getattr(config, "CREATOR_NAME", "")
    if creator:
        print(f"Made by {creator}.")

    # Barge-in audio from interrupting the previous reply; consumed below so
    # you don't repeat yourself. None on ordinary passes.
    # Greeting is outside the loop's try/except — catch here so a TTS/device
    # hiccup skips the greeting instead of killing the listen thread.
    try:
        pending_interrupt_audio = speak(
            f"{config.ASSISTANT_NAME} ready. At your service.", bridge
        )
    except Exception as e:
        print(f"(startup greeting failed, continuing without it: {e})")
        pending_interrupt_audio = None

    hang_dump = _open_hang_dump()
    while True:
        if hang_dump is not None:
            # Each pass re-arms it: only a loop stuck for minutes writes every thread's stack.
            faulthandler.dump_traceback_later(_HANG_DUMP_SECONDS, file=hang_dump)
        try:
            typed_text = None
            audio = None  # reset each pass; stays None for typed input
            if text_queue is not None:
                try:
                    typed_text = text_queue.get_nowait()
                except queue.Empty:
                    pass

            was_interruption = False
            if typed_text is None and pending_interrupt_audio is not None:
                audio = pending_interrupt_audio
                pending_interrupt_audio = None
                was_interruption = True
                print("(Picking up what you said while interrupting...)")
            elif typed_text is None:
                bypass = (
                    brain.has_pending_power_confirmation()
                    or _grace_period_active()
                    or was_interruption
                )
                audio = recorder.record_command(text_queue=text_queue, bypass_wake_word=bypass)

                if audio is None:
                    # No speech this pass, or a typed message interrupted listen.
                    if text_queue is not None:
                        try:
                            typed_text = text_queue.get_nowait()
                        except queue.Empty:
                            pass
                    if typed_text is None:
                        continue

            if typed_text is not None:
                text = typed_text.strip()
                if not text:
                    continue
                print(
                    startup_logging.format_content_log(
                        "typed input received",
                        text,
                        full_text=f"(Typed: {text!r})",
                    )
                )
            else:
                text = transcribe.transcribe(audio)
                if getattr(config, "DEBUG_PRINT_TRANSCRIPTS", True):
                    print(
                        startup_logging.format_content_log(
                            "speech transcript received",
                            text,
                            full_text=f"(Whisper heard: {text!r})",
                        )
                    )
                # Feed word count so the recorder can adapt pause-before-done (ADAPTIVE_SILENCE_*).
                recorder.update_speaking_rate(len(text.split()))
                if not text:
                    continue

            # Pending confirmation first so "yes"/"go ahead" works without the name.
            if brain.has_pending_power_confirmation():
                command = text
            else:
                wake_mode = str(getattr(config, "WAKE_WORD_MODE", "wake_word") or "wake_word").lower()
                if typed_text is not None:
                    # Typed chat is addressed to her — skip name-gating, still strip name.
                    command = _strip_name_mention(text)
                elif was_interruption:
                    # Cutting her off is equally unambiguous.
                    command = _strip_name_mention(text)
                elif _grace_period_active():
                    # She just asked a question — no name required.
                    command = _strip_name_mention(text)
                elif wake_mode == "always_listening":
                    # Always listening mode ("No wake word"): accept command directly!
                    command = _strip_name_mention(text)
                elif wake_mode == "wake_word":
                    if recorder.was_wake_word_triggered():
                        # Acoustic / neural wake word detector triggered and chimed
                        command = _strip_name_mention(text)
                    else:
                        # Acoustic trigger wasn't caught in real-time, check if Whisper transcribed the name
                        command = _extract_command(text)
                        if command is None:
                            continue  # didn't address Alyssa - ignore
                        if not command.strip() and recorder.last_speech_seconds() < _MIN_NAME_ONLY_SPEECH_SECONDS:
                            # A cough or click that Whisper "heard" as her name.
                            continue
                else:
                    command = _strip_name_mention(text)

                if not command.strip():
                    # Name only — ask what they want.
                    source = "typed" if typed_text is not None else "spoken"
                    print(
                        startup_logging.format_content_log(
                            f"{source} command received",
                            text,
                            full_text=f"{'You typed' if typed_text is not None else 'You said'}: {text}",
                        )
                    )
                    reply = _pause_prompt()
                    speak(reply, bridge)
                    _arm_or_clear_grace_period(reply)
                    continue

            source = "typed" if typed_text is not None else "spoken"
            print(
                startup_logging.format_content_log(
                    f"{source} command received",
                    text,
                    full_text=f"{'You typed' if typed_text is not None else 'You said'}: {text}",
                )
            )
            if config.LLM_PROVIDER == "gemini":
                # Free-quota visibility; also spots false name-triggers (TV/noise).
                global _gemini_calls_this_session
                _gemini_calls_this_session += 1
                print(f"[Gemini request #{_gemini_calls_this_session} this session]")

            # "Thinking..." bubble so the companion doesn't look frozen.
            if bridge is not None:
                bridge.thinking_signal.emit()

            speculative_engine.speculate(command)
            reply, final_interrupt_audio = _stream_command_reply(
                command, bridge, text_queue=text_queue
            )
            speculative_engine.clear(force=True)
            fondness.record_turn(command)
            if bridge is not None:
                bridge.fondness_signal.emit()
            if actions.consume_restart_request():
                actions.relaunch_alyssa()
            pending_interrupt_audio = final_interrupt_audio
            if final_interrupt_audio is None:
                _arm_or_clear_grace_period(reply)

        except KeyboardInterrupt:
            print("\nShutting down.")
            break
        except Exception:
            # Full traceback so recurring crashes are diagnosable.
            import traceback
            traceback.print_exc()
            print("Recovering and going back to listening...")
            time.sleep(1)  # brief backoff so a persistent failure doesn't spin


def main():
    print("Entering Alyssa application startup.")
    gui_enabled = getattr(config, "ENABLE_COMPANION_GUI", True)

    if gui_enabled:
        try:
            import overlay
        except ImportError as e:
            print(
                f"NOTE: Couldn't load the desktop companion GUI ({e}).\n"
                "Falling back to console-only mode. To use the GUI, install "
                "its one extra dependency:\n    pip install PySide6\n"
                "(or set ENABLE_COMPANION_GUI = False in config.py to stop "
                "seeing this message).\n"
            )
        else:
            overlay.run_with_assistant(run_assistant_loop)
            return

    run_assistant_loop(None)


if __name__ == "__main__":
    main()
