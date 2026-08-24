# AGENTS.md

## Cursor Cloud specific instructions

Alyssa is a single-process Python voice assistant (see `README.md`). It is
**primarily a Windows desktop app**; on this headless Linux VM you can run the
full test suite, the real dialogue/brain pipeline, and the PySide6 companion
GUI (via typed input). A faithful *voice* + *desktop-automation* E2E flow is
not possible here (no microphone, no Windows APIs).

### Environment layout
- Python dev environment lives in `.venv/` (gitignored). Dependencies come
  from `requirements-dev.txt` (which pulls in `requirements.txt`). The startup
  update script recreates/refreshes this automatically.
- The following system libraries are already provisioned in the VM snapshot
  (needed by `sounddevice` and `PySide6`, and to run a local LLM): PortAudio
  (`libportaudio2`), the Qt xcb stack (`libegl1`, `libxkbcommon-x11-0`,
  `libxcb-cursor0`, and related `libxcb-*`), `zstd`, and Ollama. Do not add
  these to the update script.

### Running the tests
- Run pytest with the offscreen Qt platform, or the GUI tests fail to import:
  `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest`
- **3 tests are expected to fail on Linux** and are not environment problems:
  - `test_runtime_fixes.py::FinalAuditFixTests::test_failed_workstation_lock_reports_failure` — uses Windows-only `ctypes.windll`.
  - `test_runtime_fixes.py::FinalAuditFixTests::test_user_environment_paths_return_expanded` — tests Windows `%VAR%\path` expansion.
  - `test_startup_contract.py::test_startup_launch_has_console_working_directory_and_delay` — asserts `HIDE_CONSOLE_WINDOW = False`, but the `bug-fixes` branch sets it to `True` in `config.py` (a branch code-state difference, not env).
- Everything else passes (171 passed at time of setup).

### Running the app (GUI) end to end
- The app needs an LLM provider. No cloud API keys are configured here, so use
  the local **Ollama** provider (installed, no secrets needed):
  - Start the server (systemd is not running in the VM, so launch it yourself):
    `ollama serve` (leave running; API on `http://localhost:11434`).
  - The default model `qwen2.5:3b` is pulled; re-pull with `ollama pull qwen2.5:3b` if missing.
- `config.py` defaults `LLM_PROVIDER = 'gemini'` (needs a key). To run without a
  key, select `ollama` — either via the in-app Settings window, or at runtime
  before launch (`import config; config.LLM_PROVIDER = 'ollama'`). Do not commit
  a change to `config.py` for this.
- Launch on the VM desktop with `DISPLAY=:1`.
- **Non-obvious GUI gotcha:** `opencv-python` (a dependency) bundles its own
  broken Qt `xcb` plugin and unconditionally overwrites
  `QT_QPA_PLATFORM_PLUGIN_PATH` on import, which makes the GUI die with
  "Could not load the Qt platform plugin xcb". To run the GUI, import `cv2`
  first, then reset `QT_QPA_PLATFORM_PLUGIN_PATH` to PySide6's own platforms
  dir (`.venv/lib/python3.12/site-packages/PySide6/Qt/plugins/platforms`)
  before any `QApplication` is created. (Pytest avoids this by using
  `QT_QPA_PLATFORM=offscreen`, whose plugin cv2 does not ship.)
- **No audio devices:** the listening loop's `recorder.record_command()` raises
  `PortAudioError: Error querying device -1` every pass; this is caught by the
  loop and is harmless. Interact by typing into the companion's
  "Type to Alyssa..." chat bar (typed input bypasses the mic and name-gating).
  Set `SPEAK_RESPONSES = False` to skip TTS playback attempts.

### Core entry point
- Both the voice loop and the GUI route every command through
  `brain.handle_command(user_text)` (provider routing → LLM → tool dispatch).
  This is the function to exercise for a headless end-to-end check.

### Local state (gitignored)
- `memory.json`, `reminders.json`, `token.json`, `overlay_config.json`,
  `logs/`, and `.venv/` are gitignored working state — do not commit them.
