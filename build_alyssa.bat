@echo off
REM Builds a standalone Alyssa.exe using PyInstaller. Run this ONCE on
REM Windows (not on the machine that will necessarily run the exe - the exe
REM only works on the same OS/architecture it was built on, i.e. build on
REM Windows to run on Windows).
REM
REM Prerequisites: run start_alyssa.bat at least once first, so the .venv
REM folder and all dependencies already exist - this script builds from
REM that same environment plus PyInstaller.

setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo ERROR: .venv doesn't exist yet. Run start_alyssa.bat first so
    echo dependencies are installed, then run this script.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -m pip install pyinstaller

REM --onefile        : one single .exe instead of a folder of files
REM --console        : keep the console window (Alyssa prints debug info,
REM                    and run_command/delete_file confirmations need it)
REM --name Alyssa    : output file name -> dist\Alyssa.exe
REM --collect-all X  : bundle a package's non-Python data files too, not
REM                    just its .py code - faster-whisper/ctranslate2 and
REM                    edge-tts both need this or they fail at runtime with
REM                    missing-file errors that don't show up until you
REM                    actually try to use them.
".venv\Scripts\python.exe" -m PyInstaller ^
    --onefile ^
    --console ^
    --name Alyssa ^
    --collect-all faster_whisper ^
    --collect-all ctranslate2 ^
    --collect-all edge_tts ^
    --collect-all PySide6 ^
    --hidden-import webrtcvad ^
    --hidden-import pyperclip ^
    --hidden-import send2trash ^
    --exclude-module config ^
    main.py

echo.
echo If this succeeded, your standalone exe is at: dist\Alyssa.exe
echo Copy config.py, memory.json (if it exists), and dist\Alyssa.exe
echo together into one folder - config.py must stay a normal .py file
echo next to the exe, it is NOT bundled inside it, so you can still edit
echo settings without rebuilding.
echo.
pause
