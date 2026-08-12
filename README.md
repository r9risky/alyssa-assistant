# Alyssa — DIY always-listening PC assistant (Windows, 100% free)

A local voice assistant with no wake word — it listens continuously,
transcribes what you say locally, sends it to a **local free AI model (via
Ollama)** to decide what to do, then actually does it on your PC (opens
apps, browses to websites, opens/deletes files, types text, presses
shortcuts, runs commands, and remembers facts across restarts).

**Everything runs on your own PC — no API key, no account, no cost.**
The only step that touches the internet during normal use is fetching the
natural-sounding voice reply (Microsoft's free Edge TTS service).

## ⚠️ Important safety note

There's no wake word in the traditional sense — instead, Alyssa only acts on
speech that mentions the name "Alyssa" somewhere in the sentence (e.g. "Alyssa,
open notepad"). It's still transcribing everything in the background to
check for the name, so normal conversation that never says "Alyssa" is safely
ignored. Two more safeguards are built in:

- `run_command` (runs arbitrary shell commands) and `delete_file` always
  ask you to confirm with y/n in the terminal, no matter what
  `CONFIRM_BEFORE_ACTIONS` is set to in `config.py` — these are the two
  actions that are genuinely hard to undo.
- If you'd rather have every single action confirmed, set
  `CONFIRM_BEFORE_ACTIONS = True` in `config.py`.

## 1. Install Python

Python 3.10+ from https://python.org/downloads — check **"Add Python to
PATH"** during install.

## Choosing an LLM provider

Alyssa can run on several different "brains" - set `LLM_PROVIDER` in
`config.py` (or pick it from Settings → Assistant → **LLM provider** in the
companion GUI) to whichever you want:

| `LLM_PROVIDER` | What it is | Needs |
|---|---|---|
| `"ollama"` | Free, local, private, unlimited use | A decent PC, no API key |
| `"gemini"` | Google's cloud API (default) | `GEMINI_API_KEY` |
| `"openai"` | OpenAI's cloud API (GPT models) | `OPENAI_API_KEY` |
| `"anthropic"` | Anthropic's cloud API (Claude models) | `ANTHROPIC_API_KEY` |
| `"custom_openai"` | Any provider speaking the OpenAI chat-completions format - Groq, OpenRouter, Together, a local LM Studio/vLLM server, etc. | `CUSTOM_BASE_URL` (+ `CUSTOM_MODEL`, `CUSTOM_API_KEY` if needed) |

Each provider's API key and model are separate settings in `config.py`
(`OPENAI_MODEL`, `ANTHROPIC_MODEL`, etc.), so you can switch back and forth
without losing any of them. The companion GUI's Settings window only shows
the fields for whichever provider is currently selected, with a **Verify**
button next to each cloud key to check it's accepted before you rely on it.

Screen vision ("Alyssa, what am I seeing?") also uses whichever provider is
selected - Gemini, OpenAI, and Anthropic's models are all multimodal
already, so nothing extra is needed beyond the API key. See "Seeing your
screen" further down for details, including the Ollama vision-model setup.

## 2. Install Ollama (the free local AI engine)

1. Download and install from https://ollama.com.
2. It runs quietly in the background automatically after install.
3. Open Command Prompt and run:
   ```
   ollama pull qwen2.5:14b
   ```
   (Roughly 9GB. If your PC has under 16GB RAM, use a smaller model
   instead, e.g. `ollama pull qwen2.5:3b`, and update `OLLAMA_MODEL` in
   `config.py` to match.)

## 3. Download this project

Unzip everything into one folder, e.g. `C:\Users\<you>\alyssa\`.

## 4. Install dependencies

```
cd C:\Users\<you>\alyssa
pip install -r requirements.txt
```

If `sounddevice` complains about missing drivers, install the **Microsoft
Visual C++ Redistributable** and retry. If you see `ModuleNotFoundError:
No module named 'pkg_resources'`, run `pip install "setuptools<81"`.

## 5. Run it

Double-click **`start_alyssa.bat`** — first run automatically creates a
private virtual environment and installs everything in
`requirements.txt` for you (no manual `pip install` needed), then Alyssa
starts. Every run after that reuses the same environment and starts
straight away, unless `requirements.txt` changes, in which case it
reinstalls automatically. (You can still run `python main.py` directly if
you'd rather manage your own environment.)

You'll hear "Alyssa ready. At your service." — then just start talking whenever you want it
to do something, making sure to say "Alyssa" somewhere in the sentence.

## Turning it into a standalone .exe (optional)

If you'd rather have one `Alyssa.exe` you can copy anywhere without a
Python install at all:

1. Run `start_alyssa.bat` at least once first (so dependencies exist).
2. Run `build_alyssa.bat`. This uses PyInstaller to produce
   `dist\Alyssa.exe`.
3. Copy `dist\Alyssa.exe`, `config.py`, and the `memory_db` folder (if it
   exists) into one folder together. `config.py` is deliberately **not**
   baked into the exe, so you can still tweak settings (voice, model, API
   key) by editing that file, without rebuilding.

Note: PyInstaller builds are OS/architecture-specific — build on the same
type of Windows machine you'll run it on. First launch of the exe is
slower than later launches (it's unpacking itself into a temp folder each
time with `--onefile`); if that bugs you, drop `--onefile` from
`build_alyssa.bat` for a `dist\Alyssa\` folder build instead, which starts
faster at the cost of being a folder instead of one file.

## Starting automatically at login, with admin rights (optional)

Alyssa sometimes needs admin rights (some `run_command`/system actions
work better elevated), and it's nice for her to just be running when you
log in. `install_startup.bat` sets both up together via a Windows Task
Scheduler task, rather than a Startup-folder shortcut — a shortcut can't
auto-elevate (it either skips admin rights, or nags you with a UAC prompt
every single login), while a scheduled task set to "run with highest
privileges" makes that trust decision once, when you install it, and then
just starts elevated silently from then on.

1. Run `start_alyssa.bat` (or `build_alyssa.bat`) at least once first, so
   there's something for the task to launch.
2. Double-click `install_startup.bat`. It'll ask Windows for admin rights
   (needed to register the task) — approve the prompt.
3. That's it — Alyssa now starts automatically, elevated, at every login.
   To test without logging out, open Task Scheduler, find
   "AlyssaAssistant", right-click → Run.

You can also enable/disable this from Settings → Companion →
**Start at login (admin)**, which just runs these same two scripts.

To turn it off later, run `uninstall_startup.bat` (or the Disable button
in Settings).

## What it can do

| Ability | Example |
|---|---|
| Open an app | "Alyssa, open notepad" |
| Type text | "Alyssa, type hello world" |
| Press a shortcut | "Alyssa, press ctrl+s" |
| Open a website | "Alyssa, go to youtube" |
| Open a specific file | "Alyssa, open my resume in documents" |
| Delete a file/folder | "Alyssa, delete the old_photos folder" (goes to Recycle Bin, not permanent) |
| Run a command | anything else — general Windows command line access |
| Search for files | "Alyssa, find that invoice pdf" |
| Remember something | "Alyssa, remember that my dog's name is Max" |
| Forget something | "Alyssa, forget the thing about my dog" |
| Media control | "Alyssa, pause the music" / "skip this song" / "turn it up" |
| Window management | "Alyssa, minimize this" / "snap this window left" / "show the desktop" |
| Clipboard | "Alyssa, copy this to the clipboard: ..." / "what's on my clipboard?" |
| Screenshot | "Alyssa, take a screenshot" |
| Screen description / feedback | "Alyssa, what am I seeing?" / "Alyssa, does this code look right?" |
| Play music | "Alyssa, play some jazz" / "Alyssa, put on Bohemian Rhapsody on YouTube Music" |
| Date/time | "Alyssa, what time is it?" |
| Power actions | "Alyssa, lock the PC" / "put it to sleep" / "sign out" / "restart" / "shut down" |
| Start fresh | "Alyssa, forget what we were just talking about" |

Sleep, sign-out, restart, and shutdown use a spoken confirmation. Alyssa
asks whether you want to continue, then listens for a simple spoken "yes"
or "no" - no terminal input or popup is required.

## Interrupting her

You can talk over Alyssa any time she's mid-reply, but by default she only
actually stops if you say her name - "Alyssa, stop" or "stop it Alyssa"
both work. Other talking nearby (background noise, the TV, someone else in
the room) doesn't cut her off. No need to say her name again for the
command itself once she does stop - whatever you'd already started saying
gets picked up automatically, so you don't have to repeat yourself.
Because she has to hear the whole thing and transcribe it before she can
tell whether you named her, there's a brief delay between when you start
talking and when she actually goes quiet - she's not being ignored, she's
just checking who you were talking to first.

If you'd rather she stop the instant she hears any sustained speech, no
name required, set `BARGE_IN_REQUIRE_NAME = False` in config.py - that's
the old behavior.

This is on by default (`ALLOW_INTERRUPTIONS = True` in config.py). Set it
to `False` if you'd rather she always finish a reply before the mic
listens again.

Because this works by having the mic listen while her own voice is coming
out of your speakers, a laptop's built-in mic/speakers can occasionally
pick up her own TTS audio and misread it as an interruption attempt. With
the default name requirement on, that's harmless - it just gets
transcribed, found not to contain her name, and ignored. If you set
`BARGE_IN_REQUIRE_NAME = False` and notice `(Interrupted - listening...)`
firing right after she starts talking with nothing actually said, a
headset mic is the most reliable fix - see `BARGE_IN_MIN_SPEECH_MS` and
`BARGE_IN_VAD_AGGRESSIVENESS` in config.py for tuning options short of that.

## Saved memory storage

Permanent memories live in a plain local `memory.json` file next to
`memory.py` - no database, no downloaded model, just a small text file
that's readable in any editor. When deciding which saved facts are
relevant to what you're asking, Alyssa matches on shared/overlapping
words rather than deep semantic meaning - so "play my music" will
correctly surface a fact like "prefers Spotify for music," but a fact
phrased with completely different words than your question (e.g. asking
about "my dog" when the saved fact only says "the golden retriever is
named Max") may not surface on wording alone. In practice this covers
most personal references well, since Alyssa tends to save facts using
the same words you or she used the first time something got resolved
(see "Adapting to non-specific commands" above).

Memories are automatically compacted: whitespace is normalized, duplicate
facts are removed, each fact is limited to 400 characters, and Alyssa
retains the newest 75 facts. You can adjust both limits in `config.py`.
Up to 20 relevant saved facts are sent with each AI request, which keeps
responses faster and uses fewer model tokens.

For each request, Alyssa prioritizes memories that share words with what
you're asking about while retaining a small amount of recent context.
She also keeps a short, private in-session summary of completed actions,
so follow-ups such as "now type this" and "close it" are more reliable.

If you're upgrading from a version that used the ChromaDB-based
`memory_db/` folder, its contents are exported into `memory.json`
automatically the first time you run this version - but only if that
older version's `chromadb` package is still installed in your `.venv`
(the current `requirements.txt` no longer includes it, to save the
several hundred MB it and its embedding model take up). If you want that
one-time export to happen, run this version once *before* deleting your
`.venv` or letting `start_alyssa.bat` reinstall dependencies; otherwise
just delete the old `memory_db/` folder by hand once you've confirmed
`memory.json` has what you need; nothing else references it anymore.

## Performance tuning

A few things make Alyssa feel snappier out of the box, no setup needed -
worth knowing about in case you want to tune them further:

- **Warm start.** The Whisper model and (if `LLM_PROVIDER = "ollama"`) the
  Ollama model both load in the background the moment Alyssa starts, in
  parallel with her saying "ready" and with you walking over to your mic -
  instead of both loading cold the moment you say your first real command,
  which used to be the slowest single thing that happened all session.
- **`WHISPER_CPU_THREADS`** (`config.py`) controls how many CPU threads
  transcription uses. `0` (default) auto-picks a generous number based on
  your core count; set a specific number if you'd rather leave more
  headroom for something else running at the same time.
- **`OLLAMA_KEEP_ALIVE`** (`config.py`, Ollama only) controls how long
  Ollama keeps the model loaded after your last command before unloading
  it. Default `"10m"` rides out a normal pause in conversation; raise it
  (e.g. `"30m"`) if you tend to go quiet for a while, or set it to `"-1"`
  to never unload it at all while Alyssa is running.
- **`TTS_PIPELINE_SENTENCES`** (`config.py`, default `True`) - for a reply
  with more than one sentence, the next sentence starts synthesizing in
  the background while the current one is already playing, instead of
  waiting for the entire reply to come back from Edge TTS before any of it
  is spoken. Most replies are one sentence already by design, so this
  mostly helps longer explanations and error messages. Set to `False` to
  go back to synthesizing the whole reply as a single request.

## Token compression (cutting API costs)

Alyssa compresses outgoing requests before they hit whichever `LLM_PROVIDER`
is configured - same idea as [OmniRoute](https://github.com/diegosouzapw/OmniRoute)'s
RTK+Caveman pipeline, just scaled to what a voice assistant actually sends.
It runs automatically, on every provider, with no setup needed.

What it does:
- **Collapses repeated lines** - if a plugin or tool dumps the same line
  over and over (a retry warning, a repeated log line), it's folded into
  one copy plus a `(x N)` count.
- **Skips resending unchanged content** - if the exact same tool result or
  reply already appears earlier in the same request (e.g. an older turn's
  weather lookup that hasn't changed), later copies are replaced with a
  short reference instead of being sent again in full.
- **Lightly abbreviates verbose phrasing** in older turns only (`"in order
  to"` -> `"to"`, and similar) - a small, fixed list, never anything that
  could change a meaning.

What it never touches:
- Code fences, inline code, URLs, and JSON-looking content are protected
  from both stages, in case a tool result contains something structured
  that needs to survive byte-for-byte.
- The system prompt (your live instructions to the model) and your current
  utterance are left completely alone, plus a configurable number of the
  most recent conversation turns - only older/verbose content gets touched.
- An "inflation guard" skips compression per-message wherever the result
  wouldn't actually be shorter than the original.

Tune it in `config.py`:
- `COMPRESSION_ENABLED` (default `True`) - turn it off entirely if you'd
  rather send everything verbatim.
- `COMPRESSION_MODE` - `"off"`, `"safe"` (repeat-collapse/dedupe only, no
  wording changes), `"balanced"` (default, adds light phrase abbreviation),
  or `"aggressive"` (same stages, smaller protected window).
- `COMPRESS_SYSTEM_PROMPT` (default `False`) - opt in if you've checked the
  abbreviated system prompt still behaves correctly.
- `COMPRESSION_PROTECT_RECENT_TURNS` (default `1`) and `COMPRESSION_MIN_CHARS`
  (default `200`).

Ask "Alyssa, how much have you compressed?" (or "token compression stats")
at any point for a spoken summary of the session's savings so far.

## Conversation memory (follow-ups)

Alyssa now keeps the last few exchanges of a session in memory (RAM only,
never written to disk), so a follow-up makes sense without repeating
yourself:

```
"Alyssa, open notepad"     -> opens Notepad
"Alyssa, now type hello"   -> types into the Notepad she just opened
```

This resets automatically after a few minutes of silence
(`CONVERSATION_TIMEOUT_SECONDS` in `config.py`), and you can clear it any
time by saying something like "Alyssa, start fresh" or "forget what we
were just talking about." It's separate from `remember_fact`'s permanent
memory database — that long-term memory is untouched by a conversation
reset.

Alyssa still won't ask you clarifying questions for ordinary ambiguous
requests — she makes her best guess and acts, since this is a hands-free
voice assistant. The one exception: if a destructive command
(`delete_file`, `run_command`, `system_power_action`) is so ambiguous that
even the conversation history above gives her nothing to go on (e.g.
"delete it" completely out of the blue), she'll ask one short question
instead of guessing — just say her name again to answer.

## Adapting to non-specific commands

You don't have to phrase things exactly. Alyssa reads past the literal
wording to the underlying goal and picks whichever tool fits, e.g.:

```
"Alyssa, it's too quiet"        -> turns the volume up
"Alyssa, get this off my screen" -> minimizes the window
"Alyssa, save this real quick"   -> presses ctrl+s
```

When a vague request is also personal — which app counts as "your
music," where "the office folder" is — she checks what she already
remembers about you first, and once she resolves it, saves that mapping
with `remember_fact` so the same phrasing resolves instantly next time
instead of being re-guessed. That's the main way she adapts to you
specifically over time; just ask her something like "Alyssa, what do you
remember about me?" any time to hear what she's picked up (the database
itself, `memory_db/`, isn't a plain text file the way `memory.json` used
to be, so asking her directly - or running `memory.load_memories()` from
a Python shell in the project folder - is the easiest way to check).

## Adding your own abilities (plugins)

Drop a `.py` file into the `plugins/` folder and Alyssa picks it up
automatically on the next launch — no need to touch `actions.py` or
`brain.py`. A plugin just defines:

- `FUNCTIONS` — a dict of `{tool_name: python_function}`
- `TOOLS` — a list of tool schemas in the same format as `brain.py`'s
  built-in `TOOLS` list, telling the model the ability exists and what
  arguments it takes

See `plugins/example_dice_and_jokes.py` for a complete, working example
("Alyssa, roll a d20" / "tell me a joke"). Delete it, or rename it to
start with `_`, to turn that example off without affecting any other
plugin. Set `PLUGINS_ENABLED = False` in `config.py` to skip loading
plugins entirely.

## Proactive behavior — Alyssa talking first

Everything above is reactive: you say something, she responds. A
background watcher thread (started from `run_assistant_loop` in
`main.py`, parallel to the always-listening loop) makes some things
proactive instead — Alyssa can speak up on her own when something needs
your attention, not just when asked. This is the actual architectural
difference between "a voice assistant that answers questions" and
something closer to Jarvis.

Any plugin can opt in by defining a module-level function:

```python
def check_watch() -> str | None:
    ...  # return a string to have Alyssa say it unprompted, or None/"" for nothing to report
```

and optionally `WATCH_INTERVAL_SECONDS = 120` (how often the watcher loop
calls it — defaults to 120s if omitted). The watcher loop calls every
plugin's `check_watch()` on its own schedule and speaks whatever comes
back. Set `ENABLE_BACKGROUND_WATCHER = False` in `config.py` to turn this
off entirely and make her purely reactive again.

Plugins that already ship with a `check_watch()`:

- **`plugins/system_watch.py`** — CPU/RAM/disk/battery monitoring via
  `psutil`. Also adds `get_system_status()` for on-demand checks ("how's
  my system doing?"). Warns once per condition (edge-triggered), not
  every cycle — e.g. "your disk is almost full" or "a process is
  pegging the CPU". Requires `pip install psutil`.
- **`plugins/security_camera.py`** — webcam/IP-camera motion detection
  with a spoken alert. Off by default; say "turn on the security
  camera" to arm it. Never records or uploads footage — only compares
  consecutive frames for motion, or saves a single still if you
  explicitly ask for a snapshot. Requires `pip install opencv-python`.
- **`plugins/news_digest.py`** — a daily spoken news/research briefing
  built on the existing `plugins/web_search.py` lookup (no extra API key).
  Configure `DIGEST_TOPICS` and `DIGEST_HOUR` at the top of the file;
  leave `DIGEST_TOPICS` empty to keep the on-demand
  `get_news_digest()` ability without the daily briefing.
- **`plugins/calendar_gmail.py`** — Google Calendar + Gmail (read-only).
  "What's my next meeting", "any important emails", plus a proactive
  "you have a meeting in 10 minutes" alert. Needs a one-time Google
  Cloud OAuth setup — full step-by-step instructions are in that file's
  docstring, and everything else in the plugin no-ops with a clear
  spoken message until you've done it.

## Compound / multi-step commands

Alyssa already chains multiple actions from one instruction — the
tool-calling loop in `brain.py`'s `handle_command()` keeps looping (up to
6 rounds) as long as the model keeps requesting tool calls, so "close all
my browser tabs, mute Spotify, and pull up my notes" runs as one spoken
command, not three separate ones. Nothing extra to enable here.

## Acting on what she sees ("Alyssa, click the Send button")

`describe_screen` (below) only narrates what's on screen. `click_screen_element`
goes further: it asks the vision model to locate a described UI element
(as a normalized on-screen position) and clicks it with `pyautogui`, so
she can act on the screen, not just describe it. It's always confirmed
first (same as `delete_file`/`run_command`), since a misplaced click can
do anything — and it's inherently approximate, since it's a language
model estimating pixel coordinates from a downscaled screenshot, not a
real UI-element lookup. Works best on clearly labeled, distinct targets.
For "click the search bar and type cats", she calls
`click_screen_element` then `type_text` as a natural follow-up.

## Voice-based access control (optional)

Set `VOICE_ID_ENABLED = True` in `config.py` to require that a spoken
"yes" approving a protected action (`delete_file`, `run_command`,
`system_power_action`, `click_screen_element`) also sound like *your*
enrolled voice, not just any voice near the mic. Say "Alyssa, enroll my
voice" first — she'll ask you to say a few phrases and build a
voiceprint (`voiceprint.json`, stored locally, next to `config.py`). A
typed "yes" in the desktop companion's chat box always still works,
since typing already implies physical keyboard access.

**Be clear-eyed about what this is**: `voice_id.py` is a lightweight,
fully local timbral fingerprint (mel-filterbank energy statistics +
cosine similarity), built with just `numpy` on purpose, so this feature
doesn't drag in a multi-GB deep-learning dependency (real speaker-
embedding models are typically PyTorch-based) for something most people
will use casually. It's a reasonable deterrent against "someone else
near the mic said yes to a delete" — it is **not** robust against a
deliberate, motivated voice-cloning attempt. Don't rely on it for
anything you couldn't tolerate a false accept on. If it starts
misfiring against your own voice, lower `VOICE_ID_SIMILARITY_THRESHOLD`
a bit and re-enroll somewhere quiet.

## Desktop companion GUI (new)

By default, Alyssa now shows up as a small movable character on your
screen — Desktop-Mate style — instead of (or alongside) the plain console
window:

- **Drag** her body to move her anywhere on screen.
- **Drag the little grip circle** in her bottom-right corner to resize her.
- **Right-click** her for a menu — including **Settings…**, which opens a
  window to change her name, voice, LLM provider/model/API key (Ollama,
  Gemini, OpenAI, Anthropic, or any custom OpenAI-compatible endpoint - see
  "Choosing an LLM provider" above), and confirmation behavior, *and* her
  appearance (swap in your own `.png`/`.jpg`/`.gif`/`.svg` character image,
  resize, adjust opacity, toggle always-on-top).
- Whenever she speaks, her reply pops up in a **speech bubble** above her
  head for a few seconds.
- If you right-click → **Hide**, use the little tray icon (bottom-right of
  your taskbar) to bring her back or quit.

### PNGTuber-style talking animation

Alyssa ships with two bundled images in `assets/` — `nottalk.png` (mouth
closed) and `talkopen.png` (mouth open) — that automatically swap back and
forth whenever she's actually speaking, exactly like a PNGTuber avatar.
From **Settings → Companion** you can:

- Swap in your own **not-talking** and **talking** images independently
  (`.png`/`.jpg`/`.gif`/`.svg`), with a **Preview talking** checkbox to see
  the swap before saving.
- Toggle **"Swap talk/not-talk image while speaking"** off if you'd rather
  she stay on one image.
- Toggle **"Bounce while talking"** and set the bounce height — a little
  rhythmic hop synced to when she's speaking.
- Toggle **"Dim her while she's not talking"** and set how dim (as a % of
  her normal opacity), so she visually fades a bit whenever she's idle and
  comes back to full opacity while she's speaking.

If you remove/blank out the images, she falls back to a small original
chibi character drawn directly in `overlay.py` — no copyrighted art
involved.

This adds one extra dependency, **PySide6**, already listed in
`requirements.txt` (so `start_alyssa.bat` installs it automatically). If
you'd rather skip the GUI entirely and just use the original console-only
version, set `ENABLE_COMPANION_GUI = False` in `config.py`.

Her position, size, opacity, and character image are remembered in
`overlay_config.json` (created automatically the first time you move or
resize her) — separate from `config.py`, which stays focused on the
assistant's own behavior.

## Seeing your screen ("Alyssa, what am I seeing?")

Ask Alyssa anything about what's currently on your screen and she'll
grab a fresh screenshot (kept in memory only, never saved to disk) and
either describe it or answer your specific question:

```
"Alyssa, what am I seeing?"          -> "You're watching a YouTube video on Minecraft."
"Alyssa, does this code look right?" -> looks at the visible code and answers
"Alyssa, what does this error say?"  -> reads out what's in the error dialog
```

This uses whichever `LLM_PROVIDER` you've set in `config.py`:

- **`"gemini"`** (the default): works immediately, no extra setup - Gemini's
  models already handle images. **Note:** this means that one screenshot's
  pixels get uploaded to Google's API for that request, same as any other
  Gemini call your commands make.
- **`"ollama"`**: uses a *separate* model from your normal `OLLAMA_MODEL`,
  set via `OLLAMA_VISION_MODEL` in `config.py` (default `"llava"`), since
  most fast tool-calling text models can't see images. Pull one first:
  `ollama pull llava`. This keeps everything fully local - nothing leaves
  your PC.

## Playing music ("Alyssa, play some jazz")

```
"Alyssa, play some music"                        -> opens/resumes Spotify
"Alyssa, put on Bohemian Rhapsody"                -> opens Spotify, searches for it
"Alyssa, play some lofi on YouTube Music"         -> opens YouTube Music, searches for it
```

Tries the **Spotify desktop app first** (same detection `open_app` uses);
if it isn't installed, falls back to opening **open.spotify.com** in your
browser instead. Same idea for **YouTube Music** - there's no official
Google desktop app for it, so unless you have an unofficial one installed
(see `_APP_EXE_ALIASES`/`_KNOWN_APP_PATHS` in `actions.py` if you want to
point it at yours), it opens **music.youtube.com** in the browser.

One honest limitation: neither service exposes a way to programmatically
pick one exact track out of search results without OAuth API access, which
this project deliberately doesn't set up (extra accounts/keys just for
this). So a song/artist request opens search results for it rather than
guaranteeing that exact track starts playing - you pick the track, and
`media_play_pause` / `media_next_track` / `media_previous_track` / the
volume tools control it normally from there. A blank "play some music"
request (no song named) just opens/resumes the app or site directly.

## Using a GPU for Whisper

Alyssa now defaults to the `large-v3-turbo` Whisper model
(`WHISPER_MODEL_SIZE` in `config.py`), which is much more accurate than the
old `base.en` default but is a bigger model - it runs great on a decent
NVIDIA GPU but can feel slow doing every transcription on CPU alone.

`WHISPER_DEVICE = "auto"` (the default) detects an NVIDIA GPU automatically
and uses it if found, falling back to CPU otherwise. The CUDA/cuDNN
libraries it needs (`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`,
`nvidia-cuda-runtime-cu12`) live in `requirements-gpu.txt`, a separate
file from the main `requirements.txt` - `start_alyssa.bat` checks
`WHISPER_DEVICE` in `config.py` and only installs it when set to `"auto"`
or `"cuda"`, automatically skipping it (and the several GB it takes up)
when `WHISPER_DEVICE = "cpu"`. Switching `WHISPER_DEVICE` between `"cpu"`
and `"auto"`/`"cuda"` is picked up automatically next launch - no need to
delete `.venv` or edit `requirements.txt` by hand. Drop `WHISPER_MODEL_SIZE`
down to `small.en` or `base.en` in `config.py` for a CPU-friendly size if
you're staying on `WHISPER_DEVICE = "cpu"`.

**If it's still running slow despite having an NVIDIA GPU**, the most
common cause is Windows simply not being able to find those pip-installed
CUDA DLLs - a real system-wide CUDA Toolkit install adds itself to `PATH`
automatically, but these pip packages don't. Alyssa now works around this
herself at startup (see `_add_nvidia_dll_dirs()` in `transcribe.py`), so a
fresh `pip install`/venv rebuild should be enough to fix it - delete the
`venv` folder next to `main.py` and rerun `start_alyssa.bat` to force a
clean reinstall if you were already on an older copy of this project.

Once running, the first "Loading Whisper model..." line in the console
prints whether it resolved to `cuda` or `cpu`. If a CUDA/cuDNN library
(e.g. `cublas64_12.dll`) turns out to be missing or broken - this can
happen even after a successful model load, since ctranslate2 sometimes
only actually touches those DLLs on the first real transcription - Alyssa
now catches that automatically, reloads fresh on CPU, and retries instead
of crashing the listening loop.

All of this - Whisper model size, device (auto-detect / force CPU / force
NVIDIA GPU), and compute type - can also be changed from the companion
GUI's **Settings → Engine** tab instead of editing `config.py` by hand.
Changes there save immediately but only take effect the next time Alyssa
starts (the speech model is loaded once at startup), so restart Alyssa
after changing them.

## Customizing

- **Name**: `config.py` → `ASSISTANT_NAME`. Changing this updates what it
  calls itself *and* the word it listens for in your speech, everywhere in
  the code — no other files need editing.
- **Voice**: `config.py` → `EDGE_TTS_VOICE`. Try `"en-US-AriaNeural"`
  (female) or `"en-US-GuyNeural"` (male) for alternatives, or open
  **Settings → Assistant → Voice & Behavior → "Browse all voices…"** for a
  searchable list of Microsoft's full catalog (400+ voices, ~140
  locales) instead of picking blind from `config.py`.
- **Custom/cloned voices (ElevenLabs)**: set `TTS_PROVIDER = "elevenlabs"`
  in `config.py` (or pick it from the **TTS provider** dropdown in
  Settings) to speak through an [ElevenLabs](https://elevenlabs.io)
  voice instead — including your own cloned voice, if you've made one.
  Needs `ELEVENLABS_API_KEY` and `ELEVENLABS_VOICE_ID`; Settings can
  fetch your account's voice list and fill the ID in for you once a key
  is entered. Costs money past ElevenLabs' free tier — Edge TTS
  (the default) stays free and unlimited.
- **Add new abilities**: write a function in `actions.py`, register it in
  `FUNCTIONS` at the bottom, and add a matching entry to `TOOLS` in
  `brain.py`.
- **Whisper accuracy vs speed**: `config.py` → `WHISPER_MODEL_SIZE`
  (`tiny.en` / `base.en` / `small.en` / `medium.en` / `large-v3` /
  `large-v3-turbo`). Defaults to `large-v3-turbo` for the best accuracy,
  which wants a GPU to feel snappy - see "Using a GPU for Whisper" below.
  Drop to `small.en` or `base.en` if you're CPU-only and it feels slow.
- **Model swap (local)**: any tool-calling Ollama model works — `ollama pull
  <name>` then update `OLLAMA_MODEL` in `config.py`. `qwen2.5:14b` is a
  strong balance of smart and reasonably fast on 64GB RAM;
  `qwen2.5:7b` trades some intelligence for noticeably faster replies.
- **Provider/model swap (cloud)**: change `LLM_PROVIDER` in `config.py` (or
  the dropdown in Settings → Assistant) to `"gemini"`, `"openai"`,
  `"anthropic"`, or `"custom_openai"`, then set that provider's model/API
  key fields — see "Choosing an LLM provider" near the top of this file.

## Known rough edges

- A local model is less capable than a large cloud model — it can
  misunderstand complex or ambiguous requests, and occasionally may still
  ask a clarifying question or fumble a tool call despite being told not
  to. A safety net strips out any stray JSON it accidentally writes as
  text so at least that never gets spoken aloud.
- It's always transcribing in the background to listen for its name —
  expect more CPU/mic usage than a wake-word setup, and occasional false
  triggers if "Alyssa" comes up in unrelated conversation.
- `open_app` only ever launches an app directly - it never types into
  Windows search. It resolves a spoken name to a real `.exe` via (in
  order) the Windows "App Paths" registry, a short hardcoded list of known
  common install locations (`_KNOWN_APP_PATHS`/`_VERSIONED_APP_PATHS` in
  `actions.py`, for apps like Spotify/Slack/Discord that don't register
  reliably), a `PATH` lookup, and finally a fuzzy scan of the Windows
  "Uninstall" registry (covers most other installed apps automatically,
  including winget-installed ones, without needing a hardcoded entry). If
  none of that finds a real install, Alyssa says "You don't have `<app>`
  installed." instead of guessing - add an entry to `_APP_EXE_ALIASES`/
  `_KNOWN_APP_PATHS` for anything that still doesn't resolve.
- Memory (`memory_db/`) is a local, unencrypted database — don't store
  anything sensitive in it.
- "Alyssa, what am I seeing?" (and similar) sends a screenshot to Google's
  Gemini API when `LLM_PROVIDER = "gemini"` — see "Seeing your screen"
  above if you'd rather that stay fully local.
