# Alyssa Assistant

Alyssa is a Windows-first voice assistant that can control your PC, answer
questions, remember useful facts, and load extra abilities from Python plugins.
Speech is transcribed locally with Faster Whisper, while reasoning can use
Gemini, Ollama, OpenAI, Anthropic, or another OpenAI-compatible provider.

## Highlights

- Always-listening voice input gated by the assistant's name
- Local speech-to-text with Faster Whisper
- Configurable local or cloud language model
- Desktop companion with typed chat and interruption support
- App, window, media, clipboard, file, screenshot, and system controls
- Screen description and vision-guided clicking
- Local conversation history, saved memories, reminders, and timers
- Weather, web search, article summaries, system monitoring, and other plugins
- Spoken confirmation before protected actions

## Safety and privacy

Alyssa can type, click, run commands, move files, and control Windows. Review
the code and configuration before giving it access to important data or running
it with elevated privileges.

- Voice commands are ignored unless they mention `Alyssa` (or a configured
  alias). Audio is still transcribed locally to detect that name.
- The default configuration asks for confirmation before actions.
- Commands, deletion, vision-guided clicks, process termination, Recycle Bin
  emptying, and disruptive power actions require a separate approval step.
- Spoken approval recognizes confirmation words such as `yes`, `confirm`, and
  `go ahead`; it does not authenticate the speaker or provide a security
  boundary against another person near the microphone.
- Deleted files are normally moved to the Windows Recycle Bin.
- Speech transcription is local unless `STT_PROVIDER` selects ElevenLabs
  realtime (or `auto` finds an ElevenLabs key). Prompts and screenshots are
  sent to the configured LLM provider when a cloud provider is selected.
- Edge TTS sends reply text to Microsoft's online speech service. Networked
  plugins may also contact their documented services.
- `memory.json`, Google OAuth credentials, and OAuth tokens are ignored by Git.
  Never commit API keys placed in `config.py`.

These safeguards reduce accidental actions; they are not a security boundary.

## Requirements

- Windows 10 or 11
- Python 3.10 or newer from [python.org](https://www.python.org/downloads/)
- [Microsoft C++ Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/)
  with the **Desktop development with C++** workload (required to compile
  native packages when a matching prebuilt wheel is unavailable, including
  `webrtcvad-wheels` on Python 3.14)
- A microphone and audio output
- One supported LLM provider
- Internet access for cloud models, Edge TTS, and networked plugins

For a fully local language model, install [Ollama](https://ollama.com/) and
pull the configured model:

```powershell
ollama pull qwen2.5:3b
```

## Quick start

1. Clone or download this repository.
2. Open `config.py` and choose an `LLM_PROVIDER`, or configure it later in the
   companion's Settings window.
3. If you do not want NVIDIA CUDA packages installed, set
   `WHISPER_DEVICE = "cpu"` before the first launch.
4. Double-click `scripts\start_alyssa.bat`.

The launcher creates `.venv`, installs the required packages, and starts
Alyssa. Later launches reuse the environment unless a requirements file
changes.

To run it manually instead:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python main.py
```

With `WHISPER_DEVICE` set to `auto` or `cuda`, also install
`requirements-gpu.txt`. Those NVIDIA packages require several gigabytes.

## LLM providers

Set `LLM_PROVIDER` in `config.py` to one of the following:

| Value | Provider | Required setup |
| --- | --- | --- |
| `gemini` | Google Gemini (default) | Add a Gemini API key in Settings or `config.py` |
| `ollama` | Local Ollama model | Install Ollama and pull `OLLAMA_MODEL` |
| `openai` | OpenAI | Set `OPENAI_API_KEY` |
| `anthropic` | Anthropic | Set `ANTHROPIC_API_KEY` |
| `custom_openai` | OpenAI-compatible endpoint | Set the base URL, model, and optional `CUSTOM_LLM_API_KEY` |

Example environment-variable setup:

```powershell
setx OPENAI_API_KEY "your-key"
setx ANTHROPIC_API_KEY "your-key"
setx CUSTOM_LLM_API_KEY "your-key"
```

Restart the terminal or Alyssa after using `setx`. Screen vision uses the
selected provider; Ollama users must also pull the configured vision model,
which defaults to `llava`.

## Example commands

```text
Alyssa, open Notepad
Alyssa, type hello world
Alyssa, find my latest PDF
Alyssa, remember that I prefer Spotify
Alyssa, remind me to call the dentist tomorrow at 3 PM
Alyssa, set a timer for ten minutes
Alyssa, what's using the most memory?
Alyssa, summarize https://example.com/article
Alyssa, what am I looking at?
```

You can say "Alyssa, stop" or "stop, Alyssa" over a reply to interrupt it.
Typed messages in the desktop companion do not require the assistant's name.

## Configuration

The most useful settings are in `config.py`:

| Setting | Purpose |
| --- | --- |
| `ASSISTANT_NAME` | Spoken command name |
| `LLM_PROVIDER` | Active model provider |
| `WHISPER_MODEL_SIZE` | Speech recognition model size |
| `WHISPER_DEVICE` | `cpu`, `cuda`, or `auto` |
| `STT_PROVIDER` | `auto`, local Whisper, or ElevenLabs realtime WebSocket STT |
| `SILENCE_SECONDS` | Base pause required to end a spoken turn |
| `TTS_PROVIDER` | `edge` or `elevenlabs` |
| `TTS_AUDIO_BUFFER_MS` | PCM prebuffer used by ElevenLabs streaming playback |
| `CONFIRM_BEFORE_ACTIONS` | Confirm ordinary actions before running them |
| `POWER_CONFIRMATION_TIMEOUT_SECONDS` | How long a protected-action approval remains pending |
| `ALLOW_INTERRUPTIONS` | Permit speech to interrupt replies |
| `BARGE_IN_REQUIRE_NAME` | Require both the assistant's name and `stop` for spoken interruptions |
| `CONVERSATION_MEMORY_TURNS` | Number of recent conversation turns kept in context |
| `PLUGINS_ENABLED` | Load tools from `plugins/` |
| `ENABLE_BACKGROUND_WATCHER` | Run proactive plugin checks |
| `ENABLE_COMPANION_GUI` | Show the desktop companion |

The Settings window exposes the common provider, voice, assistant, and
companion options without requiring manual edits. Its Updates tab installs
the latest published GitHub release while preserving local settings and data.
Application files are replaced with the published versions, including local
edits, so commit or back up code changes before updating.

### Low-latency voice pipeline

The default endpointing window is 300 ms and adapts between 240–420 ms from
the observed speaking rate. Speech starts after 120 ms of sustained voice;
barge-in detection starts after 150 ms. With name-gated interruptions enabled,
playback stops only after the captured phrase contains both the assistant's
name and the whole word `stop`. Conversation context keeps 11 turns and slides
again at 4,000 characters.

With an ElevenLabs key, `STT_PROVIDER = "auto"` uses Scribe realtime over one
persistent WebSocket and emits partial transcripts while recording. Without a
key it falls back to the existing local Faster Whisper path. ElevenLabs TTS
uses the Flash model, clause-level text streaming, raw 24 kHz PCM playback, and
a 100 ms client prebuffer. Edge TTS retains clause pipelining but must finish
each clause's encoded audio before that clause can play.

LLM text streams for Gemini, Ollama, OpenAI-compatible, and Anthropic providers.
Generation, TTS, playback, and interruption listening overlap; new speech
is transcribed and checked before a name-gated spoken interruption cancels the
active response. A typed message still interrupts immediately.

## Included plugins

Plugins are loaded from `plugins/` at startup. The included set provides:

- Calculator and unit conversion
- Read-only Google Calendar and Gmail access
- IP-based location and Open-Meteo weather
- Web search and webpage summarization
- News digests
- Reminders, timers, and stopwatch controls
- Process management and temporary-file cleanup
- System health monitoring
- Optional webcam motion alerts
- Runtime Caveman and Ponytail response modes

Some plugins require network access, extra credentials, or local hardware.
See the module docstring at the top of each plugin for its setup and privacy
details. Google integration uses read-only OAuth scopes and stores its local
token in `token.json`.

### Adding a plugin

Create a `.py` file in `plugins/` that exports `FUNCTIONS` and matching `TOOLS`
schemas. Files beginning with `_` are ignored.

```python
def roll_die(sides: int = 6) -> str:
    import random
    return str(random.randint(1, sides))


FUNCTIONS = {"roll_die": roll_die}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "roll_die",
            "description": "Rolls a die.",
            "parameters": {
                "type": "object",
                "properties": {"sides": {"type": "integer", "minimum": 2}},
            },
        },
    }
]
```

A plugin may also expose `check_watch() -> str | None` and an optional
`WATCH_INTERVAL_SECONDS` value to provide proactive spoken alerts.

## Local data

| File | Contents |
| --- | --- |
| `memory.json` | Saved facts |
| `reminders.json` | Local reminders |
| `token.json` | Google OAuth token, when enabled |
| `overlay_config.json` | Companion appearance and position |

Saved memories are plain JSON and use lightweight keyword matching. Session
conversation history stays in memory and expires after the configured timeout.

## Run the tests

Install the development requirements after setting up the environment, then
run the tests:

```powershell
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

## Start with Windows

Run `scripts\install_startup.bat` to register Alyssa with Windows Task Scheduler. Run
`scripts\uninstall_startup.bat` to remove the task. Inspect the script first and only
enable startup after you are comfortable with Alyssa's permissions.

## Troubleshooting

- **Python is not found:** reinstall from python.org and enable **Add Python to
  PATH** during setup.
- **Dependency setup is stuck or inconsistent:** delete only the local `.venv`
  folder and run `scripts\start_alyssa.bat` again.
- **Ollama cannot be reached:** open Ollama and confirm the configured model is
  present with `ollama list`.
- **No cloud response:** verify the selected provider's key and model in
  Settings.
- **False interruptions:** keep `BARGE_IN_REQUIRE_NAME = True`; spoken
  interruptions then require both "Alyssa" and "stop". A headset microphone
  can further reduce false speech detection.
- **Plugin failed to load:** check the startup console; plugin import errors are
  reported and the remaining plugins continue loading.
