# Alyssa

Alyssa is a voice assistant I built for my own Windows desktop — she listens
for her name, transcribes speech locally with Faster Whisper, and hands
anything that needs actual reasoning off to whichever LLM I've got configured
(Gemini by default, but Ollama/OpenAI/Anthropic/any OpenAI-compatible endpoint
all work). She can open apps, click around, manage windows, remember stuff I
tell her, and pull in extra abilities through a plugin folder.

This is a personal project, not a product — expect rough edges. I'm putting
it up because it's been genuinely useful to me and might be to someone else.

## What she does

- Always-listening, gated on her name so she's not transcribing everything
- Local speech-to-text (Faster Whisper), or ElevenLabs realtime if you'd
  rather pay for lower latency
- Swappable brain — local Ollama model or a cloud provider, your call
- A little desktop companion window you can type into or talk to
- App/window/media/clipboard/file/screenshot/system control
- Can look at your screen and click things based on what it sees
- Remembers facts across sessions, plus reminders and timers
- Weather, web search, article summaries, system monitoring, and whatever
  else I've bolted on via plugins
- Asks before doing anything destructive

## Before you give her the keys to your PC

Alyssa can type, click, run commands, move files, and generally do things a
person sitting at your keyboard could do. Read through `config.py` and the
`actions/` folder before you point her at anything you care about, and don't
run her elevated unless you have a real reason to.

A few things worth knowing:

- She ignores audio that doesn't include her name (or an alias) — but that
  audio is still transcribed locally to check, it just isn't acted on or sent
  anywhere.
- `CONFIRM_BEFORE_ACTIONS` is on by default. Deleting things, running
  commands, vision-guided clicks, killing processes, emptying the Recycle
  Bin, and power actions (shutdown/restart/sleep) always require a separate
  spoken or typed "yes" regardless of that setting.
- Saying "yes" out loud isn't a security boundary — it just recognizes
  confirmation words, it doesn't check who said them. Anyone near the mic
  can approve a pending action.
- Deleted files go to the Recycle Bin, not straight to oblivion.
- Speech stays local unless you turn on ElevenLabs for STT. Whatever cloud
  LLM you've picked does see your prompts (and screenshots, if you use
  vision), same as talking to it directly. Edge TTS sends your reply text to
  Microsoft to turn into audio.
- **API keys don't live in `config.py` anymore.** They're stored through
  `credential_store.py` in your OS's own credential manager — Windows
  Credential Manager, macOS Keychain, or the Linux Secret Service (GNOME
  Keyring/KWallet). The Settings window writes there directly now instead of
  patching a plaintext key into a file on disk. If you're updating from an
  older copy that still has real keys sitting in `config.py`, the app
  migrates them into the keyring automatically on next launch and blanks the
  file — see `credential_store.py`'s docstring if you want the details.
- `memory.json`, Google OAuth files, and OAuth tokens are gitignored. Don't
  commit them.

None of this makes her safe to run unsupervised. It just means the obvious
footguns have a confirmation step in front of them.

## How it's laid out

Kept this stacked on purpose so nothing has to import in a circle:

```text
main.py / overlay
        |
        v
brain (dialogue, providers, vision)
        |
        v
actions (desktop/system adapters) ----> plugin_loader ----> plugins
        |
        v
OS / audio / network
```

`brain/dialogue.py` owns the actual conversation loop and orchestration.
`brain/tool_catalog.py` / `brain/tool_registry.py` own the tool schemas —
built-in and plugin — that both the providers and dialogue share, so the
provider code never has to import the orchestration code. Desktop automation
lives behind `actions/desktop.py` so importing the reasoning layer doesn't
drag in PyAutoGUI or require an active desktop session; the couple of things
`actions` needs from `brain` (vision, resetting a conversation) get injected
through `actions/bridges.py` instead of a direct import.

Mostly this exists so I can swap out the automation layer or add a plugin
without provider logic breaking, and so I can unit-test the provider/dialogue
code without a GUI attached.

## What you need

- Windows 10 or 11
- Python 3.10+ from [python.org](https://www.python.org/downloads/)
- [Microsoft C++ Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/)
  (Desktop development with C++ workload) — only needed if a package doesn't
  have a prebuilt wheel for your Python version, which happens with
  `webrtcvad-wheels` on newer Pythons
- A mic and speakers
- At least one LLM provider set up
- Internet, if you're using a cloud model, Edge TTS, or anything network-y

Want to run everything local? Grab [Ollama](https://ollama.com/) and pull the
default model:

```powershell
ollama pull qwen2.5:3b
```

## Getting it running

1. Clone/download this repo.
2. Open `config.py` and pick an `LLM_PROVIDER` — or just leave it and set it
   from the companion's Settings window once she's running.
3. Don't want NVIDIA CUDA packages pulled in? Set `WHISPER_DEVICE = "cpu"`
   before your first launch.
4. Double-click `scripts\start_alyssa.bat`.

That script sets up a `.venv`, installs what's needed, and starts her.
Later launches reuse the same environment unless a requirements file changed.

Doing it by hand instead:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python main.py
```

If `WHISPER_DEVICE` is `auto` or `cuda`, also install `requirements-gpu.txt` —
heads up, that's a few gigs of NVIDIA packages.

## Picking a provider

Set `LLM_PROVIDER` in `config.py`:

| Value | Provider | What it needs |
| --- | --- | --- |
| `gemini` | Google Gemini (default) | A Gemini key, added via Settings |
| `ollama` | Local Ollama model | Ollama installed, `OLLAMA_MODEL` pulled |
| `openai` | OpenAI | An OpenAI key |
| `anthropic` | Anthropic | An Anthropic key |
| `custom_openai` | Any OpenAI-compatible endpoint | Base URL, model, optional key |

Easiest way to set a key is through the Settings window (it goes straight
into your OS keyring). If you'd rather use environment variables instead —
handy for a server/headless setup — those still work and take priority over
whatever's in the keyring:

```powershell
setx OPENAI_API_KEY "your-key"
setx ANTHROPIC_API_KEY "your-key"
setx GEMINI_API_KEY "your-key"
setx CUSTOM_LLM_API_KEY "your-key"
```

(Restart your terminal, or Alyssa, after `setx` — it doesn't apply to an
already-open shell.)

Vision uses whatever provider you've got selected; Ollama folks also need to
pull a vision model (`llava` by default).

## Stuff you can say to her

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

"Alyssa, stop" (or "stop, Alyssa") interrupts whatever she's saying. Typed
messages in the companion window don't need her name first.

## config.py cheat sheet

The settings I actually touch most often:

| Setting | What it does |
| --- | --- |
| `ASSISTANT_NAME` | The wake word |
| `LLM_PROVIDER` | Which brain is active |
| `WHISPER_MODEL_SIZE` | STT model size |
| `WHISPER_DEVICE` | `cpu`, `cuda`, or `auto` |
| `STT_PROVIDER` | `auto`, local Whisper, or ElevenLabs realtime |
| `SILENCE_SECONDS` | How long a pause has to be before she thinks you're done |
| `TTS_PROVIDER` | `edge` or `elevenlabs` |
| `TTS_AUDIO_BUFFER_MS` | Prebuffer for ElevenLabs streaming playback |
| `CONFIRM_BEFORE_ACTIONS` | Confirm ordinary actions too, not just protected ones |
| `POWER_CONFIRMATION_TIMEOUT_SECONDS` | How long a pending protected action stays pending |
| `ALLOW_INTERRUPTIONS` | Whether talking over her cancels a reply |
| `BARGE_IN_REQUIRE_NAME` | Require her name + "stop" together to interrupt |
| `CONVERSATION_MEMORY_TURNS` | How many recent turns she keeps in context |
| `PLUGINS_ENABLED` | Whether `plugins/` gets loaded at all |
| `ENABLE_BACKGROUND_WATCHER` | Proactive plugin checks (reminders, etc.) |
| `ENABLE_COMPANION_GUI` | Show/hide the desktop companion |

Most of this is also editable from the Settings window without touching the
file directly. Its Updates tab pulls the latest GitHub release and overwrites
application files (including any local edits you've made) while keeping your
settings and data — so commit or back up code changes first if you've been
poking around.

### On latency

Endpointing defaults to a 300ms window that adapts between 240–420ms based on
how fast you're talking. Speech has to run 120ms before it counts; barge-in
detection kicks in after 150ms. With name-gated interruption on, playback only
stops once the captured audio has both her name and the full word "stop" in
it. Context holds 11 turns and slides once it hits 4,000 characters.

If you've got an ElevenLabs key, `STT_PROVIDER = "auto"` switches to Scribe
realtime over a persistent WebSocket with partial transcripts while you're
still talking. No key, it falls back to local Whisper. ElevenLabs TTS uses
the Flash model with clause-level streaming and a 100ms prebuffer on raw PCM.
Edge TTS still pipelines by clause but has to finish encoding each clause
before it can start playing.

Text streams from every provider I support. Generation, TTS, playback, and
interruption listening all overlap — new speech gets transcribed and checked
before a name-gated interruption actually cancels anything. Typed messages
interrupt instantly, no gating.

## Plugins

Everything in `plugins/` loads at startup. What's in there right now:

- Calculator / unit conversion
- Read-only Google Calendar + Gmail
- IP-based location + Open-Meteo weather
- Web search and page summarization
- News digests
- Reminders, timers, stopwatch
- Process management, temp-file cleanup
- System health monitoring
- Optional webcam motion alerts
- Caveman mode / Ponytail response-length modes

Some need extra credentials or hardware — check the docstring at the top of
each plugin file for setup and what it sends where. The Google plugin uses
read-only OAuth scopes and keeps its token in `token.json`.

### Writing your own

A plugin is just a `.py` file in `plugins/` exporting `FUNCTIONS` and a
matching `TOOLS` schema. Files starting with `_` get skipped.

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

Add a `check_watch() -> str | None` function (plus an optional
`WATCH_INTERVAL_SECONDS`) if you want it to proactively speak up on its own.

## What's stored locally

| File | What's in it |
| --- | --- |
| `memory.json` | Facts she's been told to remember |
| `reminders.json` | Reminders |
| `token.json` | Google OAuth token, if you've enabled that plugin |
| `overlay_config.json` | Companion window position/appearance |

API keys aren't in this list anymore — those live in the OS keyring, not a
local file. Memories are plain JSON with basic keyword matching, nothing
fancy. Conversation history for the current session lives in memory only and
expires after the configured timeout.

## Running the tests

```powershell
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

## Launching on startup

`scripts\install_startup.bat` registers her with Windows Task Scheduler.
`scripts\uninstall_startup.bat` undoes that. Read the script before running
it, and only set this up once you're comfortable with what she's allowed to
do unattended.

## When something's broken

- **Python isn't found** — reinstall from python.org and check "Add Python
  to PATH" during setup.
- **Setup seems stuck or broken** — delete the local `.venv` folder and rerun
  `scripts\start_alyssa.bat`.
- **Can't reach Ollama** — make sure Ollama's actually running and
  `ollama list` shows the model you configured.
- **No response from a cloud provider** — double-check the key and model in
  Settings for whichever provider is selected.
- **She keeps interrupting herself on background noise** — keep
  `BARGE_IN_REQUIRE_NAME = True` (needs her name + "stop" together) and,
  if that's still not enough, try a headset mic instead of open speakers.
- **A plugin didn't load** — check the startup console. Import errors get
  printed there and the rest of the plugins keep loading regardless.
