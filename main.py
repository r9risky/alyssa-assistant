"""
Alyssa Assistant - Main Listening Loop and Application Entrypoint.
"""
import queue
import random
import re
import os
import sys
import threading
import time

# PyInstaller exe: look for config.py next to the exe, not bundled inside it,
# so editing config.py takes effect without a rebuild.
if getattr(sys, "frozen", False):
    sys.path.insert(0, os.path.dirname(sys.executable))
    _config_dir = os.path.dirname(sys.executable)
else:
    _config_dir = os.path.dirname(os.path.abspath(__file__))

# Windows startup processes can inherit an arbitrary current directory.
os.chdir(_config_dir)

import startup_logging
_startup_log_path = startup_logging.configure(_config_dir)
print(
    f"Starting Alyssa: executable={sys.executable!r}, app_dir={_config_dir!r}, "
    f"cwd={os.getcwd()!r}, frozen={bool(getattr(sys, 'frozen', False))}"
)

if sys.stdin is None:
    sys.stdin = open(os.devnull, "r")

# One-time upgrade path: if this install still has real API keys sitting
# in config.py as plaintext literals (how the Settings window used to
# save them, before credential_store.py moved secrets into the OS
# keyring), move them into the keyring and blank the literals - before
# config.py is imported below, so the values it reads via
# credential_store.get_secret() are already in place either way. See
# credential_store.py's docstring for what this is protecting against.
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

# Serializes speak() calls between the main listen loop and the background
# watcher thread (see run_watcher_loop below), so a proactive alert ("your
# disk is almost full") can never talk over - or get talked over by - a
# reply to something you actually asked. A watcher alert simply waits its
# turn; nothing is dropped, since each plugin's check_watch() re-evaluates
# on its own next cycle anyway.
_speak_lock = threading.Lock()


def _clean_reply_for_speech(text: str) -> str:
    """Removes punctuation a model occasionally leaves before a sentence."""
    return re.sub(r"^[\s,;:—–-]+", "", (text or "")).strip()


def speak(text: str, bridge=None):
    """Speaks *text*, and returns audio you spoke to interrupt her with, if
    you did - a numpy array ready to hand straight to transcribe.transcribe,
    or None if she finished the reply without being cut off (or
    SPEAK_RESPONSES/ALLOW_INTERRUPTIONS is off).

    A background thread (recorder.listen_for_barge_in) watches the mic while
    she talks. The moment it hears you, it sets stop_speaking_event, which
    voice.speak()'s playback loop reacts to within ~20ms, and keeps
    recording what you say next so it can be treated as your next command."""
    text = _clean_reply_for_speech(text)
    if not text:
        return None

    with _speak_lock:
        print(f"{config.ASSISTANT_NAME}: {text}")
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
            # A broken audio driver can leave stream.read() blocked forever.
            # The listener is a daemon, so never let that freeze all replies.
            listener_thread.join(timeout=1.0)

        return interrupt_result["audio"]


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
        )
        listener_thread = threading.Thread(target=listen, daemon=True)
        listener_thread.start()
        speaker_finished = False

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
            )
            streamed_text = _clean_reply_for_speech("".join(streamed_parts))
            clean_reply = _clean_reply_for_speech(reply)
            if (
                clean_reply
                and not streamed_text.rstrip().endswith(clean_reply)
                and not stop_event.is_set()
            ):
                show_and_feed(clean_reply)
            speaker.finish()
            speaker_finished = True
            visible_reply = clean_reply or streamed_text
            if visible_reply:
                print(f"{config.ASSISTANT_NAME}: {visible_reply}")
                if bridge is not None:
                    bridge.speak_signal.emit(visible_reply)
            return reply, interrupt_result["audio"]
        finally:
            if not speaker_finished:
                stop_event.set()
                speaker.finish()
            response_done_event.set()
            listener_thread.join(timeout=1.0)


def run_watcher_loop(bridge=None):
    """Background thread, parallel to the main listen loop in
    run_assistant_loop below - the actual architectural shift that makes
    Alyssa proactive instead of purely reactive. Periodically runs every
    plugin's check_watch() (system diagnostics, security camera motion,
    calendar reminders, a daily news digest, whatever else you add - see
    plugin_loader.py's module docstring for how a plugin opts in) and
    speaks whatever any of them return, unprompted - nobody has to ask
    first.

    Each plugin tracks its own "have I already mentioned this" state (see
    plugins/system_watch.py for the pattern), so this loop doesn't need to
    know anything about de-duplication - it just calls check_watch() on
    each plugin's own schedule and speaks a non-empty result."""
    next_due = {}
    watcher_signature = None

    while True:
        # Reloads replace plugin_loader's watcher list. Read it each pass so
        # disabled checks stop and newly enabled checks start immediately.
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
                speak(alert, bridge)
        time.sleep(max(0.5, min(soonest, 5.0)))


# Randomized so Alyssa isn't saying the same line every time her name is
# said with no request attached.
_PAUSE_PROMPTS = [
    "What do you want me to do?",
    "Yes? What can I help with?",
    "I'm listening - what do you need?",
    "Go ahead, what would you like me to do?",
]


def _pause_prompt() -> str:
    return random.choice(_PAUSE_PROMPTS)


# Timestamp until which a reply is allowed without saying Alyssa's name
# again, armed whenever her last reply ended in a question. 0 = inactive.
_grace_until = 0.0


def _reply_asks_question(reply: str) -> bool:
    return bool(reply) and reply.rstrip().endswith("?")


def _arm_or_clear_grace_period(reply: str):
    """Starts (or restarts) the name-optional grace period if `reply` ends
    in a question, otherwise clears it - called after every reply Alyssa
    speaks on her own initiative."""
    global _grace_until
    if _reply_asks_question(reply):
        seconds = getattr(config, "FOLLOWUP_GRACE_SECONDS", 20)
        _grace_until = time.time() + max(0, seconds)
    else:
        _grace_until = 0.0


def _grace_period_active() -> bool:
    return time.time() < _grace_until


def _extract_command(text: str):
    """Returns the command text if the assistant's name was mentioned,
    otherwise None - keeps ordinary conversation from being treated as a
    command."""
    span = nameutil.find_name_span(text)
    if span is None:
        return None
    return nameutil.strip_name_at_span(text, span)


def _strip_name_mention(text: str) -> str:
    """Like _extract_command but never returns None - for callers (typed
    chat input) that don't need to gate on the name being said, but still
    want it stripped so downstream logic (e.g. brain.py's small-talk
    detector) sees the same text voice input would produce."""
    span = nameutil.find_name_span(text)
    if span is None:
        return text
    return nameutil.strip_name_at_span(text, span)


def run_preflight_checks(bridge=None) -> bool:
    """Checks the configured LLM provider is reachable before entering the
    listening loop. In console mode (bridge is None) prints and exits on
    failure; in GUI mode reports the problem via bridge instead of killing
    the app, so Settings stays reachable."""

    print("Running startup preflight checks...")

    def _fail(message: str) -> bool:
        print(message)
        if bridge is not None:
            bridge.error_signal.emit(message)
            return False
        sys.exit(1)

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
            sys.exit(1)
        print("Startup preflight checks passed.")
        return True

    if config.LLM_PROVIDER == "openai":
        result = _require_key(config.OPENAI_API_KEY, "OpenAI", "OPENAI_API_KEY", bridge)
        if result:
            print("Startup preflight checks passed.")
        return result

    if config.LLM_PROVIDER == "anthropic":
        result = _require_key(config.ANTHROPIC_API_KEY, "Anthropic", "ANTHROPIC_API_KEY", bridge)
        if result:
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

    # Model might not be pulled yet - check now, rather than a confusing
    # 404 later mid-loop.
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

    # Warn (don't block) if the model doesn't declare tool-calling support -
    # it may respond but silently fail to run actual commands.
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


def run_assistant_loop(bridge=None):
    """The always-listening loop. Runs on the main thread in console mode,
    or on a background thread when the desktop companion GUI is enabled -
    this function doesn't need to know which; it just calls speak(reply, bridge)."""
    # Warm up Whisper and (for Ollama) the LLM now, before the cost hits on
    # your first real command. Done before preflight on purpose: Whisper
    # doesn't need an LLM provider/key, so it can load while you're still in
    # Settings fixing a missing key. Both calls are safe to call more than
    # once, so re-running this after a failed preflight just no-ops here.
    threading.Thread(target=transcribe.preload, daemon=True).start()
    threading.Thread(target=brain.warm_up_connections, daemon=True).start()
    threading.Thread(target=voice.warm_up, daemon=True).start()

    if not run_preflight_checks(bridge):
        return  # message already shown; in GUI mode the window stays open

    if getattr(config, "ENABLE_BACKGROUND_WATCHER", True):
        threading.Thread(target=run_watcher_loop, args=(bridge,), daemon=True).start()

    print(f"{config.ASSISTANT_NAME} is running (always listening, no wake word). Press Ctrl+C to quit.")
    creator = getattr(config, "CREATOR_NAME", "")
    if creator:
        print(f"Made by {creator}.")
    # Goes through speak() so the GUI mode both fills the speech bubble and
    # actually plays the TTS, mouth animation synced via bridge signals.
    text_queue = getattr(bridge, "text_queue", None) if bridge is not None else None

    # Audio captured by recorder.listen_for_barge_in if you interrupted the
    # previous reply (or the initial greeting) - consumed below instead of
    # making you repeat yourself. None on every ordinary pass.
    #
    # This call sits outside the while loop's try/except below, so it used
    # to be a real crash risk: a transient hiccup here (TTS network error,
    # audio device briefly busy/reclaimed, etc.) raised straight out of
    # run_assistant_loop() and killed the whole listening thread before a
    # single command was ever heard - looking exactly like a random crash,
    # often right after waking the PC or right after another app grabbed
    # the mic/speakers. Catching it here means a bad greeting just gets
    # skipped instead of taking the whole assistant down with it.
    try:
        pending_interrupt_audio = speak(
            f"{config.ASSISTANT_NAME} ready. At your service.", bridge
        )
    except Exception as e:
        print(f"(startup greeting failed, continuing without it: {e})")
        pending_interrupt_audio = None

    while True:
        try:
            typed_text = None
            audio = None  # explicit reset each pass - see below; stays None for typed input
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
                audio = recorder.record_command(text_queue=text_queue)

                if audio is None:
                    # Could be "no speech this pass" or "a typed message
                    # interrupted the listen" - check for the latter first.
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
                print(f"(Typed: {text!r})")
            else:
                text = transcribe.transcribe(audio)
                if getattr(config, "DEBUG_PRINT_TRANSCRIPTS", True):
                    print(f"(Whisper heard: {text!r})")
                # Feed word count back so the recorder can adapt the
                # pause-before-done threshold - see ADAPTIVE_SILENCE_* in config.py.
                recorder.update_speaking_rate(len(text.split()))
                if not text:
                    continue

            # A pending confirmation always gets first priority, so "yes"/
            # "go ahead" works without saying Alyssa's name.
            if brain.has_pending_power_confirmation():
                command = text
            else:
                if typed_text is not None:
                    # Typed into her chat box - unambiguously addressed to
                    # her, so skip the name-mention requirement (unlike
                    # voice), but still strip a mentioned name if present.
                    command = _strip_name_mention(text)
                elif was_interruption:
                    # Cutting her off mid-reply is just as unambiguous.
                    command = _strip_name_mention(text)
                elif _grace_period_active():
                    # She just asked a question - no name required, same
                    # handling as above.
                    command = _strip_name_mention(text)
                else:
                    command = _extract_command(text)
                    if command is None:
                        continue  # didn't mention Alyssa - ignore

                if not command.strip():
                    # Name said/typed with nothing following - ask what they want.
                    print(f"{'You typed' if typed_text is not None else 'You said'}: {text}")
                    reply = _pause_prompt()
                    speak(reply, bridge)
                    _arm_or_clear_grace_period(reply)
                    continue

            print(f"{'You typed' if typed_text is not None else 'You said'}: {text}")
            if config.LLM_PROVIDER == "gemini":
                # Visibility into free-quota usage, and for spotting false
                # name-triggers (background noise/TV misheard as "Alyssa").
                global _gemini_calls_this_session
                _gemini_calls_this_session += 1
                print(f"[Gemini request #{_gemini_calls_this_session} this session]")

            # "Thinking..." bubble while the model works, so the companion
            # doesn't look frozen during a multi-second cloud round trip.
            if bridge is not None:
                bridge.thinking_signal.emit()

            reply, final_interrupt_audio = _stream_command_reply(
                command, bridge, text_queue=text_queue
            )
            if actions.consume_restart_request():
                actions.relaunch_alyssa()
            pending_interrupt_audio = final_interrupt_audio
            if final_interrupt_audio is None:
                _arm_or_clear_grace_period(reply)

        except KeyboardInterrupt:
            print("\nShutting down.")
            break
        except Exception:
            # Full traceback (not just str(e)) so a recurring "random
            # crash" report is actually diagnosable from the console log
            # or telemetry, instead of a bare one-line message that
            # doesn't say where in the turn it happened.
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
