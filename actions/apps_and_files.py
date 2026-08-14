import ctypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from ctypes import wintypes
from functools import lru_cache

import send2trash

import config

try:
    import winreg
except ImportError:
    winreg = None

from . import confirmation
from .confirmation import _confirm

if os.name == "nt":
    _user32 = ctypes.windll.user32
    _shell32 = ctypes.windll.shell32
else:
    _user32 = None
    _shell32 = None

if _user32 is not None:
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _user32.PostMessageW.restype = wintypes.BOOL
    _user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.ShowWindow.restype = wintypes.BOOL

if _shell32 is not None:
    _shell32.ShellExecuteW.argtypes = [
        wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR,
        wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int,
    ]
    _shell32.ShellExecuteW.restype = wintypes.HINSTANCE

_user32 = ctypes.windll.user32 if os.name == "nt" else None


_shell32 = ctypes.windll.shell32 if os.name == "nt" else None


_WM_CLOSE = 0x0010


_SC_MINIMIZE = 0xF020


_SC_MAXIMIZE = 0xF030


_WM_SYSCOMMAND = 0x0112


_SW_SHOWNOACTIVATE = 4  # show the window but don't activate/foreground it


_restart_requested = threading.Event()


def restart_alyssa() -> str:
    """Requests an app restart after Alyssa finishes her current reply."""
    _restart_requested.set()
    return "Restarting Alyssa."


def consume_restart_request() -> bool:
    requested = _restart_requested.is_set()
    _restart_requested.clear()
    return requested


def relaunch_alyssa():
    """Replaces this process with a fresh Alyssa instance."""
    args = [sys.executable] + (sys.argv[1:] if getattr(sys, "frozen", False) else sys.argv)
    os.execv(sys.executable, args)


def _launch_without_stealing_focus(path_or_url: str) -> bool:
    """Opens a file/app/URL via ShellExecuteW with SW_SHOWNOACTIVATE, so it
    appears without stealing focus from whatever's on screen. Returns True
    on success so callers can fall back to os.startfile()/webbrowser.open().

    Caveat: some apps (Electron apps, some browsers on first launch) call
    SetForegroundWindow() on themselves regardless. Also, since focus isn't
    guaranteed, a same-breath follow-up ("open Discord and type hello")
    won't land in it - ask her to switch to it first."""
    if _shell32 is None:
        return False
    try:
        result = _shell32.ShellExecuteW(None, "open", path_or_url, None, None, _SW_SHOWNOACTIVATE)
    except Exception as e:
        print(f"[background-launch] ShellExecuteW failed for {path_or_url!r}: {e}")
        return False
    # ShellExecuteW's restype is a pointer type (HINSTANCE), and ctypes
    # auto-converts a returned 0/NULL - one of ShellExecute's own documented
    # failure codes - into Python None, which int() can't take directly.
    value = 0 if result is None else int(result)
    if value <= 32:  # ShellExecute's own success convention: >32 means it worked
        print(f"[background-launch] ShellExecuteW returned failure code {value} for {path_or_url!r}")
        return False
    return True


def _post_syscommand_to_foreground(syscommand: int) -> bool:
    """Posts a WM_SYSCOMMAND (minimize/maximize) straight to the current
    foreground window instead of simulating the Win+Down/Win+Up hotkey.
    Returns True on success so callers can fall back to the hotkey."""
    if _user32 is None:
        return False
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return False
    return bool(_user32.PostMessageW(hwnd, _WM_SYSCOMMAND, syscommand, 0))


def _close_foreground_window() -> bool:
    """Posts WM_CLOSE straight to the current foreground window instead of
    simulating Alt+F4. Returns True on success so the caller can fall back
    to the hotkey."""
    if _user32 is None:
        return False
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return False
    return bool(_user32.PostMessageW(hwnd, _WM_CLOSE, 0, 0))


_APP_EXE_ALIASES = {
    "chrome": "chrome.exe", "google chrome": "chrome.exe",
    "edge": "msedge.exe", "microsoft edge": "msedge.exe",
    "firefox": "firefox.exe",
    "notepad": "notepad.exe",
    "spotify": "spotify.exe",
    "explorer": "explorer.exe", "windows explorer": "explorer.exe",
    "file explorer": "explorer.exe",
    "vscode": "code.exe", "vs code": "code.exe", "visual studio code": "code.exe",
    "word": "winword.exe", "excel": "excel.exe", "powerpoint": "powerpnt.exe",
    "calculator": "calc.exe",
    # "discord" -> "discord.exe" already falls out of the default rule
    # below; only the PTB/Canary variants need an entry.
    "discord ptb": "discordptb.exe", "discordptb": "discordptb.exe",
    "discord canary": "discordcanary.exe", "discordcanary": "discordcanary.exe",
    "epic games": "epicgameslauncher.exe", "epic games launcher": "epicgameslauncher.exe",
    "obs": "obs64.exe", "obs studio": "obs64.exe",
    # No official YouTube Music desktop app - points at the most common
    # unofficial client (th-ch/youtube-music). Otherwise play_music() falls
    # back to the browser for YouTube Music.
    "youtube music": "youtube music.exe", "ytmusic": "youtube music.exe",
    "ytm": "youtube music.exe",
    # Extra common apps whose real .exe name doesn't match "name + .exe".
    "irfanview": "i_view64.exe", "irfanview64": "i_view64.exe",
    "revo uninstaller": "revounin.exe",
    "7-zip": "7zfm.exe", "7zip": "7zfm.exe",
    "winrar": "winrar.exe", "winrar archiver": "winrar.exe",
    "paint.net": "paintdotnet.exe",
    "malwarebytes": "mbam.exe",
    "zoom workplace": "zoom.exe",
    "eclipse ide for java": "eclipse.exe", "eclipse": "eclipse.exe",
    "gog galaxy": "galaxyclient.exe",
    "ea app": "eadesktop.exe",
    "ubisoft connect": "upc.exe",
    "microsoft powertoys": "powertoys.exe", "powertoys": "powertoys.exe",
    "microsoft visual studio code": "code.exe",
}


_KNOWN_APP_PATHS = {
    "chrome.exe": [
        r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
        r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
        r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
    ],
    "msedge.exe": [
        r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
        r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    ],
    "firefox.exe": [
        r"%ProgramFiles%\Mozilla Firefox\firefox.exe",
        r"%ProgramFiles(x86)%\Mozilla Firefox\firefox.exe",
    ],
    "spotify.exe": [
        r"%AppData%\Spotify\Spotify.exe",  # the normal per-user installer location
        r"%ProgramFiles%\Spotify\Spotify.exe",  # older/enterprise per-machine installs
        r"%ProgramFiles(x86)%\Spotify\Spotify.exe",
    ],
    "slack.exe": [
        r"%LocalAppData%\slack\slack.exe",
    ],
    "steam.exe": [
        r"%ProgramFiles(x86)%\Steam\steam.exe",
        r"%ProgramFiles%\Steam\steam.exe",
    ],
    "telegram.exe": [
        r"%AppData%\Telegram Desktop\Telegram.exe",
    ],
    "whatsapp.exe": [
        r"%LocalAppData%\WhatsApp\WhatsApp.exe",
    ],
    "zoom.exe": [
        r"%AppData%\Zoom\bin\Zoom.exe",
    ],
    "signal.exe": [
        r"%LocalAppData%\Programs\signal-desktop\Signal.exe",
    ],
    "epicgameslauncher.exe": [
        r"%ProgramFiles(x86)%\Epic Games\Launcher\Portal\Binaries\Win64\EpicGamesLauncher.exe",
    ],
    "obs64.exe": [
        r"%ProgramFiles%\obs-studio\bin\64bit\obs64.exe",
    ],
    "youtube music.exe": [
        r"%LocalAppData%\Programs\YouTube Music\YouTube Music.exe",
    ],
    "notepad++.exe": [
        r"%ProgramFiles%\Notepad++\notepad++.exe",
        r"%ProgramFiles(x86)%\Notepad++\notepad++.exe",
    ],
    "i_view64.exe": [
        r"%ProgramFiles%\IrfanView\i_view64.exe",
    ],
    "revounin.exe": [
        r"%ProgramFiles(x86)%\VS Revo Group\Revo Uninstaller\RevoUnin.exe",
        r"%ProgramFiles%\VS Revo Group\Revo Uninstaller\RevoUnin.exe",
    ],
    "powertoys.exe": [
        r"%LocalAppData%\PowerToys\PowerToys.exe",
        r"%ProgramFiles%\PowerToys\PowerToys.exe",
    ],
}


_VERSIONED_APP_PATHS = {
    "discord.exe": (r"%LocalAppData%\Discord", "Discord.exe"),
    "discordptb.exe": (r"%LocalAppData%\DiscordPTB", "DiscordPTB.exe"),
    "discordcanary.exe": (r"%LocalAppData%\DiscordCanary", "DiscordCanary.exe"),
}


@lru_cache(maxsize=128)
def _resolve_versioned_app_path(base_dir_template: str, exe_name: str):
    """Finds the highest-numbered 'app-X.Y.Z' subfolder under a base
    install directory and returns the path to exe_name inside it, or None
    if the base folder doesn't exist or has no matching subfolder yet."""
    base_dir = os.path.expandvars(base_dir_template)
    if not os.path.isdir(base_dir):
        return None

    best_version, best_path = None, None
    for entry in os.listdir(base_dir):
        if not entry.startswith("app-"):
            continue
        exe_path = os.path.join(base_dir, entry, exe_name)
        if not os.path.exists(exe_path):
            continue
        version = tuple(
            int(part) if part.isdigit() else 0
            for part in entry[len("app-"):].split(".")
        )
        if best_version is None or version > best_version:
            best_version, best_path = version, exe_path

    return best_path


_UNINSTALL_KEY_ROOTS = [
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall") if winreg else None,
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall") if winreg else None,
    (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall") if winreg else None,
]


def _search_uninstall_registry(app_name: str):
    """Fuzzy-matches app_name against installed programs' DisplayName in
    the Windows Uninstall registry, then looks for a matching .exe in that
    program's InstallLocation (or its DisplayIcon's folder). Returns a
    resolved .exe path, or None if nothing matched well enough."""
    if winreg is None:
        return None

    name_words = [w for w in re.split(r"[^a-z0-9]+", app_name.lower()) if w]
    if not name_words:
        return None

    best_score, best_path = 0, None

    for root in _UNINSTALL_KEY_ROOTS:
        if root is None:
            continue
        hive, subkey_path = root
        try:
            root_key = winreg.OpenKey(hive, subkey_path)
        except OSError:
            continue

        for i in range(winreg.QueryInfoKey(root_key)[0]):
            try:
                sub_name = winreg.EnumKey(root_key, i)
                sub_key = winreg.OpenKey(root_key, sub_name)
                display_name, _ = winreg.QueryValueEx(sub_key, "DisplayName")
            except OSError:
                continue

            display_words = set(re.split(r"[^a-z0-9]+", display_name.lower()))
            score = sum(1 for w in name_words if w in display_words)
            if score == 0 or score < len(name_words):
                continue  # require every spoken word to match to avoid false positives

            install_dir = None
            try:
                loc, _ = winreg.QueryValueEx(sub_key, "InstallLocation")
                if loc and os.path.isdir(os.path.expandvars(loc)):
                    install_dir = os.path.expandvars(loc)
            except OSError:
                pass
            if install_dir is None:
                try:
                    icon, _ = winreg.QueryValueEx(sub_key, "DisplayIcon")
                    icon = icon.split(",")[0].strip('"')
                    if icon.lower().endswith(".exe") and os.path.exists(icon):
                        # DisplayIcon often points straight at the exe already
                        if score > best_score:
                            best_score, best_path = score, icon
                        continue
                    if icon:
                        install_dir = os.path.dirname(icon)
                except OSError:
                    pass
            if not install_dir or not os.path.isdir(install_dir):
                continue

            # Look for a plausible .exe in the install folder (and one
            # level deep), preferring one whose filename overlaps the
            # spoken name, skipping obvious uninstaller/helper exes.
            candidates = []
            for dirpath, _dirnames, filenames in os.walk(install_dir):
                depth = dirpath[len(install_dir):].count(os.sep)
                if depth > 1:
                    continue
                for fname in filenames:
                    if not fname.lower().endswith(".exe"):
                        continue
                    lname = fname.lower()
                    if "uninstall" in lname or "unins0" in lname or "setup" in lname:
                        continue
                    candidates.append(os.path.join(dirpath, fname))

            if not candidates:
                continue

            def _name_overlap(path):
                stem = os.path.splitext(os.path.basename(path))[0].lower()
                return sum(1 for w in name_words if w in stem)

            candidates.sort(key=_name_overlap, reverse=True)
            if score > best_score:
                best_score, best_path = score, candidates[0]

    return best_path


@lru_cache(maxsize=128)
def _resolve_app_path(app_name: str):
    """Tries to resolve a spoken app name straight to a real .exe path,
    using the same 'App Paths' registry Windows itself uses to resolve a
    bare name like 'chrome' typed into the Run box - so we can launch it
    directly instead of typing into Start-menu search and hoping the top
    result is right (search timing/indexing/best-match order is exactly
    what was making Chrome launches flaky). Falls back to a short list of
    known common install locations (including version-folder scanning for
    apps like Discord that update into a new folder each time), then a
    PATH lookup. Returns None if nothing is found, so callers can fall
    back to the old search-typing method for anything not covered here
    (e.g. Store apps, or an install location this doesn't know about)."""
    name = app_name.strip().lower()
    exe = _APP_EXE_ALIASES.get(name)
    if exe is None:
        exe = name if name.endswith(".exe") else name + ".exe"

    if winreg is not None:
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                key = winreg.OpenKey(
                    hive,
                    rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}",
                )
                path, _ = winreg.QueryValueEx(key, "")
                if path and os.path.exists(path):
                    return path
            except OSError:
                pass

    for candidate in _KNOWN_APP_PATHS.get(exe, []):
        expanded = os.path.expandvars(candidate)
        if os.path.exists(expanded):
            return expanded

    if exe in _VERSIONED_APP_PATHS:
        base_dir_template, exe_name = _VERSIONED_APP_PATHS[exe]
        resolved = _resolve_versioned_app_path(base_dir_template, exe_name)
        if resolved:
            return resolved

    found = shutil.which(exe)
    if found:
        return found

    # Last resort: fuzzy-match the spoken name against every installed
    # program's registered DisplayName - covers the long tail without
    # needing a hardcoded entry for each one.
    return _search_uninstall_registry(app_name)


def open_app(app_name: str) -> str:
    """Opens an application by name via direct launch only - the Windows
    App Paths registry, a short list of known common install locations, and
    a generic scan of the Windows Uninstall registry for anything else
    installed on this PC. Never falls back to typing into Windows search:
    if no real install of the app can be found, this reports that instead
    of guessing at a search result."""
    if not app_name or not str(app_name).strip():
        return "I need an app name before I can open anything."

    if not _confirm(f"open '{app_name}'"):
        return "Cancelled by user."

    settle = max(0.0, float(getattr(config, "APP_LAUNCH_SETTLE_SECONDS", 1.5)))

    resolved = _resolve_app_path(app_name)
    if resolved:
        try:
            launched_quietly = False
            if getattr(config, "LAUNCH_APPS_IN_BACKGROUND", True):
                launched_quietly = _launch_without_stealing_focus(resolved)
            if not launched_quietly:
                os.startfile(resolved)  # steals focus, but always works - the fallback
            print(f"[open_app] launched {'quietly' if launched_quietly else 'directly'}: {resolved}")
            # Give the window a moment to appear before returning, so a
            # same-command follow-up (e.g. type_text right after opening
            # Discord) doesn't fire too early. Note: a quiet launch doesn't
            # take focus, so that follow-up would land wherever focus
            # already was - ask her to switch to it first if needed.
            time.sleep(settle)
            return f"Opened {app_name}."
        except OSError as e:
            print(f"[open_app] direct launch of {resolved!r} failed: {e}")
            return f"Found {app_name} but couldn't launch it: {e}"

    print(f"[open_app] no installed app matched {app_name!r}")
    return f"I couldn't find {app_name} installed."


_FRIENDLY_SITE_NAMES = {
    "youtube": "YouTube",
    "github": "GitHub",
    "linkedin": "LinkedIn",
    "reddit": "Reddit",
    "paypal": "PayPal",
    "gmail": "Gmail",
    "whatsapp": "WhatsApp",
    "tiktok": "TikTok",
    "espn": "ESPN",
    "bbc": "BBC",
    "cnn": "CNN",
    "imdb": "IMDb",
}


def _friendly_site_name(url: str) -> str:
    """Turns a URL into a short name worth saying out loud instead of
    reading the whole address, e.g. 'https://www.youtube.com/watch?v=xyz'
    -> 'YouTube'. Falls back to the raw url if it can't be parsed at all,
    since that's still more useful than staying silent about what opened."""
    try:
        netloc = urllib.parse.urlparse(url).netloc.lower()
    except ValueError:
        return url
    if netloc.startswith("www."):
        netloc = netloc[4:]
    host = netloc.split(":")[0]  # drop a port, if any
    main = host.split(".")[0] if host else ""
    if not main:
        return url
    return _FRIENDLY_SITE_NAMES.get(main, main.capitalize())


def open_url(url: str) -> str:
    """Opens a URL in the default web browser."""
    if not url or not str(url).strip():
        return "I need a URL before I can open anything."
    if not _confirm(f"open the website {url}"):
        return "Cancelled by user."
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    launched_quietly = False
    if getattr(config, "LAUNCH_APPS_IN_BACKGROUND", True):
        launched_quietly = _launch_without_stealing_focus(url)
    if not launched_quietly:
        webbrowser.open(url)  # steals focus, but always works - the fallback
    return f"Opened {_friendly_site_name(url)} for you."


def _desktop_folder() -> str:
    """Returns the real Desktop path, including a redirected OneDrive one."""
    if winreg is not None:
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
            ) as key:
                desktop, _ = winreg.QueryValueEx(key, "Desktop")
            return os.path.expandvars(desktop)
        except OSError:
            pass
    return os.path.join(os.path.expanduser("~"), "Desktop")


def _resolve_placeholder_user_path(path: str) -> str:
    """Replaces an LLM's C:\\Users\\User placeholder with this PC's path."""
    expanded = os.path.expandvars(os.path.expanduser(path)).replace("/", "\\")
    desktop_match = re.match(
        r"^[A-Za-z]:\\Users\\(?:user|username|<user>)\\Desktop(?:\\(.*))?$",
        expanded,
        flags=re.IGNORECASE,
    )
    if desktop_match:
        return os.path.normpath(os.path.join(_desktop_folder(), desktop_match.group(1) or ""))

    home_match = re.match(
        r"^[A-Za-z]:\\Users\\(?:user|username|<user>)(?:\\(.*))?$",
        expanded,
        flags=re.IGNORECASE,
    )
    if home_match:
        return os.path.normpath(os.path.join(os.path.expanduser("~"), home_match.group(1) or ""))
    return os.path.normpath(expanded)


def _friendly_file_name(path: str) -> str:
    """Returns just the file/folder name for speaking out loud instead of
    the full path, e.g. 'C:\\Users\\Alex\\Desktop\\report_final_v2.docx'
    -> 'report_final_v2.docx' - same idea as _friendly_site_name() above,
    just for files instead of URLs."""
    name = os.path.basename(os.path.normpath(path))
    return name or path  # fall back to the full path for an edge case like a bare drive root


def open_file(path: str) -> str:
    """Opens a specific file with its default associated application."""
    if not path or not str(path).strip():
        return "I need a file path before I can open anything."
    path = _resolve_placeholder_user_path(path)
    if not _confirm(f"open the file '{path}'"):
        return "Cancelled by user."
    try:
        launched_quietly = False
        if getattr(config, "LAUNCH_APPS_IN_BACKGROUND", True):
            launched_quietly = _launch_without_stealing_focus(path)
        if not launched_quietly:
            os.startfile(path)  # steals focus, but always works - the fallback
        return f"Opened {_friendly_file_name(path)}."
    except Exception as e:
        return f"Couldn't open {_friendly_file_name(path)}: {e}"


def delete_file(path: str, confirmed: bool = False) -> str:
    """Moves a file or folder to the Recycle Bin after spoken approval."""
    path = _resolve_placeholder_user_path(path)
    if not confirmed:
        if confirmation._critical_confirmation_callback is None:
            return "Confirmation required: please ask the user to approve deleting this item."
        decision = confirmation._critical_confirmation_callback(
            "delete_file", f"move '{path}' to the Recycle Bin", {"path": path}
        )
        if decision is None:
            return "VOICE_CONFIRMATION_REQUIRED"
        if not decision:
            return "Cancelled by user."
    try:
        send2trash.send2trash(path)
        return f"Moved {_friendly_file_name(path)} to the Recycle Bin."
    except Exception as e:
        return f"Couldn't delete {_friendly_file_name(path)}: {e}"


def run_command(command: str, confirmed: bool = False) -> str:
    """Runs a shell command (cmd.exe) after spoken approval."""
    if not confirmed:
        if confirmation._critical_confirmation_callback is None:
            return "Confirmation required: please ask the user to approve this command."
        decision = confirmation._critical_confirmation_callback(
            "run_command", f"run the command '{command}'", {"command": command}
        )
        if decision is None:
            return "VOICE_CONFIRMATION_REQUIRED"
        if not decision:
            return "Cancelled by user."
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=15
        )
        output = (result.stdout or "") + (result.stderr or "")
        if result.returncode:
            return (
                output.strip()[:1000]
                or f"Command failed with exit code {result.returncode}."
            )
        return output.strip()[:1000] or "Command ran with no output."
    except Exception as e:
        return f"Error running command: {e}"
