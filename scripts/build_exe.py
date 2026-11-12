"""Automated build script for packaging Alyssa into a standalone Windows .exe.

Usage:
    python scripts/build_exe.py
"""

import hashlib
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
        print("[build] PyInstaller not found. Installing...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])
        except Exception:
            subprocess.check_call(["uv", "pip", "install", "pyinstaller"])


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


# Optional files that are only loaded on demand. Without them the release unpacks
# to under 500 MB, the most that the updater in v1.9.0 and older will install.
_TRIM_GLOBS = (
    "_internal/PySide6/opengl32sw.dll",  # software OpenGL fallback; her windows draw without OpenGL
    "_internal/PySide6/translations/*",  # Qt's own dialog translations; her UI is English
    "_internal/cv2/opencv_videoio_ffmpeg*.dll",  # video files and streams; webcams use Windows' own backend
    "_internal/PIL/_avif*",  # AVIF images; Pillow skips the format when it's missing
    "_internal/espeakng_loader/espeak-ng-data/ru_dict",  # Kokoro has no Russian voice
)
OLD_UPDATER_LIMIT_BYTES = 500 * 1024 * 1024


def _trim_bundle():
    removed = 0
    for pattern in _TRIM_GLOBS:
        for path in OUTPUT_FOLDER.glob(pattern):
            if path.is_file():
                removed += path.stat().st_size
                path.unlink()
    total = sum(f.stat().st_size for f in OUTPUT_FOLDER.rglob("*") if f.is_file())
    print(f"[build] Trimmed {removed / 1e6:.0f} MB; bundle is {total / 1e6:.0f} MB")
    if total > OLD_UPDATER_LIMIT_BYTES:
        print(
            "[build] WARNING: over 500 MB unpacked - Alyssa v1.9.0 and older can't "
            "update to this release in-app and need Alyssa-Setup.exe instead."
        )


def post_process_bundle():
    print("[build] Post-processing output distribution...")
    if not OUTPUT_FOLDER.is_dir():
        raise RuntimeError(f"Expected output folder not found: {OUTPUT_FOLDER}")

    # Ensure Alyssa.exe exists in bundle across all build platforms
    exe_path = OUTPUT_FOLDER / "Alyssa.exe"
    elf_path = OUTPUT_FOLDER / "Alyssa"
    if elf_path.is_file() and not exe_path.is_file():
        shutil.copy2(elf_path, exe_path)

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

    # Copy core application files into the distribution bundle so that:
    # 1. Existing older Alyssa installations (v1.5.0/v1.5.1/v1.5.2) can extract and
    #    verify the release (their updater specifically checks for main.py & overlay)
    # 2. Source-based environments can also update from this same release package
    source_files = [
        "main.py",
        "updater.py",
        "memory.py",
        "nameutil.py",
        "credential_store.py",
        "user_settings.py",
        "plugin_loader.py",
        "recorder.py",
        "transcribe.py",
        "voice.py",
        "voice_dsp.py",
        "voice_playback.py",
        "voice_synthesis.py",
        "wake_word.py",
        "call_ears.py",
        "hotkeys.py",
        "startup_logging.py",
        "telemetry.py",
        "color_themes.json",
        "requirements.txt",
    ]
    for filename in source_files:
        src = ROOT_DIR / filename
        dst = OUTPUT_FOLDER / filename
        if src.is_file() and not dst.is_file():
            shutil.copy2(src, dst)

    for dirname in ("overlay", "brain", "actions", "scripts"):
        src = ROOT_DIR / dirname
        dst = OUTPUT_FOLDER / dirname
        if src.is_dir() and not dst.is_dir():
            shutil.copytree(src, dst)

    _trim_bundle()

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

    # Generate published SHA-256 checksums file
    sha256_hash = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    checksum_path = DIST_DIR / "checksums.txt"
    checksum_path.write_text(f"{sha256_hash} *{zip_path.name}\n", encoding="utf-8")
    print(f"[build] Generated checksum: {checksum_path.name} ({sha256_hash})")

    target_exe = exe_path if exe_path.is_file() else elf_path
    print(f"\n[build] SUCCESS! Executable built at: {target_exe}")
    print(f"[build] Packaged zip archive: {zip_path}\n")


def main():
    os.chdir(ROOT_DIR)
    check_and_install_pyinstaller()
    run_pyinstaller_build()
    post_process_bundle()


if __name__ == "__main__":
    main()
