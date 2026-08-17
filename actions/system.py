import codecs
import ctypes
import datetime
import os
import subprocess
import requests

import config
import memory
import plugin_loader

from . import bridges, confirmation

def get_datetime() -> str:
    """Returns the current local date and time."""
    now = datetime.datetime.now()
    return now.strftime("It's %I:%M %p on %A, %B %d, %Y.")


def system_power_action(action: str, confirmed: bool = False) -> str:
    """Locks, sleeps, signs out, restarts, or shuts down the PC."""
    action = action.strip().lower()
    if action not in ("lock", "sleep", "signout", "restart", "shutdown"):
        return f"'{action}' isn't a power action I recognize - use lock, sleep, signout, restart, or shutdown."

    # Every action that changes the current session is approved by voice,
    # never by blocking on input() in the launch terminal.
    if action != "lock" and not confirmed:
        if confirmation._power_confirmation_callback is None:
            return "Confirmation required: please ask the user to confirm this power action."
        decision = confirmation._power_confirmation_callback(
            "system_power_action", f"{action} the computer", {"action": action}
        )
        if decision is None:
            return "VOICE_CONFIRMATION_REQUIRED"
        if not decision:
            return "Cancelled by user."

    try:
        if action == "lock":
            import ctypes
            if not ctypes.windll.user32.LockWorkStation():
                return "Couldn't lock the PC."
            return "Locked the PC."
        if action == "sleep":
            subprocess.run(
                ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
                timeout=5,
                check=True,
            )
            return "Putting the PC to sleep."
        if action == "signout":
            subprocess.run(["shutdown", "/l"], timeout=5, check=True)
            return "Signing out."
        if action == "restart":
            subprocess.run(["shutdown", "/r", "/t", "0"], timeout=5, check=True)
            return "Restarting the PC."
        if action == "shutdown":
            subprocess.run(["shutdown", "/s", "/t", "0"], timeout=5, check=True)
            return "Shutting down the PC."
    except Exception as e:
        return f"Couldn't {action} the PC: {e}"


def reset_conversation() -> str:
    """Clears short-term conversation memory (what's been discussed so far
    this session) - use when the user asks to start fresh, change the
    subject, or forget the current conversation. Does NOT touch anything
    saved with remember_fact - that permanent memory is untouched."""
    if not bridges.clear_conversation():
        return "I couldn't clear the conversation because the brain service is not initialized."
    return "Starting fresh - I've cleared what we were just discussing."


def remember_fact(fact: str) -> str:
    """Saves a fact to persistent memory so Alyssa remembers it across restarts."""
    return memory.remember(fact)


def forget_fact(fact_snippet: str) -> str:
    """Removes a previously remembered fact that matches the given snippet."""
    return memory.forget(fact_snippet)


_MAX_CONTENT_FILE_BYTES = 2_000_000


_MAX_CONTENT_SEARCH_BYTES = 25_000_000


def _file_contains_text(path: str, query: str, max_bytes: int) -> tuple[bool, int]:
    """Search UTF-8 text without ever reading beyond max_bytes."""
    decoder = codecs.getincrementaldecoder("utf-8")("ignore")
    overlap = ""
    bytes_read = 0
    with open(path, "rb") as f:
        while bytes_read < max_bytes:
            chunk = f.read(min(64 * 1024, max_bytes - bytes_read))
            if not chunk:
                break
            bytes_read += len(chunk)
            text = (overlap + decoder.decode(chunk)).casefold()
            if query in text:
                return True, bytes_read
            overlap_length = max(0, len(query) - 1)
            overlap = text[-overlap_length:] if overlap_length else ""
        if query in (overlap + decoder.decode(b"", final=True)).casefold():
            return True, bytes_read
    return False, bytes_read


def search_files(query: str, location: str = "", search_contents: bool = False) -> str:
    """Searches for files by name (substring match) under a folder, defaulting
    to the user's home folder if no location is given. Skips noisy/system
    folders. Capped at 25 results and a few thousand files scanned so it
    can't hang searching an entire drive.

    If search_contents is True, also searches inside small text files
    (under 2MB) for the query text, not just filenames - slower, so only
    use it when the user specifically wants to search file contents/text,
    not just find a file by name."""
    query = (query or "").strip()
    if not query:
        return "Tell me part of the file name or text to search for."

    root = os.path.expanduser(location) if location else os.path.expanduser("~")
    if not os.path.isdir(root):
        return f"'{root}' isn't a folder I can search."

    query_lower = query.lower()
    skip_dirs = {
        ".git", "node_modules", "__pycache__", "$recycle.bin",
        "appdata", ".cache", "venv", ".venv", "site-packages",
    }
    text_extensions = {
        ".txt", ".md", ".csv", ".log", ".json", ".py", ".js", ".ts",
        ".html", ".css", ".yml", ".yaml", ".ini", ".cfg",
    }

    matches = []
    scanned = 0
    content_bytes_scanned = 0
    content_limit_reached = False
    max_scanned = 5000
    max_results = 25

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d.lower() not in skip_dirs]

        for filename in filenames:
            scanned += 1
            if scanned > max_scanned or len(matches) >= max_results:
                break

            full_path = os.path.join(dirpath, filename)

            if query_lower in filename.lower():
                matches.append(full_path)
                continue

            if search_contents and os.path.splitext(filename)[1].lower() in text_extensions:
                try:
                    size = os.path.getsize(full_path)
                    if size <= _MAX_CONTENT_FILE_BYTES:
                        remaining = _MAX_CONTENT_SEARCH_BYTES - content_bytes_scanned
                        if remaining <= 0:
                            content_limit_reached = True
                            search_contents = False
                            continue
                        found, bytes_read = _file_contains_text(
                            full_path,
                            query.casefold(),
                            min(_MAX_CONTENT_FILE_BYTES, remaining),
                        )
                        content_bytes_scanned += bytes_read
                        if found:
                            matches.append(full_path)
                        if content_bytes_scanned >= _MAX_CONTENT_SEARCH_BYTES:
                            content_limit_reached = True
                            search_contents = False
                except OSError:
                    pass

        if scanned > max_scanned or len(matches) >= max_results:
            break

    if not matches:
        result = f"No files matching '{query}' found under {root}."
        if content_limit_reached:
            result += " Content search stopped after 25 MB; narrow the folder to search more."
        return result

    result = f"Found {len(matches)} match(es) for '{query}' under {root}:\n"
    # Relative to the search root rather than the full absolute path - still
    # enough to tell same-named files in different subfolders apart, but a
    # lot less to read out loud than the whole machine path each time.
    result += "\n".join(os.path.relpath(m, root) for m in matches)
    if len(matches) >= max_results:
        result += "\n(stopped at 25 results - narrow your search for more precise results.)"
    elif content_limit_reached:
        result += "\n(content search stopped after 25 MB - narrow the folder to search more.)"
    return result


def run_diagnostics() -> str:
    """Runs a self-check across every subsystem Alyssa depends on - the
    configured LLM connection, speech recognition (Whisper), the
    microphone, text-to-speech (Edge TTS), persistent memory, and
    plugins - and reports which are healthy and which (if any) have a
    problem. Use this whenever the user asks Alyssa to run diagnostics
    or a self-test on herself, check her own systems, or asks something
    like 'is everything working?', 'are you okay?', or 'run a health
    check'."""
    # Imported here (not at module load time) to avoid a circular import
    # (brain.py itself imports this module) - same reasoning as
    # describe_screen()/reset_conversation() above.
    import transcribe
    import voice

    checks = []  # (label, ok, detail) - detail is a short plain-English status

    def _append_check(label: str, ok: bool, detail: str):
        checks.append((label, ok, str(detail).strip() or "no details"))

    # --- LLM connection ---
    provider = getattr(config, "LLM_PROVIDER", "ollama")
    if provider == "ollama":
        try:
            requests.get("http://localhost:11434", timeout=3)
        except requests.exceptions.RequestException as e:
            _append_check(
                "LLM connection (Ollama)", False,
                f"can't reach Ollama at localhost:11434 ({e.__class__.__name__}) - is it running?",
            )
        else:
            wanted = config.OLLAMA_MODEL
            try:
                response = requests.get("http://localhost:11434/api/tags", timeout=5)
                response.raise_for_status()
                tags = response.json()
                pulled = {m.get("name") for m in tags.get("models", []) if isinstance(m, dict) and m.get("name")}
                have_it = wanted in pulled or any(
                    p.split(":")[0] == wanted.split(":")[0] for p in pulled
                )
            except requests.exceptions.RequestException as e:
                have_it = None
                _append_check(
                    "LLM connection (Ollama)", True,
                    f"running, but the model list could not be verified ({e.__class__.__name__})",
                )
            except (ValueError, TypeError):
                have_it = None
                _append_check(
                    "LLM connection (Ollama)", True,
                    "running, but the model list response was not readable",
                )
            else:
                if have_it:
                    _append_check("LLM connection (Ollama)", True, f"running, model '{wanted}' is pulled and ready")
                else:
                    _append_check("LLM connection (Ollama)", False, f"running, but model '{wanted}' isn't pulled yet")
    else:
        key_attr, label = {
            "gemini": ("GEMINI_API_KEY", "Gemini"),
            "openai": ("OPENAI_API_KEY", "OpenAI"),
            "anthropic": ("ANTHROPIC_API_KEY", "Claude"),
            "custom_openai": ("CUSTOM_API_KEY", "the custom provider"),
        }.get(provider, (None, provider))
        if provider == "custom_openai":
            base_url = getattr(config, "CUSTOM_BASE_URL", "")
            if base_url:
                _append_check(f"LLM connection ({label})", True, f"configured to use {base_url}")
            else:
                _append_check(f"LLM connection ({label})", False, "CUSTOM_BASE_URL isn't set in config.py")
        elif key_attr and getattr(config, key_attr, ""):
            _append_check(f"LLM connection ({label})", True, "API key is configured")
        else:
            _append_check(f"LLM connection ({label})", False, f"{key_attr} isn't set in config.py")

    # --- Speech recognition (Whisper) ---
    # get_engine_status() reports what's REALLY running (GPU vs CPU,
    # precision) rather than just what config.py requests, since "auto"
    # settings resolve differently per machine and a GPU load can silently
    # fall back to CPU - see transcribe.py.
    try:
        _append_check("Speech recognition", True, transcribe.get_engine_status())
    except Exception as e:
        _append_check("Speech recognition", False, f"couldn't determine the Whisper engine state ({e})")

    # --- Microphone ---
    try:
        import sounddevice as sd
        devices = sd.query_devices(kind="input")
        if isinstance(devices, list):
            device_info = next((d for d in devices if d.get("default") or d.get("name") == sd.default.device[0]), None)
            if isinstance(device_info, dict):
                name = device_info.get("name") or "unknown"
                _append_check("Microphone", True, f"default input device detected ({name})")
            else:
                _append_check("Microphone", True, "input devices detected, but no default device summary was available")
        else:
            _append_check("Microphone", True, "input devices detected")
    except Exception as e:
        _append_check("Microphone", False, f"no working input device found ({e})")

    # --- Text-to-speech ---
    if not getattr(config, "SPEAK_RESPONSES", True):
        _append_check("Text-to-speech", True, "turned off in settings - replies are text-only right now")
    else:
        provider = getattr(config, "TTS_PROVIDER", "edge")
        provider_label = "ElevenLabs" if provider == "elevenlabs" else "Edge TTS"
        try:
            test_path = voice._synthesize_to_temp_file("Diagnostics test.")
            ok = os.path.exists(test_path) and os.path.getsize(test_path) > 0
            try:
                os.remove(test_path)
            except OSError:
                pass
            if ok:
                voice_label = getattr(config, "ELEVENLABS_VOICE_ID", "") if provider == "elevenlabs" else config.EDGE_TTS_VOICE
                _append_check("Text-to-speech", True, f"{provider_label} is reachable, voice '{voice_label}' works")
            else:
                _append_check("Text-to-speech", False, f"{provider_label} returned an empty audio file")
        except Exception as e:
            _append_check("Text-to-speech", False, f"couldn't reach {provider_label} ({e})")

    # --- Persistent memory ---
    try:
        current = memory.load_memories()
        memory.save_memories(current)  # round-trip write, confirms the database is actually writable too
        _append_check("Memory", True, f"local memory database is readable and writable ({len(current)} fact(s) saved)")
    except Exception as e:
        _append_check("Memory", False, f"couldn't read/write the memory database ({e})")

    # --- Plugins ---
    if not getattr(config, "PLUGINS_ENABLED", True):
        _append_check("Plugins", True, "disabled in config.py")
    else:
        from . import PLUGIN_FUNCTIONS, _PLUGIN_LOAD_PROBLEMS
        errors = plugin_loader.get_load_errors() + _PLUGIN_LOAD_PROBLEMS
        count = len(PLUGIN_FUNCTIONS)
        if errors:
            _append_check("Plugins", False, f"{count} loaded, but ran into: {'; '.join(errors)}")
        else:
            _append_check("Plugins", True, f"{count} loaded, no errors")

    # --- Background watcher (proactive alerts) ---
    if not getattr(config, "ENABLE_BACKGROUND_WATCHER", True):
        _append_check("Background watcher", True, "disabled in config.py")
    else:
        watchers = plugin_loader.get_watchers()
        if watchers:
            names = ", ".join(w["name"] for w in watchers)
            _append_check("Background watcher", True, f"monitoring: {names}")
        else:
            _append_check("Background watcher", True, "enabled, but no plugin currently registers a proactive check")

    problems = [c for c in checks if not c[1]]

    def _health_hint(label: str, detail: str) -> str:
        lowered = (detail or "").lower()
        if "ollama" in lowered and "running" in lowered:
            return "If this is unexpected, check that Ollama is installed and the model has been pulled."
        if "microphone" in lowered or "input device" in lowered:
            return "If this is unexpected, verify the microphone is connected and selected as the default input device."
        if "speech recognition" in lowered or "whisper" in lowered:
            return "If this is unexpected, check your Whisper installation and GPU/CUDA availability."
        if "text-to-speech" in lowered or "edge tts" in lowered:
            return "If this is unexpected, verify your TTS provider settings and network access."
        if "memory" in lowered:
            return "If this is unexpected, check that the memory.json file is writable and the folder permissions are correct."
        if "plugin" in lowered:
            return "If this is unexpected, review the plugin file for import errors or conflicting tool names."
        return ""

    if not problems:
        summary = (
            "All systems normal - everything Alyssa depends on is working fine. "
            "The assistant should be ready to listen, think, and act."
        )
    else:
        summary = (
            f"Found {len(problems)} issue(s) that may affect Alyssa's behavior. "
            f"The most relevant ones are: "
            + "; ".join(f"{label} - {detail}" for label, _, detail in problems)
        )

    detailed_lines = []
    detailed_lines.append(summary)
    detailed_lines.append("")
    detailed_lines.append("Detailed status:")
    for label, ok, detail in checks:
        prefix = "OK" if ok else "PROBLEM"
        detailed_lines.append(f"- [{prefix}] {label}: {detail}")
        hint = _health_hint(label, detail)
        if hint:
            detailed_lines.append(f"  Hint: {hint}")

    return "\n".join(detailed_lines)
