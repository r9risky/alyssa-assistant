@echo off
REM Removes the "AlyssaAssistant" scheduled task created by
REM install_startup.bat, so Alyssa stops starting automatically at login.

setlocal
cd /d "%~dp0"
set "TASK_NAME=AlyssaAssistant"

REM --- Self-elevate if this window isn't already running as admin ---
net session >nul 2>&1
if not "%errorlevel%"=="0" (
    echo Requesting administrator privileges...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -WorkingDirectory '%~dp0' -Verb RunAs"
    exit /b
)

schtasks /Query /TN "%TASK_NAME%" >nul 2>&1
if errorlevel 1 (
    echo "%TASK_NAME%" isn't registered - nothing to remove.
    pause
    exit /b 0
)

schtasks /Delete /TN "%TASK_NAME%" /F >nul
if errorlevel 1 (
    echo ERROR: Failed to remove the scheduled task.
    pause
    exit /b 1
)

echo Done - Alyssa will no longer start automatically at login.
pause
