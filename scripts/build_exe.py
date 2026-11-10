"""Automated build script for packaging Alyssa into a standalone Windows .exe.

Usage:
    python scripts/build_exe.py
"""

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
DIST_DIR = ROOT_DIR / "dist"
BUILD_DIR = ROOT_DIR / "build"
OUTPUT_FOLDER = DIST_DIR / "Alyssa"


def check_and_install_pyinstaller():
    try:
        import PyInstaller
        print(f"[build] PyInstaller {PyInstaller.__version__} detected.")
    except ImportError:
        print("[build] PyInstaller not found. Installing via pip...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])


def run_pyinstaller_build():
    spec_path = ROOT_DIR / "alyssa.spec"
    if not spec_path.is_file():
        raise FileNotFoundError(f"Missing specification file: {spec_path}")

    print(f"[build] Running PyInstaller on {spec_path.name}...")
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        str(spec_path),
    ]
    subprocess.check_call(cmd, cwd=str(ROOT_DIR))


def post_process_bundle():
    print("[build] Post-processing output distribution...")
    if not OUTPUT_FOLDER.is_dir():
        raise RuntimeError(f"Expected output folder not found: {OUTPUT_FOLDER}")

    # Copy config.py next to the executable so user can edit settings without rebuild
    shutil.copy2(ROOT_DIR / "config.py", OUTPUT_FOLDER / "config.py")

    # Copy external plugins folder if not already populated
    plugins_target = OUTPUT_FOLDER / "plugins"
    if not plugins_target.is_dir():
        shutil.copytree(ROOT_DIR / "plugins", plugins_target)

    # Copy assets folder next to executable
    assets_target = OUTPUT_FOLDER / "assets"
    if not assets_target.is_dir():
        shutil.copytree(ROOT_DIR / "assets", assets_target)

    # Create a quick-start README for end-users
    readme_path = OUTPUT_FOLDER / "HOW_TO_RUN.txt"
    readme_path.write_text(
        "Alyssa Assistant - Windows Release\n"
        "===================================\n\n"
        "1. Double-click 'Alyssa.exe' to launch the assistant.\n"
        "2. The assistant runs silently in your system tray and floating companion window.\n"
        "3. You can edit 'config.py' directly with Notepad to change default hotkeys or settings.\n"
        "4. You can drop custom Python scripts into the 'plugins/' folder anytime!\n"
    )

    # Create zip archive
    zip_path = DIST_DIR / "Alyssa-Windows-x64.zip"
    print(f"[build] Packaging {zip_path.name}...")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in OUTPUT_FOLDER.rglob("*"):
            if file.is_file():
                zf.write(file, arcname=file.relative_to(DIST_DIR))

    print(f"\n[build] SUCCESS! Executable built at: {OUTPUT_FOLDER / 'Alyssa.exe'}")
    print(f"[build] Packaged zip archive: {zip_path}\n")


def main():
    os.chdir(ROOT_DIR)
    check_and_install_pyinstaller()
    run_pyinstaller_build()
    post_process_bundle()


if __name__ == "__main__":
    main()
