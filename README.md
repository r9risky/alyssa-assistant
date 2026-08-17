# Alyssa AI Assistant

Alyssa is a **Windows-first desktop and voice assistant** built in Python. It combines local speech recognition, configurable LLM providers, desktop automation, memory, a companion GUI, and a plugin system for extending tools without putting every capability into the core runtime.

> **Important:** Alyssa can type, click, launch applications, manipulate files, run system actions, and send prompts to configured AI providers. Review the configuration and permissions before using it on a machine that contains important or sensitive data.

## Contents

- [Features](#features)
- [Architecture](#architecture)
- [Project structure](#project-structure)
- [Requirements](#requirements)
- [Quick start](#quick-start)
- [LLM providers](#llm-providers)
- [Secure LLM credentials](#secure-llm-credentials)
- [Voice pipeline](#voice-pipeline)
- [Configuration](#configuration)
- [Example commands](#example-commands)
- [Plugins](#plugins)
- [Local data and privacy](#local-data-and-privacy)
- [Testing](#testing)
- [Start with Windows](#start-with-windows)
- [Troubleshooting](#troubleshooting)

---

## Features

- Name-gated voice commands using the configured assistant name
- Local speech-to-text with Faster Whisper
- Optional ElevenLabs realtime speech-to-text
- Streaming LLM responses
- Gemini, OpenAI, Anthropic, Ollama, and OpenAI-compatible providers
- Desktop companion GUI with typed chat
- Speech interruption / barge-in support
- Application, window, keyboard, mouse, clipboard, screenshot, media, file, and system tools
- Screen description and vision-guided actions
- Local saved memories and recent conversation context
- Reminders, timers, weather, web search, webpage summaries, system monitoring, and other plugins
- Confirmation gates for protected or disruptive actions
- Per-user secure storage for LLM API credentials on Windows

---

## Architecture

The project is organized so the reasoning layer, automation layer, UI, and provider integrations remain separate.

```text
                         +----------------------+
                         |       main.py        |
                         | runtime orchestration|
                         +----------+-----------+
                                    |
                 +------------------+------------------+
                 |                                     |
                 v                                     v
        +------------------+                  +------------------+
        |     overlay/     |                  |      brain/      |
        | companion + UI   |                  | dialogue + LLM   |
        +------------------+                  +--------+---------+
                                                        |
                                      +-----------------+-----------------+
                                      |                                   |
                                      v                                   v
                            +-------------------+                +------------------+
                            | brain/providers/  |                |     actions/     |
                            | LLM adapters      |                | OS / desktop I/O |
                            +-------------------+                +--------+---------+
                                                                           |
                                                                           v
                                                               +--------------------+
                                                               | Windows / desktop  |
                                                               | audio / filesystem |
                                                               +--------------------+

brain/tool_catalog.py  -> static LLM tool schemas
brain/tool_registry.py -> live built-in + plugin tool registry
plugin_loader.py       -> discovers tools from plugins/
actions/bridges.py     -> injected callbacks for the small set of brain-owned
                          capabilities needed by actions
```

### Dependency rules

The architecture deliberately avoids importing the reasoning layer back into the action layer.

- `brain/` owns conversation orchestration, provider selection, tool calling, and vision.
- `actions/` owns desktop and operating-system operations.
- `actions/desktop.py` delays desktop-library initialization until automation is actually needed.
- `actions/bridges.py` uses injected callbacks instead of direct `actions -> brain` imports.
- `brain/tool_catalog.py` contains static tool schemas.
- `brain/tool_registry.py` builds the live tool view from built-in actions and plugins.
- Provider modules do not import the dialogue orchestrator.
- `overlay/` owns companion rendering, settings, themes, widgets, and GUI behavior.

These boundaries reduce circular dependencies and make the reasoning stack easier to test without requiring a live graphical desktop.

---

## Project structure

```text
AlyssaAi/
|
|-- main.py                    # Application entry point and runtime orchestration
|-- config.py                  # Non-secret application/provider configuration
|-- credential_store.py        # Secure LLM credential storage and lookup
|-- memory.py                  # Saved-memory logic
|-- recorder.py                # Microphone recording / endpointing
|-- transcribe.py              # Speech-to-text integration
|-- voice.py                   # Text-to-speech and playback
|-- telemetry.py               # Runtime telemetry helpers
|-- updater.py                 # Application update support
|-- plugin_loader.py           # Plugin discovery and registration
|
|-- brain/
|   |-- dialogue.py            # Conversation orchestration and tool execution
|   |-- common.py              # Shared reasoning helpers
|   |-- vision.py              # Screen/image reasoning
|   |-- text_utils.py          # Text utilities
|   |-- tool_catalog.py        # Static tool definitions
|   |-- tool_registry.py       # Runtime tool registry
|   `-- providers/
|       |-- gemini.py
|       |-- openai.py
|       |-- anthropic.py
|       `-- ollama.py
|
|-- actions/
|   |-- apps_and_files.py
|   |-- clipboard_and_screen.py
|   |-- confirmation.py
|   |-- desktop.py
|   |-- input_sim.py
|   |-- media.py
|   |-- music.py
|   |-- system.py
|   |-- windows.py
|   `-- bridges.py
|
|-- overlay/
|   |-- app_shell.py
|   |-- credential_checks.py
|   |-- rendering.py
|   |-- settings_dialog.py
|   |-- theming.py
|   `-- widgets.py
|
|-- plugins/                   # Optional/runtime-discovered capabilities
|-- scripts/                   # Windows launcher and startup-task scripts
|-- tests/                     # Unit, safety, architecture, and runtime tests
|-- assets/                    # Application assets
|
|-- requirements.txt
|-- requirements-gpu.txt
|-- requirements-dev.txt
`-- README.md
```

---

## Requirements

Alyssa is designed primarily for Windows desktop use.

### Required

- Windows 10 or Windows 11
- Python 3.10 or newer
- A microphone for voice commands
- Audio output for spoken responses
- At least one configured LLM provider

### May be required during installation

Some Python packages contain native components. If pip cannot find a compatible prebuilt wheel for your Python version, install **Microsoft C++ Build Tools** with the **Desktop development with C++** workload.

### Internet access

Internet access is required when using cloud LLM providers, Edge TTS, ElevenLabs, network-based plugins, or application updates. Ollama can provide local LLM inference once its models are installed.

---

## Quick start

### 1. Download or clone the project

Place the project in a normal writable folder on Windows.

### 2. Choose an LLM provider

Edit `config.py` and set:

```python
LLM_PROVIDER = "gemini"
```

Supported values are:

```text
gemini
openai
anthropic
ollama
custom_openai
```

You can also change the provider later from Alyssa's Settings window.

### 3. Choose CPU or GPU speech recognition

For the simplest installation, use:

```python
WHISPER_DEVICE = "cpu"
```

If `WHISPER_DEVICE` is `auto` or `cuda`, the launcher also installs packages from `requirements-gpu.txt`.

### 4. Launch Alyssa

Double-click:

```text
scripts\start_alyssa.bat
```

The launcher:

1. checks that Python is available;
2. creates `.venv` if needed;
3. installs or refreshes dependencies when requirements change;
4. includes GPU requirements when the configured Whisper device requires them; and
5. starts `main.py`.

The current launcher requests administrator elevation before running. Review `scripts\start_alyssa.bat` if you prefer to change that behavior.

### Manual launch

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python main.py
```

If you intentionally use CUDA Whisper support:

```powershell
.venv\Scripts\python -m pip install -r requirements-gpu.txt
```

---

## LLM providers

Provider selection and model configuration remain in `config.py`. **LLM API secrets do not.**

| `LLM_PROVIDER` | Backend | Main configuration | Credential source |
|---|---|---|---|
| `gemini` | Google Gemini | `GEMINI_MODEL` | Settings / `GEMINI_API_KEY` |
| `openai` | OpenAI | `OPENAI_MODEL`, `OPENAI_BASE_URL` | Settings / `OPENAI_API_KEY` |
| `anthropic` | Anthropic | `ANTHROPIC_MODEL` | Settings / `ANTHROPIC_API_KEY` |
| `ollama` | Local Ollama | `OLLAMA_MODEL`, `OLLAMA_URL` | No cloud API key required |
| `custom_openai` | OpenAI-compatible API | `CUSTOM_MODEL`, `CUSTOM_BASE_URL` | Settings / `CUSTOM_LLM_API_KEY` |

### Ollama

Install Ollama separately and pull the model configured by `OLLAMA_MODEL`, for example:

```powershell
ollama pull qwen2.5:3b
```

Screen vision through Ollama uses `OLLAMA_VISION_MODEL`; make sure that model is also installed if you use the vision features.

---

## Secure LLM credentials

LLM keys are intentionally separated from `config.py`.

### Stored credentials

`credential_store.py` manages:

```text
GEMINI_API_KEY
OPENAI_API_KEY
ANTHROPIC_API_KEY
CUSTOM_LLM_API_KEY
```

Internally, the custom-provider setting is exposed to the Python configuration as `CUSTOM_API_KEY`, while its environment variable is `CUSTOM_LLM_API_KEY`.

### Windows storage

Keys entered through the Settings window are stored outside the project at:

```text
%LOCALAPPDATA%\AlyssaAi\llm_credentials.dat
```

On Windows, the credential payload is encrypted with **Windows DPAPI** and is tied to the current Windows user account.

This keeps LLM keys out of:

- `config.py`;
- the repository;
- normal project ZIP archives; and
- application source updates.

### Environment variables

Environment variables take precedence over the stored credential file.

```powershell
setx GEMINI_API_KEY "your-key"
setx OPENAI_API_KEY "your-key"
setx ANTHROPIC_API_KEY "your-key"
setx CUSTOM_LLM_API_KEY "your-key"
```

After using `setx`, start a new terminal or restart Alyssa so the new environment is visible to the process.

For temporary PowerShell-only configuration:

```powershell
$env:OPENAI_API_KEY = "your-key"
```

### Custom credential directory

For testing or managed deployments, the credential directory can be overridden with:

```text
ALYSSA_DATA_DIR
```

Do not point this at a source-controlled directory unless you understand the implications.

### Non-Windows behavior

The application is Windows-first. On non-Windows systems, `credential_store.py` uses the user's configuration directory and attempts to restrict permissions to the current user, but it does **not** provide the Windows DPAPI encryption used on Windows.

### Other service credentials

The secure store above covers the **LLM provider keys**. Other integrations may use their own configuration or credential files. For example, Google OAuth uses `credentials.json` / `token.json`, while voice and media integrations have their own settings. Check each integration before publishing or sharing a configured project folder.

---

## Voice pipeline

Alyssa's voice path is designed to overlap recording, transcription, generation, speech synthesis, playback, and interruption handling where possible.

### Speech-to-text

`STT_PROVIDER` controls transcription:

```python
STT_PROVIDER = "auto"
```

Available modes include local Whisper and ElevenLabs realtime transcription. In `auto` mode, the runtime can choose the realtime path when its required credential is configured and otherwise use local transcription.

Important speech-recognition settings include:

```text
WHISPER_MODEL_SIZE
WHISPER_DEVICE
WHISPER_COMPUTE_TYPE
SILENCE_SECONDS
MIN_SPEECH_MS
VAD_AGGRESSIVENESS
VAD_ENERGY_FALLBACK_ENABLED
VAD_ENERGY_THRESHOLD_DBFS
VAD_ENERGY_MARGIN_DB
VAD_PREROLL_MS
ADAPTIVE_SILENCE_ENABLED
```

### Text-to-speech

`TTS_PROVIDER` controls spoken replies:

```python
TTS_PROVIDER = "edge"
```

Supported runtime paths include Edge TTS and ElevenLabs.

### Interruption / barge-in

Alyssa can listen for new speech while a response is playing.

```python
ALLOW_INTERRUPTIONS = True
BARGE_IN_REQUIRE_NAME = True
```

When name-gated barge-in is enabled, spoken interruption requires the assistant name together with the stop command. Typed messages can interrupt immediately.

---

## Configuration

Most non-secret runtime behavior is controlled through `config.py` or the Settings UI.

### Assistant

| Setting | Purpose |
|---|---|
| `ASSISTANT_NAME` | Primary spoken assistant name |
| `ASSISTANT_NAME_ALIASES` | Alternate recognized names |
| `FOLLOWUP_GRACE_SECONDS` | Follow-up command grace period |
| `CONVERSATION_TIMEOUT_SECONDS` | Session timeout |

### LLM

| Setting | Purpose |
|---|---|
| `LLM_PROVIDER` | Active LLM backend |
| `GEMINI_MODEL` | Gemini model name |
| `OPENAI_MODEL` | OpenAI model name |
| `OPENAI_BASE_URL` | OpenAI-compatible base URL for OpenAI mode |
| `ANTHROPIC_MODEL` | Anthropic model name |
| `OLLAMA_MODEL` | Local Ollama text model |
| `CUSTOM_MODEL` | Custom OpenAI-compatible model |
| `CUSTOM_BASE_URL` | Custom OpenAI-compatible endpoint |
| `LLM_MAX_OUTPUT_TOKENS` | Response token limit used by the runtime |

LLM API keys are loaded through `credential_store.py`, not hard-coded in this section.

### Speech recognition

| Setting | Purpose |
|---|---|
| `STT_PROVIDER` | Speech-to-text backend |
| `WHISPER_MODEL_SIZE` | Local Whisper model size |
| `WHISPER_DEVICE` | `cpu`, `cuda`, or `auto` |
| `SILENCE_SECONDS` | Base endpointing silence window |
| `MIN_SPEECH_MS` | Minimum sustained speech duration before a turn starts |
| `VAD_AGGRESSIVENESS` | WebRTC speech/noise classification level |
| `VAD_ENERGY_FALLBACK_ENABLED` | Enable adaptive RMS fallback when WebRTC misses processed/virtual mic speech |
| `VAD_ENERGY_THRESHOLD_DBFS` | Absolute minimum level used by the fallback gate |
| `VAD_ENERGY_MARGIN_DB` | Required level above the learned noise floor |
| `VAD_PREROLL_MS` | Audio retained before speech detection so the first syllable is not clipped |
| `DEBUG_AUDIO_LEVELS` | Print peak/gate/noise-floor diagnostics for silent passes |
| `ADAPTIVE_SILENCE_ENABLED` | Adaptive endpoint timing |

### Speech output

| Setting | Purpose |
|---|---|
| `SPEAK_RESPONSES` | Enable spoken replies |
| `TTS_PROVIDER` | TTS backend |
| `EDGE_TTS_VOICE` | Edge TTS voice |
| `TTS_STREAMING_ENABLED` | Enable streaming playback pipeline |
| `TTS_AUDIO_BUFFER_MS` | Client audio prebuffer |

### Safety and automation

| Setting | Purpose |
|---|---|
| `CONFIRM_BEFORE_ACTIONS` | Require confirmation before ordinary actions |
| `POWER_CONFIRMATION_TIMEOUT_SECONDS` | Lifetime of pending protected-action approval |
| `ALLOW_INTERRUPTIONS` | Allow speech to interrupt replies |
| `BARGE_IN_REQUIRE_NAME` | Require the assistant name for spoken interruption |
| `LAUNCH_APPS_IN_BACKGROUND` | Application-launch behavior |

### Memory and plugins

| Setting | Purpose |
|---|---|
| `CONVERSATION_MEMORY_TURNS` | Number of recent turns retained |
| `CONVERSATION_MEMORY_CHARACTERS` | Character cap for recent context |
| `MAX_SAVED_MEMORIES` | Saved-memory limit |
| `PLUGINS_ENABLED` | Enable plugin discovery |
| `ENABLE_BACKGROUND_WATCHER` | Enable proactive plugin checks |
| `REMINDER_UPCOMING_WINDOW_HOURS` | Reminder look-ahead window |

### Companion GUI

| Setting | Purpose |
|---|---|
| `ENABLE_COMPANION_GUI` | Enable the desktop companion |
| `HIDE_CONSOLE_WINDOW` | Hide the console after startup |

The Settings window exposes many common options without requiring direct edits to `config.py`.

---

## Example commands

```text
Alyssa, open Notepad
Alyssa, type hello world
Alyssa, find my latest PDF
Alyssa, remember that I prefer Spotify
Alyssa, remind me to call the dentist tomorrow at 3 PM
Alyssa, set a timer for ten minutes
Alyssa, what's using the most memory?
Alyssa, summarize this webpage
Alyssa, what am I looking at?
```

Typed messages in the companion do not require the assistant's spoken name.

---

## Plugins

Plugins are discovered from `plugins/` by `plugin_loader.py`.

The included plugin modules cover areas such as:

- calculator and unit conversion;
- Google Calendar and Gmail;
- location and weather;
- news;
- reminders;
- timers and stopwatch controls;
- process management;
- webcam/security-camera monitoring;
- system health monitoring;
- web search;
- webpage summarization; and
- optional runtime response modes.

Some plugins require additional credentials, network access, or hardware.

### Plugin interface

A plugin can expose callable functions through `FUNCTIONS` and matching LLM schemas through `TOOLS`.

```python
import random


def roll_die(sides: int = 6) -> str:
    return str(random.randint(1, sides))


FUNCTIONS = {
    "roll_die": roll_die,
}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "roll_die",
            "description": "Roll a die.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sides": {
                        "type": "integer",
                        "minimum": 2,
                    }
                },
            },
        },
    }
]
```

Plugin files whose names begin with `_` are ignored by normal discovery.

A plugin may also expose a watcher function for proactive alerts, depending on the plugin-loader contract used by the runtime.

---

## Local data and privacy

Alyssa combines local processing with optional cloud services.

### Local data

Common local files include:

| File | Purpose |
|---|---|
| `memory.json` | Saved assistant memories |
| `overlay_config.json` | Companion appearance / position settings |
| `token.json` | Google OAuth token when Google integration is enabled |
| `%LOCALAPPDATA%\AlyssaAi\llm_credentials.dat` | DPAPI-protected LLM credentials on Windows |

Additional plugins can create their own local state files.

### Git exclusions

The repository `.gitignore` excludes common private/runtime files including:

```text
credentials.json
token.json
memory.json
llm_credentials.dat
.venv/
build/
dist/
```

The LLM credential file normally lives outside the repository anyway; the ignore rule provides an additional safeguard if `ALYSSA_DATA_DIR` is redirected into the project.

### Cloud processing

Depending on configuration, data may leave the machine:

- cloud LLM providers receive conversation prompts and relevant tool context;
- vision requests may send screenshots/images to the selected cloud provider;
- Edge TTS sends reply text to Microsoft's speech service;
- ElevenLabs modes send audio/text required by those services; and
- network plugins contact their respective external services.

Use Ollama plus local Whisper when you want to minimize cloud inference, while remembering that other enabled integrations may still use the network.

### Action safety

Alyssa includes confirmation logic for potentially consequential actions. These checks reduce accidental execution but should not be treated as authentication or a complete operating-system security boundary.

Do not run the assistant with more privileges than it needs.

---

## Testing

Install development dependencies:

```powershell
.venv\Scripts\python -m pip install -r requirements-dev.txt
```

Run the suite:

```powershell
.venv\Scripts\python -m pytest
```

The tests cover areas including:

- architecture boundaries;
- credential storage;
- conversation/provider message conversion;
- latency pipeline behavior;
- memory;
- assistant-name handling;
- runtime regressions;
- safety behavior;
- Settings GUI behavior;
- tool argument sanitization;
- updater behavior; and
- webpage summarization.

Some tests or runtime paths depend on Windows, GUI, audio, or optional native libraries and are best validated on the target Windows environment.

---

## Start with Windows

The project includes Task Scheduler helper scripts:

```text
scripts\install_startup.bat
scripts\uninstall_startup.bat
```

Review the scripts before enabling automatic startup, especially because Alyssa has desktop-control capabilities.

---

## Troubleshooting

### Python is not found

Install Python from python.org and make sure Python is added to `PATH` during setup.

### The virtual environment is broken

Delete only the local `.venv` directory and run:

```text
scripts\start_alyssa.bat
```

again.

### Dependency installation fails

Upgrade pip and check whether Microsoft C++ Build Tools are required for the Python version you installed.

### Alyssa cannot reach Ollama

Make sure Ollama is running and verify installed models:

```powershell
ollama list
```

Then compare the installed model names with `OLLAMA_MODEL` and `OLLAMA_VISION_MODEL` in `config.py`.

### A cloud LLM does not respond

Check all three of the following:

1. `LLM_PROVIDER` selects the expected backend;
2. the model/base URL is correct for that backend; and
3. the API key is present in Settings or the corresponding environment variable.

Do **not** paste LLM API keys back into `config.py`.

### Where is my LLM key stored?

On Windows, a key saved through Settings is stored at:

```text
%LOCALAPPDATA%\AlyssaAi\llm_credentials.dat
```

The credential payload is protected with Windows DPAPI for the current user.

### Whisper loads, but Alyssa keeps saying/listing "Listening" and never transcribes

If startup shows a line such as:

```text
Whisper model loaded (base.en on cuda/float16).
```

but there are no later lines like:

```text
(Whisper heard: 'Alyssa ...')
```

then Whisper itself is already loaded correctly. The microphone front-end is not
classifying the captured audio as speech, so the turn is being discarded before
Whisper is called.

Current builds use WebRTC VAD as the primary detector and an adaptive RMS-energy
fallback for processed/virtual microphones such as SteelSeries Sonar. They also
keep a short pre-roll so the beginning of "Alyssa" is not clipped.

For microphone diagnostics, temporarily set:

```python
DEBUG_AUDIO_LEVELS = True
```

A silent pass will then report the observed peak, current energy threshold, and
learned noise floor. If the peak remains near digital silence while you are talking,
check `MICROPHONE_DEVICE` and the Windows/Sonar input routing. If speech is visible
but still below the gate, adjust `VAD_ENERGY_THRESHOLD_DBFS` conservatively.

### Voice interruptions trigger too easily

Keep:

```python
BARGE_IN_REQUIRE_NAME = True
```

and consider using a headset microphone or adjusting VAD/endpoint settings.

### A plugin does not load

Check startup output for the plugin import error. Plugins are isolated enough that one failing plugin should not require removing unrelated plugins.

### Settings changes do not appear immediately

Some settings are read at runtime while others are naturally applied during provider/audio initialization. Restart Alyssa after changing provider credentials, environment variables, or low-level audio configuration if behavior appears stale.

---

## Security checklist before sharing the project

Before publishing, sending, or archiving a configured Alyssa installation:

- verify that no API keys were manually added to source files;
- verify `credentials.json` and `token.json` are not included;
- review plugin-specific credential/state files;
- avoid bundling personal `memory.json` unless intended;
- do not include `.venv`, `build`, or `dist` unless specifically required; and
- remember that the Windows LLM credential store normally lives outside the project folder.

---

## License

No license file is included in this package. Add an explicit `LICENSE` file before distributing the project if you want to define reuse, modification, or redistribution terms.
