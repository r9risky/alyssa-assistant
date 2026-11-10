@echo off
setlocal
cd /d "%~dp0\.."

echo ===================================================
echo   Alyssa Assistant - Windows .exe Builder
echo ===================================================
echo.

rem Check for Python
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not found on PATH.
    echo Please install Python 3.10+ from https://python.org
    pause
    exit /b 1
)

rem Prefer virtual environment if it exists
if exist ".venv\Scripts\python.exe" (
    set "PYTHON_EXE=.venv\Scripts\python.exe"
) else (
    set "PYTHON_EXE=python"
)

echo Using Python: %PYTHON_EXE%
echo Installing dependencies and PyInstaller...
%PYTHON_EXE% -m pip install --upgrade pip
%PYTHON_EXE% -m pip install -r requirements.txt
%PYTHON_EXE% -m pip install pyinstaller

echo.
echo Building Alyssa.exe...
%PYTHON_EXE% scripts\build_exe.py

if errorlevel 1 (
    echo.
    echo [ERROR] Build failed. Check output above for details.
    pause
    exit /b 1
)

echo.
echo ===================================================
echo   Build complete! Output in: dist\Alyssa\
echo ===================================================
pause
