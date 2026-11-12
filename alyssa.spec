# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification file for Alyssa Assistant.

Builds a standalone Windows executable with bundled PySide6 GUI, audio engines,
VAD models, assets, and dynamic plugin loading support.
"""

import os
import sys

block_cipher = None

BASE_DIR = os.path.abspath(SPECPATH)

datas = [
    (os.path.join(BASE_DIR, "assets"), "assets"),
    (os.path.join(BASE_DIR, "color_themes.json"), "."),
    (os.path.join(BASE_DIR, "plugins"), "plugins"),
]

hiddenimports = [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtSvg",
    "sounddevice",
    "numpy",
    "PIL",
    "PIL.Image",
    "PIL.ImageDraw",
    "requests",
    "urllib3",
    "psutil",
    "pygame",
    "onnxruntime",
    "cffi",
    "actions",
    "actions.apps_and_files",
    "actions.asides",
    "actions.clipboard_and_screen",
    "actions.clips",
    "actions.confirmation",
    "actions.desktop_chaos",
    "actions.game_sense",
    "actions.system",
    "brain",
    "brain.dialogue",
    "brain.speculative",
    "brain.tool_catalog",
    "call_ears",
    "credential_store",
    "hotkeys",
    "memory",
    "nameutil",
    "plugin_loader",
    "recorder",
    "startup_logging",
    "telemetry",
    "transcribe",
    "updater",
    "voice",
    "voice_dsp",
    "voice_playback",
    "voice_synthesis",
    "wake_word",
    "audio",
    "audio.recorder",
    "audio.transcribe",
    "audio.playback",
    "audio.synthesis",
    "audio.dsp",
    "audio.wake_word",
    "audio.call_ears",
    "core",
    "core.credential_store",
    "core.startup_logging",
    "core.telemetry",
    "core.nameutil",
    "core.hotkeys",
    "core.memory",
]

# Kokoro offline voice: its config.json and espeak-ng's DLL + phoneme data.
# Discord bot: discord.py's bundled libopus DLL, davey (voice encryption) and PyNaCl.
from PyInstaller.utils.hooks import collect_all

binaries = []
# faster_whisper: its Silero VAD model (assets/silero_vad_v6.onnx); without it every
# local transcription failed with NO_SUCHFILE.
for _package in ("faster_whisper", "kokoro_onnx", "espeakng_loader", "phonemizer", "discord", "davey", "nacl"):
    try:
        _datas, _binaries, _hidden = collect_all(_package)
    except Exception:
        continue  # optional voice package not installed in this build env
    datas += _datas
    binaries += _binaries
    hiddenimports += _hidden

excludes = [
    "tkinter",
    "matplotlib",
    "scipy",
    "IPython",
    "unittest",
]

a = Analysis(
    [os.path.join(BASE_DIR, "main.py")],
    pathex=[BASE_DIR],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# Standalone folder distribution (fast startup, easily editable config & plugins)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Alyssa",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # No black terminal window; runs as pure desktop companion
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(BASE_DIR, "assets", "nottalk.png") if os.path.exists(os.path.join(BASE_DIR, "assets", "nottalk.png")) else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Alyssa",
)
