@echo off
REM Makes Alyssa start automatically, with admin rights, every time you
REM log in to Windows - via a Task Scheduler task (not the Startup folder,
REM since a Startup-folder shortcut can't auto-elevate: it either skips
REM admin rights entirely or throws a UAC prompt on every single login).
REM A Task Scheduler task set to "run with highest privileges" gets the
REM trust decision made once, right now, and then starts silently
REM elevated at every future login - no repeated prompts.
REM
REM Run this by double-clicking it. It will ask for admin rights itself
REM (a UAC prompt) - that's expected and required to register the task.
REM
REM Prerequisite: run start_alyssa.bat (or build_alyssa.bat) at least once
REM first, so dependencies - or the standalone exe - already exist.

setlocal enabledelayedexpansion
cd /d "%~dp0"
set "SCRIPT_DIR=%~dp0"
set "TASK_NAME=AlyssaAssistant"

REM --- Self-elevate if this window isn't already running as admin ---
net session >nul 2>&1
if not "%errorlevel%"=="0" (
    echo Requesting administrator privileges...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -WorkingDirectory '%SCRIPT_DIR%' -Verb RunAs"
    exit /b
)

REM --- Decide what the task should actually launch ---
REM Prefer the standalone exe from build_alyssa.bat (cleanest - no console
REM window, no venv path to break if Python changes). Fall back to the
REM venv's pythonw.exe (windowless) running main.py directly.
if exist "%SCRIPT_DIR%dist\Alyssa.exe" (
    set "ACTION_EXE=%SCRIPT_DIR%dist\Alyssa.exe"
    set "ACTION_ARGS="
) else if exist "%SCRIPT_DIR%.venv\Scripts\pythonw.exe" (
    set "ACTION_EXE=%SCRIPT_DIR%.venv\Scripts\pythonw.exe"
    set "ACTION_ARGS=main.py"
) else (
    echo ERROR: Alyssa hasn't been set up yet.
    echo Run start_alyssa.bat once first ^(to install dependencies^), or
    echo build_alyssa.bat if you want the standalone exe - then run this
    echo script again.
    echo.
    pause
    exit /b 1
)

echo Registering "%TASK_NAME%" to start Alyssa at login, elevated...

REM One PowerShell block does the whole registration - New-ScheduledTask*
REM lets us set the working directory directly (schtasks.exe's /Create
REM has no equivalent flag), which matters here since Alyssa looks for
REM config.py/plugins/assets relative to its working directory.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$ErrorActionPreference = 'Stop';" ^
    "$action = New-ScheduledTaskAction -Execute '%ACTION_EXE%' -Argument '%ACTION_ARGS%' -WorkingDirectory '%SCRIPT_DIR%';" ^
    "$trigger = New-ScheduledTaskTrigger -AtLogOn -User \"$env:USERDOMAIN\$env:USERNAME\";" ^
    "$principal = New-ScheduledTaskPrincipal -UserId \"$env:USERDOMAIN\$env:USERNAME\" -LogonType Interactive -RunLevel Highest;" ^
    "$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable;" ^
    "Register-ScheduledTask -TaskName '%TASK_NAME%' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null"

if errorlevel 1 (
    echo.
    echo ERROR: Failed to register the scheduled task - see the message above.
    pause
    exit /b 1
)

echo.
echo Done. Alyssa will now start automatically, with admin rights, the
echo next time you log in to Windows.
echo.
echo To test it right now without logging out: open Task Scheduler,
echo find "%TASK_NAME%", right-click it, and choose Run.
echo To undo this later, run uninstall_startup.bat.
echo.
pause
