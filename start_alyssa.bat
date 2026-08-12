@echo off
REM Double-click this file to start Alyssa - a true "one click" launcher.
REM First run: creates a private virtual environment (.venv) next to this
REM file and installs everything in requirements.txt into it - no need to
REM run pip install yourself. Every later run reuses that same environment
REM and starts instantly, unless requirements.txt has changed since the
REM last install, in which case it re-installs automatically.
REM
REM Place this .bat file directly inside the "alyssa" folder, next to
REM main.py, before using it.

setlocal enabledelayedexpansion
cd /d "%~dp0"

REM --- Make sure Python is available at all ---
where python >nul 2>nul
if errorlevel 1 (
    echo ERROR: Python isn't installed, or isn't on your PATH.
    echo Install it from https://www.python.org/downloads/ - during setup,
    echo make sure to tick "Add python.exe to PATH" - then run this file again.
    echo.
    pause
    exit /b 1
)

REM --- Create the private environment on first run ---
if not exist ".venv\Scripts\python.exe" (
    echo Setting up Alyssa for the first time - this only happens once...
    python -m venv .venv
    if errorlevel 1 (
        echo ERROR: Failed to create the virtual environment.
        pause
        exit /b 1
    )

    REM Some Python installs - most commonly the Microsoft Store version of
    REM Python, which deliberately restricts/omits pip for sandboxing
    REM reasons - produce a venv that LOOKS fine (venv creation reports no
    REM error) but has no pip in it at all. Catch that right here with a
    REM clear, actionable message instead of letting it surface later as a
    REM confusing "No module named pip" in the middle of dependency install.
    ".venv\Scripts\python.exe" -m ensurepip --upgrade >nul 2>nul
    ".venv\Scripts\python.exe" -m pip --version >nul 2>nul
    if errorlevel 1 (
        echo ERROR: This Python install doesn't include pip, and Alyssa
        echo couldn't add it automatically ^(this happens most often with
        echo the Microsoft Store version of Python, which restricts pip
        echo for sandboxing reasons^).
        echo.
        echo Fix: uninstall that Python, then install it instead from
        echo https://www.python.org/downloads/ ^(NOT the Microsoft Store
        echo listing^) - make sure "pip" stays checked during setup. Then
        echo delete the ".venv" folder next to this file and run this
        echo script again.
        echo.
        pause
        exit /b 1
    )
)

REM --- Decide whether GPU (CUDA) packages are needed, based on config.py's
REM     WHISPER_DEVICE - skips several GB of NVIDIA libraries entirely on a
REM     "cpu" setup, since faster-whisper never touches them in that mode.
set "REQ_FILES=requirements.txt"
for /f "usebackq delims=" %%D in (`".venv\Scripts\python.exe" -c "import config; print(getattr(config, 'WHISPER_DEVICE', 'cpu').strip().lower())" 2^>nul`) do set "WHISPER_DEVICE_VALUE=%%D"
if /i not "!WHISPER_DEVICE_VALUE!"=="cpu" if exist "requirements-gpu.txt" set "REQ_FILES=requirements.txt requirements-gpu.txt"

REM --- (Re-)install dependencies only when needed, not on every launch ---
REM A stamp file records the hash of the requirements file(s) - and the
REM WHISPER_DEVICE mode - we last installed successfully from; if it
REM matches the current setup, dependencies are already installed and we
REM skip pip entirely (uses Python itself to compute the hash - more
REM reliable across machines than parsing certutil output in batch, which
REM was occasionally causing every-launch reinstalls). Folding
REM WHISPER_DEVICE_VALUE into the hash means flipping config.py from
REM "cpu" to "auto"/"cuda" (or back) is picked up as a real change, since
REM which files get installed depends on it, not just their contents.
set "STAMP_FILE=.venv\requirements.stamp"
set "NEED_INSTALL=0"

".venv\Scripts\python.exe" -c "import hashlib,os,sys; files=r'%REQ_FILES%'.split(); data=b''.join(open(f,'rb').read() for f in files) + b'|%WHISPER_DEVICE_VALUE%'; cur=hashlib.sha256(data).hexdigest(); stamp=r'%STAMP_FILE%'; old=open(stamp,'r',encoding='utf-8').read().strip() if os.path.exists(stamp) else ''; sys.exit(0 if cur==old else 1)"
if errorlevel 1 set "NEED_INSTALL=1"

if "!NEED_INSTALL!"=="1" (
    echo Installing/updating dependencies - this can take a few minutes...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
    for %%F in (!REQ_FILES!) do (
        ".venv\Scripts\python.exe" -m pip install -r %%F
        if errorlevel 1 (
            echo ERROR: Dependency install failed - see the messages above.
            pause
            exit /b 1
        )
    )
    ".venv\Scripts\python.exe" -c "import hashlib; files=r'%REQ_FILES%'.split(); data=b''.join(open(f,'rb').read() for f in files) + b'|%WHISPER_DEVICE_VALUE%'; open(r'%STAMP_FILE%','w',encoding='utf-8').write(hashlib.sha256(data).hexdigest())"
) else (
    echo Dependencies already installed - skipping straight to launch.
)

".venv\Scripts\python.exe" main.py
set "ALYSSA_EXIT=%ERRORLEVEL%"

REM Pause on crash so the user can inspect the error traceback
if not "!ALYSSA_EXIT!"=="0" (
    echo.
    echo ERROR: Alyssa crashed or closed with error code !ALYSSA_EXIT!.
    echo.
    pause
)

exit /b !ALYSSA_EXIT!