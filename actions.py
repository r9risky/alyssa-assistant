"""
PC automation actions and system control utilities for Alyssa.
"""

import ctypes
import datetime
import os
import re
import shutil
import subprocess
import time
import urllib.parse
import webbrowser
from ctypes import wintypes
from functools import lru_cache

import pyautogui
import pyperclip
import requests
import send2trash

import config
import memory
import plugin_loader

try:
    import winreg
except ImportError:
    winreg = None

try:
    from PIL import ImageGrab
except ImportError:
    ImageGrab = None

pyautogui.FAILSAFE = True

# Windows user32/shell32 pointers
_user32 = ctypes.windll.user32 if os.name == "nt" else None
_shell32 = ctypes.windll.shell32 if os.name == "nt" else None

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

_WM_CLOSE = 0x0010
_SC_MINIMIZE = 0xF020
_SC_MAXIMIZE = 0xF030
_WM_SYSCOMMAND = 0x0112
_SW_SHOWNOACTIVATE = 4  # show the window but don't activate/foreground it


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


# Set by brain.py. Keeping these hooks here lets the action layer stay
# independent of the voice-conversation logic.
_power_confirmation_callback = None
_critical_confirmation_callback = None


def set_power_confirmation_callback(callback):
    """Sets the callback that begins a spoken power-action confirmation."""
    global _power_confirmation_callback
    _power_confirmation_callback = callback


def set_critical_confirmation_callback(callback):
    """Sets the callback for spoken command and deletion approvals."""
    global _critical_confirmation_callback
    _critical_confirmation_callback = callback


def _confirm(description: str, force: bool = False) -> bool:
    """Asks for y/n confirmation. Normally only if CONFIRM_BEFORE_ACTIONS is
    enabled in config.py - but force=True (used by delete_file, run_command,
    and restart/shutdown) always asks regardless of that setting, since
    those are the actions that are genuinely hard to undo."""
    if not force and not config.CONFIRM_BEFORE_ACTIONS:
        return True
    answer = input(f'About to: {description}\nProceed? [y/N] ').strip().lower()
    return answer == "y"


def _type(text: str, interval: float = 0.02):
    """Types text at the current cursor location. pyautogui.typewrite() only
    knows the handful of characters on a US keyboard and raises KeyError on
    anything else (accented letters, most non-English text, emoji, curly
    quotes) - since spoken commands and dictated text can easily contain
    those, fall back to a clipboard paste for anything typewrite can't
    handle, which works for arbitrary Unicode text.

    IMPORTANT: this check happens BEFORE typing, not as a try/except around
    typewrite(). typewrite() types character-by-character, so if it hit an
    unsupported character partway through a string it would already have
    typed everything before that point for real - then the except branch
    would paste the *entire* original text on top, duplicating that leading
    chunk (e.g. "hello" + unsupported-char + "world" could come out as
    "hellohello world"). Checking up front avoids ever typing a partial
    string in the first place."""
    if text.isascii():
        pyautogui.typewrite(text, interval=interval)
        return

    previous_clipboard = None
    try:
        previous_clipboard = pyperclip.paste()
    except Exception:
        pass  # clipboard read can fail (e.g. empty/non-text clipboard); non-fatal

    pyperclip.copy(text)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.1)  # give the paste a moment to land before restoring clipboard

    if previous_clipboard is not None:
        try:
            pyperclip.copy(previous_clipboard)
        except Exception:
            pass


# Common spoken names -> the actual .exe Windows knows them by. Only needed
# for names that don't already end in .exe or match the exe stem directly.
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


# Hardcoded fallback install locations for common apps, tried if the App
# Paths registry lookup above comes up empty - many popular apps (Spotify,
# Slack, Steam, etc.) are per-user Electron-style installers that don't
# register an App Paths key, so without this they'd fall to the flakier
# Start-search method. %-style env vars are expanded at lookup time.
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


# Some apps (Discord and its PTB/Canary variants) install into a
# version-numbered subfolder that changes on every auto-update
# (%LocalAppData%\Discord\app-1.0.9160\Discord.exe) - a hardcoded path
# would break on the next update, so scan for the highest "app-*" subfolder instead.
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


# Registry locations where nearly every installed Windows app lists itself
# with a DisplayName and usually an InstallLocation/DisplayIcon pointing at
# its exe - scanning this generically avoids hardcoding every possible app;
# only the ones that don't register here reliably need _KNOWN_APP_PATHS entries.
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
    return f"You don't have {app_name} installed."


def type_text(text: str) -> str:
    """Types text at the current cursor location."""
    if text is None:
        return "I need some text before I can type it."
    if not _confirm(f"type: {text!r}"):
        return "Cancelled by user."
    _type(text)
    return "Typed the text."


# The LLM sees the plain-English name in a spoken command ("windows key",
# "control", "escape") and doesn't reliably know pyautogui's exact key
# names - a mismatch here used to just throw and get swallowed as a
# generic "Error running press_keys: ..." with nothing actually pressed.
_KEY_ALIASES = {
    "control": "ctrl", "windows": "win", "window": "win", "super": "win",
    "cmd": "win", "command": "win", "return": "enter", "esc": "escape",
    "del": "delete", "ins": "insert", "pgup": "pageup", "pgdn": "pagedown",
    "spacebar": "space", "plus": "+", "minus": "-",
}


def press_keys(keys: str) -> str:
    """Presses a key combo, e.g. 'ctrl+s' or 'alt+tab'."""
    if not _confirm(f"press keys: {keys}"):
        return "Cancelled by user."
    key_list = [k.strip().lower() for k in keys.split("+") if k.strip()]
    key_list = [_KEY_ALIASES.get(k, k) for k in key_list]
    if not key_list:
        return f"'{keys}' isn't a key combo I can press."
    pyautogui.hotkey(*key_list)
    return f"Pressed {keys}."

# A few well-known sites get a properly-cased spoken name instead of the
# plain capitalized domain (e.g. "YouTube" not "Youtube", "GitHub" not
# "Github"). Anything not listed here just falls back to Title Case of the
# domain's main part, which reads fine for most sites.
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
    return path


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
        if _critical_confirmation_callback is None:
            return "Confirmation required: please ask the user to approve deleting this item."
        decision = _critical_confirmation_callback(
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
        if _critical_confirmation_callback is None:
            return "Confirmation required: please ask the user to approve this command."
        decision = _critical_confirmation_callback(
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


# --- Media control ---------------------------------------------------------
# These rely on standard OS media keys, so they work with whatever app
# currently owns "now playing" (Spotify, YouTube in a browser tab, etc.) -
# no per-app integration needed.

def media_play_pause() -> str:
    """Toggles play/pause on whatever media is currently active."""
    if not _confirm("toggle play/pause"):
        return "Cancelled by user."
    pyautogui.press("playpause")
    return "Toggled play/pause."


def media_next_track() -> str:
    """Skips to the next media track."""
    if not _confirm("skip to the next track"):
        return "Cancelled by user."
    pyautogui.press("nexttrack")
    return "Skipped to the next track."


def media_previous_track() -> str:
    """Goes back to the previous media track."""
    if not _confirm("go to the previous track"):
        return "Cancelled by user."
    pyautogui.press("prevtrack")
    return "Went back to the previous track."


def volume_up(steps: int = 2) -> str:
    """Turns the system volume up. Each step is roughly a 2% increase."""
    steps = max(1, min(int(steps), 20))
    if not _confirm(f"turn the volume up ({steps} step(s))"):
        return "Cancelled by user."
    for _ in range(steps):
        pyautogui.press("volumeup")
    return "Turned the volume up."


def volume_down(steps: int = 2) -> str:
    """Turns the system volume down. Each step is roughly a 2% decrease."""
    steps = max(1, min(int(steps), 20))
    if not _confirm(f"turn the volume down ({steps} step(s))"):
        return "Cancelled by user."
    for _ in range(steps):
        pyautogui.press("volumedown")
    return "Turned the volume down."


def toggle_mute() -> str:
    """Mutes or unmutes system audio."""
    if not _confirm("toggle mute"):
        return "Cancelled by user."
    pyautogui.press("volumemute")
    return "Toggled mute."


def set_volume_level(percent: int = 50) -> str:
    """Sets system volume to a specific percentage (0 to 100%)."""
    try:
        pct = max(0, min(100, int(percent)))
    except (ValueError, TypeError):
        return f"Invalid volume percentage: '{percent}'."

    if not _confirm(f"set system volume to {pct}%"):
        return "Cancelled by user."

    for _ in range(50):
        pyautogui.press("volumedown")

    up_steps = int(pct / 2)
    for _ in range(up_steps):
        pyautogui.press("volumeup")

    return f"Set volume to {pct}%."


# --- Music (Spotify / YouTube Music) ----------------------------------------
# Tries the desktop app first (same resolution as open_app()); falls back
# to the browser version if it isn't installed.
#
# If Spotify/YouTube API credentials are configured (config.py), this
# resolves the query to one actual track/video via that service's search
# API and opens a direct link that starts it playing immediately, instead
# of a search-results page. Without credentials (or on a failed lookup),
# it falls back to the search-results behavior and says so honestly rather
# than claiming a specific song started.

_SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
_SPOTIFY_SEARCH_URL = "https://api.spotify.com/v1/search"

# Cached in-process so a search doesn't re-authenticate every single time -
# client-credentials tokens are valid for about an hour.
_spotify_token_cache = {"access_token": None, "expires_at": 0}


def _get_spotify_token():
    """Returns a valid app-only Spotify access token, fetching/caching a new
    one if needed, or None if credentials aren't configured or the request
    fails. This token can only read public catalog data (search, track/
    album/artist/playlist info) - it has no access to any user's account,
    playlists, or listening history, since play_music never asks anyone to
    log in."""
    client_id = getattr(config, "SPOTIFY_CLIENT_ID", "")
    client_secret = getattr(config, "SPOTIFY_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        return None

    if _spotify_token_cache["access_token"] and time.time() < _spotify_token_cache["expires_at"]:
        return _spotify_token_cache["access_token"]

    try:
        response = requests.post(
            _SPOTIFY_TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(client_id, client_secret),
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException as e:
        print(f"[play_music] Spotify auth failed ({e}); falling back to search link")
        return None

    token = data.get("access_token")
    if not token:
        return None
    # Refresh a little early (60s of slack) rather than risk a request
    # landing right as the cached token expires.
    _spotify_token_cache["access_token"] = token
    _spotify_token_cache["expires_at"] = time.time() + data.get("expires_in", 3600) - 60
    return token


def _spotify_top_match(query: str):
    """Searches Spotify's catalog for `query` and returns (uri, label) for
    the single best match, or None if there's no token available or nothing
    was found. Tries tracks first (the common case - "play X"), then falls
    back to albums/artists/playlists so a request like "play my Discover
    Weekly" or "play some Fleetwood Mac" still resolves to something
    playable instead of only ever matching individual songs."""
    token = _get_spotify_token()
    if not token:
        return None

    headers = {"Authorization": f"Bearer {token}"}
    for search_type, label_fmt in (
        ("track", lambda item: f"{item['name']} by {item['artists'][0]['name']}" if item.get("artists") else item["name"]),
        ("album", lambda item: f"the album {item['name']} by {item['artists'][0]['name']}" if item.get("artists") else f"the album {item['name']}"),
        ("playlist", lambda item: f"the playlist {item['name']}"),
        ("artist", lambda item: item["name"]),
    ):
        try:
            response = requests.get(
                _SPOTIFY_SEARCH_URL,
                headers=headers,
                params={"q": query, "type": search_type, "limit": 1},
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()
        except requests.exceptions.RequestException as e:
            print(f"[play_music] Spotify search failed ({e}); falling back to search link")
            return None

        items = (data.get(f"{search_type}s") or {}).get("items") or []
        # Spotify's API can return a null slot in results for content that's
        # been taken down/region-locked - skip past those instead of
        # treating a null as a match.
        item = next((i for i in items if i), None)
        if item and item.get("uri"):
            try:
                return item["uri"], label_fmt(item)
            except (KeyError, IndexError):
                return item["uri"], item.get("name", query)

    return None


def _spotify_uri_to_web_url(uri: str):
    """Converts a 'spotify:track:ID'-style URI into the equivalent
    'https://open.spotify.com/track/ID' web player link, for when the
    desktop app isn't installed - open.spotify.com starts playing a track/
    album/playlist/artist page directly, same as the app does with the URI."""
    parts = uri.split(":")
    if len(parts) != 3:
        return None
    _, kind, item_id = parts
    return f"https://open.spotify.com/{kind}/{item_id}"


# --- YouTube Music resolution -----------------------------------------------
# Same idea as the Spotify block above, using YouTube Data API v3 search
# instead (a plain API key, no OAuth needed - see config.YOUTUBE_API_KEY).
# No registered desktop-app URI scheme, so the resolved video always opens
# as a music.youtube.com watch link, which still starts playing on load.

_YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"


def _youtube_top_video(query: str):
    """Searches YouTube for `query` and returns (video_id, title) for the
    single best match, or None if no API key or no match. Prefers Music
    category (id 10) results first so "play some jazz" doesn't land on an
    unrelated talk-show clip, falling back to unfiltered search if empty."""
    api_key = getattr(config, "YOUTUBE_API_KEY", "")
    if not api_key:
        return None

    for extra_params in ({"videoCategoryId": "10"}, {}):
        try:
            response = requests.get(
                _YOUTUBE_SEARCH_URL,
                params={
                    "part": "snippet",
                    "q": query,
                    "type": "video",
                    "maxResults": 1,
                    "key": api_key,
                    **extra_params,
                },
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()
        except requests.exceptions.RequestException as e:
            print(f"[play_music] YouTube search failed ({e}); falling back to search link")
            return None

        items = data.get("items") or []
        item = next((i for i in items if i and (i.get("id") or {}).get("videoId")), None)
        if item:
            video_id = item["id"]["videoId"]
            title = (item.get("snippet") or {}).get("title", query)
            return video_id, title

    return None


def _launch_resolved_music(service_key: str, query: str, app_path):
    """Tries to resolve `query` to one specific piece of content and start
    it playing directly, for whichever service_key this is. Returns the
    spoken reply on success, or None if there's no API access configured,
    nothing matched, or launching it failed - in which case play_music()
    falls back to a plain search-results page instead."""
    if service_key == "spotify":
        match = _spotify_top_match(query)
        if not match:
            return None
        uri, label = match
        if app_path:
            try:
                os.startfile(uri)
                return f"Playing {label} on Spotify."
            except OSError as e:
                print(f"[play_music] opening resolved Spotify URI failed ({e}); trying the web player instead")
        web_url = _spotify_uri_to_web_url(uri)
        if not web_url:
            return None
        webbrowser.open(web_url)
        return f"Playing {label} on Spotify in your browser."

    if service_key == "youtube music":
        match = _youtube_top_video(query)
        if not match:
            return None
        video_id, title = match
        # No registered desktop-app URI scheme for YouTube Music, so this
        # always opens as a browser watch link, which starts playing on its own.
        webbrowser.open(f"https://music.youtube.com/watch?v={video_id}")
        return f"Playing {title} on YouTube Music."

    return None


_MUSIC_SERVICES = {
    "spotify": {
        "display_name": "Spotify",
        "app_lookup_name": "spotify",
        # Spotify registers this URI scheme on install - opens the desktop
        # app to search results. Only used as a fallback when
        # _launch_resolved_music() can't resolve to one specific track.
        "search_uri": "spotify:search:{query}",
        "web_search_url": "https://open.spotify.com/search/{query}",
        "web_home_url": "https://open.spotify.com",
    },
    "youtube music": {
        "display_name": "YouTube Music",
        "app_lookup_name": "youtube music",
        # No registered URI scheme for the unofficial YouTube Music desktop
        # clients, so search always happens in-browser. Fallback only.
        "search_uri": None,
        "web_search_url": "https://music.youtube.com/search?q={query}",
        "web_home_url": "https://music.youtube.com",
    },
}

# Lets the LLM's raw "service" argument be a loose phrase and still resolve.
_MUSIC_SERVICE_ALIASES = {
    "spotify": "spotify",
    "youtube music": "youtube music", "youtube": "youtube music",
    "yt music": "youtube music", "ytmusic": "youtube music", "ytm": "youtube music",
}


def play_music(query: str = "", service: str = "spotify") -> str:
    """Plays music via Spotify (default) or YouTube Music. `query` is a
    song/artist/album/playlist to search for - leave blank to just open or
    resume whatever's already cued up. If the relevant API credentials are
    configured (config.SPOTIFY_CLIENT_ID/SECRET or config.YOUTUBE_API_KEY),
    resolves `query` to one specific track/video/album/playlist and
    actually starts it playing; otherwise just opens a search results page
    for the user to pick from. Tries the desktop app first if installed,
    otherwise falls back to opening the service in the browser - same
    approach as open_app(), just service-specific."""
    service_key = _MUSIC_SERVICE_ALIASES.get(service.strip().lower(), "spotify")
    info = _MUSIC_SERVICES[service_key]
    query = query.strip()

    action_desc = f"play '{query}' on {info['display_name']}" if query else f"open {info['display_name']}"
    if not _confirm(action_desc):
        return "Cancelled by user."

    app_path = _resolve_app_path(info["app_lookup_name"])

    # Try to resolve the query to one specific track/video and start it
    # playing, rather than opening a search page - see _launch_resolved_music.
    if query:
        resolved_reply = _launch_resolved_music(service_key, query, app_path)
        if resolved_reply:
            return resolved_reply

    can_deep_link_search = bool(query and info["search_uri"])

    if app_path and (not query or can_deep_link_search):
        try:
            if can_deep_link_search:
                os.startfile(info["search_uri"].format(query=urllib.parse.quote(query)))
                return (
                    f"Opened {info['display_name']} and searched for "
                    f"'{query}' - pick the track and I've got play/pause, "
                    "skip, and volume from there."
                )
            os.startfile(app_path)
            time.sleep(1.5)  # give it a moment to come to the foreground
            pyautogui.press("playpause")  # best-effort: resume whatever's cued up
            return f"Opened {info['display_name']}."
        except OSError as e:
            print(f"[play_music] app launch failed ({e}); falling back to browser")

    url = (
        info["web_search_url"].format(query=urllib.parse.quote(query))
        if query else info["web_home_url"]
    )
    webbrowser.open(url)
    if query:
        return (
            f"Opened '{query}' search results for {info['display_name']} "
            "in your browser - pick the track and I've got play/pause, "
            "skip, and volume from there."
        )
    return f"Opened {info['display_name']} in your browser."


# --- Window management ------------------------------------------------------
# Keyboard-shortcut based, same approach as press_keys - no extra
# dependency needed to control window layout.

def minimize_window() -> str:
    """Minimizes the currently focused window."""
    if not _confirm("minimize the current window"):
        return "Cancelled by user."
    if not _post_syscommand_to_foreground(_SC_MINIMIZE):
        pyautogui.hotkey("win", "down")  # fallback: non-Windows, or no foreground window found
    return "Minimized the window."


def maximize_window() -> str:
    """Maximizes the currently focused window."""
    if not _confirm("maximize the current window"):
        return "Cancelled by user."
    if not _post_syscommand_to_foreground(_SC_MAXIMIZE):
        pyautogui.hotkey("win", "up")  # fallback: non-Windows, or no foreground window found
    return "Maximized the window."


def close_window() -> str:
    """Closes the currently focused window/application."""
    if not _confirm("close the current window"):
        return "Cancelled by user."
    if _close_foreground_window():
        return "Closed the window."
    # Fallback (non-Windows, or no foreground window found): simulate
    # Alt+F4. Not pyautogui.hotkey() - some apps don't register a hotkey()
    # burst as a real Alt+F4 unless Alt is actually held a moment first.
    # switch_window() below uses the same hold-tap-release pattern.
    pyautogui.keyDown("alt")
    pyautogui.press("f4")
    time.sleep(0.3)
    pyautogui.keyUp("alt")
    return "Closed the window."


def switch_window() -> str:
    """Switches focus to the previously active window (alt+tab). Left as a
    real Alt+Tab simulation on purpose - there's no quieter version that
    still brings something else to the screen."""
    if not _confirm("switch to the previous window"):
        return "Cancelled by user."
    pyautogui.keyDown("alt")
    pyautogui.press("tab")
    time.sleep(0.3)
    pyautogui.keyUp("alt")
    return "Switched windows."


def snap_window(side: str) -> str:
    """Snaps the currently focused window to one side of the screen. side:
    'left' or 'right'. Left as a real Win+Left/Right simulation - same
    reasoning as switch_window() above, the visible move IS the result."""
    side = side.strip().lower()
    if side not in ("left", "right"):
        return f"'{side}' isn't a side I recognize - use 'left' or 'right'."
    if not _confirm(f"snap the current window to the {side}"):
        return "Cancelled by user."
    pyautogui.hotkey("win", side)
    return f"Snapped the window to the {side}."


def show_desktop() -> str:
    """Minimizes all windows to show the desktop. Left as a real Win+D
    simulation - same reasoning as switch_window() above."""
    if not _confirm("show the desktop"):
        return "Cancelled by user."
    pyautogui.hotkey("win", "d")
    return "Showed the desktop."


# --- Clipboard ---------------------------------------------------------------

def read_clipboard() -> str:
    """Reads and returns the current clipboard text content."""
    try:
        content = pyperclip.paste()
    except Exception as e:
        return f"Couldn't read the clipboard: {e}"
    if not content:
        return "The clipboard is empty."
    preview = content if len(content) <= 500 else content[:500] + "... (truncated)"
    return f"Clipboard contains: {preview}"


def set_clipboard(text: str) -> str:
    """Copies the given text to the clipboard."""
    if not _confirm(f"copy this to the clipboard: {text!r}"):
        return "Cancelled by user."
    try:
        pyperclip.copy(text)
    except Exception as e:
        return f"Couldn't set the clipboard: {e}"
    return "Copied to the clipboard."


# --- Misc utility -------------------------------------------------------------

def take_screenshot() -> str:
    """Takes a screenshot of the whole screen and saves it to the Pictures
    folder. Uses Pillow's ImageGrab directly rather than
    pyautogui.screenshot(), which routes through pyscreeze and has a known
    import failure on some Python/Pillow combos."""
    if ImageGrab is None:
        return "Couldn't take a screenshot: Pillow isn't installed (pip install Pillow)."
    if not _confirm("take a screenshot"):
        return "Cancelled by user."
    pictures_dir = os.path.join(os.path.expanduser("~"), "Pictures")
    os.makedirs(pictures_dir, exist_ok=True)
    filename = f"alyssa_screenshot_{time.strftime('%Y%m%d_%H%M%S')}.png"
    path = os.path.join(pictures_dir, filename)
    try:
        ImageGrab.grab().save(path)
    except Exception as e:
        return f"Couldn't take a screenshot: {e}"
    return f"Saved a screenshot to your Pictures folder as {_friendly_file_name(path)}."


def describe_screen(question: str = "") -> str:
    """Looks at what's currently on screen and describes it, or answers a
    specific question about it, using a vision-capable model (see
    OLLAMA_VISION_MODEL / GEMINI_MODEL in config.py). Captures a fresh
    screenshot in memory only - unlike take_screenshot(), nothing is ever
    written to disk here."""
    # Imported here, not at module load time, to avoid a circular import -
    # brain.py imports this module, so this resolves fine only once brain
    # has finished importing.
    import brain
    return brain.describe_screen_with_vision(question)


def click_screen_element(description: str, double_click: bool = False, confirmed: bool = False) -> str:
    """Finds a described UI element on screen (via the vision model) and
    clicks it - e.g. 'click the Send button', 'click the X to close that
    popup'. This is what lets Alyssa act on what she sees rather than
    just narrate it, at the cost of being approximate: a language model
    estimating pixel coordinates from a screenshot isn't as reliable as a
    real UI-element lookup, so it works best on clearly labeled, distinct
    targets and can miss small or ambiguous ones. Always confirmed first
    (same as run_command/delete_file) since a misplaced click is hard to
    predict the consequences of - it could hit anything."""
    if not confirmed:
        if _critical_confirmation_callback is None:
            return "Confirmation required: please ask the user to approve this click."
        decision = _critical_confirmation_callback(
            "click_screen_element",
            f"click on '{description}'",
            {"description": description, "double_click": double_click},
        )
        if decision is None:
            return "VOICE_CONFIRMATION_REQUIRED"
        if not decision:
            return "Cancelled by user."

    import brain
    point = brain.locate_screen_element_with_vision(description)
    if point is None:
        return f"I couldn't find '{description}' on screen."
    x_pct, y_pct = point
    screen_w, screen_h = pyautogui.size()
    x = int(screen_w * x_pct / 100)
    y = int(screen_h * y_pct / 100)
    try:
        if double_click:
            pyautogui.doubleClick(x, y)
        else:
            pyautogui.click(x, y)
    except Exception as e:
        return f"Found '{description}' but couldn't click it: {e}"
    return f"Clicked '{description}'."


def enroll_voice() -> str:
    """Records a few short phrases and builds a voiceprint, so voice-based
    access control (see VOICE_ID_ENABLED in config.py) can recognize the
    enrolled user specifically before approving sensitive actions (delete,
    run command, power actions, screen clicks). Re-running this overwrites
    any previous enrollment."""
    import recorder
    import voice as voice_module
    import voice_id

    phrases_needed = 3
    samples = []
    voice_module.speak(
        f"Let's set up voice ID. I'll ask you to speak {phrases_needed} times - "
        "just say a full sentence naturally each time, then pause."
    )
    for i in range(phrases_needed):
        voice_module.speak(f"Phrase {i + 1} of {phrases_needed} - go ahead.")
        time.sleep(0.3)
        audio = recorder.record_command()
        if audio is not None:
            samples.append(audio)
        else:
            voice_module.speak("I didn't catch that one - let's try again.")
            time.sleep(0.3)
            audio = recorder.record_command()
            if audio is not None:
                samples.append(audio)
    return voice_id.enroll_from_samples(samples)


def get_datetime() -> str:
    """Returns the current local date and time."""
    now = datetime.datetime.now()
    return now.strftime("It's %I:%M %p on %A, %B %d, %Y.")


def system_power_action(action: str, confirmed: bool = False) -> str:
    """Locks, sleeps, signs out, restarts, or shuts down the PC."""
    action = action.strip().lower()
    if action not in ("lock", "sleep", "signout", "restart", "shutdown"):
        return f"'{action}' isn't a power action I recognize - use lock, sleep, signout, restart, or shutdown."

    # Every action that changes the current session is approved by voice,
    # never by blocking on input() in the launch terminal.
    if action != "lock" and not confirmed:
        if _power_confirmation_callback is None:
            return "Confirmation required: please ask the user to confirm this power action."
        decision = _power_confirmation_callback(
            "system_power_action", f"{action} the computer", {"action": action}
        )
        if decision is None:
            return "VOICE_CONFIRMATION_REQUIRED"
        if not decision:
            return "Cancelled by user."

    try:
        if action == "lock":
            import ctypes
            ctypes.windll.user32.LockWorkStation()
            return "Locked the PC."
        if action == "sleep":
            subprocess.run(
                ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
                timeout=5,
            )
            return "Putting the PC to sleep."
        if action == "signout":
            subprocess.run(["shutdown", "/l"], timeout=5)
            return "Signing out."
        if action == "restart":
            subprocess.run(["shutdown", "/r", "/t", "0"], timeout=5)
            return "Restarting the PC."
        if action == "shutdown":
            subprocess.run(["shutdown", "/s", "/t", "0"], timeout=5)
            return "Shutting down the PC."
    except Exception as e:
        return f"Couldn't {action} the PC: {e}"
def reset_conversation() -> str:
    """Clears short-term conversation memory (what's been discussed so far
    this session) - use when the user asks to start fresh, change the
    subject, or forget the current conversation. Does NOT touch anything
    saved with remember_fact - that permanent memory is untouched."""
    # Imported here, not at module load time, to avoid a circular import
    # with brain.py (which imports actions) - resolves fine once brain has
    # finished importing, by the time this is actually called.
    import brain
    brain.clear_conversation_history()
    return "Starting fresh - I've cleared what we were just discussing."


def remember_fact(fact: str) -> str:
    """Saves a fact to persistent memory so Alyssa remembers it across restarts."""
    return memory.remember(fact)


def forget_fact(fact_snippet: str) -> str:
    """Removes a previously remembered fact that matches the given snippet."""
    return memory.forget(fact_snippet)


def search_files(query: str, location: str = "", search_contents: bool = False) -> str:
    """Searches for files by name (substring match) under a folder, defaulting
    to the user's home folder if no location is given. Skips noisy/system
    folders. Capped at 25 results and a few thousand files scanned so it
    can't hang searching an entire drive.

    If search_contents is True, also searches inside small text files
    (under 2MB) for the query text, not just filenames - slower, so only
    use it when the user specifically wants to search file contents/text,
    not just find a file by name."""
    query = (query or "").strip()
    if not query:
        return "Tell me part of the file name or text to search for."

    root = os.path.expanduser(location) if location else os.path.expanduser("~")
    if not os.path.isdir(root):
        return f"'{root}' isn't a folder I can search."

    query_lower = query.lower()
    skip_dirs = {
        ".git", "node_modules", "__pycache__", "$recycle.bin",
        "appdata", ".cache", "venv", ".venv", "site-packages",
    }
    text_extensions = {
        ".txt", ".md", ".csv", ".log", ".json", ".py", ".js", ".ts",
        ".html", ".css", ".yml", ".yaml", ".ini", ".cfg",
    }

    matches = []
    scanned = 0
    max_scanned = 5000
    max_results = 25

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d.lower() not in skip_dirs]

        for filename in filenames:
            scanned += 1
            if scanned > max_scanned or len(matches) >= max_results:
                break

            full_path = os.path.join(dirpath, filename)

            if query_lower in filename.lower():
                matches.append(full_path)
                continue

            if search_contents and os.path.splitext(filename)[1].lower() in text_extensions:
                try:
                    if os.path.getsize(full_path) <= 2_000_000:
                        with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                            if query_lower in f.read().lower():
                                matches.append(full_path)
                except OSError:
                    pass

        if scanned > max_scanned or len(matches) >= max_results:
            break

    if not matches:
        return f"No files matching '{query}' found under {root}."

    result = f"Found {len(matches)} match(es) for '{query}' under {root}:\n"
    # Relative to the search root rather than the full absolute path - still
    # enough to tell same-named files in different subfolders apart, but a
    # lot less to read out loud than the whole machine path each time.
    result += "\n".join(os.path.relpath(m, root) for m in matches)
    if len(matches) >= max_results:
        result += "\n(stopped at 25 results - narrow your search for more precise results.)"
    return result


def run_diagnostics() -> str:
    """Runs a self-check across every subsystem Alyssa depends on - the
    configured LLM connection, speech recognition (Whisper), the
    microphone, text-to-speech (Edge TTS), persistent memory, and
    plugins - and reports which are healthy and which (if any) have a
    problem. Use this whenever the user asks Alyssa to run diagnostics
    or a self-test on herself, check her own systems, or asks something
    like 'is everything working?', 'are you okay?', or 'run a health
    check'."""
    # Imported here (not at module load time) to avoid a circular import
    # (brain.py itself imports this module) - same reasoning as
    # describe_screen()/reset_conversation() above.
    import transcribe
    import voice

    checks = []  # (label, ok, detail) - detail is a short plain-English status

    def _append_check(label: str, ok: bool, detail: str):
        checks.append((label, ok, str(detail).strip() or "no details"))

    # --- LLM connection ---
    provider = getattr(config, "LLM_PROVIDER", "ollama")
    if provider == "ollama":
        try:
            requests.get("http://localhost:11434", timeout=3)
        except requests.exceptions.RequestException as e:
            _append_check(
                "LLM connection (Ollama)", False,
                f"can't reach Ollama at localhost:11434 ({e.__class__.__name__}) - is it running?",
            )
        else:
            wanted = config.OLLAMA_MODEL
            try:
                response = requests.get("http://localhost:11434/api/tags", timeout=5)
                response.raise_for_status()
                tags = response.json()
                pulled = {m.get("name") for m in tags.get("models", []) if isinstance(m, dict) and m.get("name")}
                have_it = wanted in pulled or any(
                    p.split(":")[0] == wanted.split(":")[0] for p in pulled
                )
            except requests.exceptions.RequestException as e:
                have_it = None
                _append_check(
                    "LLM connection (Ollama)", True,
                    f"running, but the model list could not be verified ({e.__class__.__name__})",
                )
            except (ValueError, TypeError):
                have_it = None
                _append_check(
                    "LLM connection (Ollama)", True,
                    "running, but the model list response was not readable",
                )
            else:
                if have_it:
                    _append_check("LLM connection (Ollama)", True, f"running, model '{wanted}' is pulled and ready")
                else:
                    _append_check("LLM connection (Ollama)", False, f"running, but model '{wanted}' isn't pulled yet")
    else:
        key_attr, label = {
            "gemini": ("GEMINI_API_KEY", "Gemini"),
            "openai": ("OPENAI_API_KEY", "OpenAI"),
            "anthropic": ("ANTHROPIC_API_KEY", "Claude"),
            "custom_openai": ("CUSTOM_API_KEY", "the custom provider"),
        }.get(provider, (None, provider))
        if provider == "custom_openai":
            base_url = getattr(config, "CUSTOM_BASE_URL", "")
            if base_url:
                _append_check(f"LLM connection ({label})", True, f"configured to use {base_url}")
            else:
                _append_check(f"LLM connection ({label})", False, "CUSTOM_BASE_URL isn't set in config.py")
        elif key_attr and getattr(config, key_attr, ""):
            _append_check(f"LLM connection ({label})", True, "API key is configured")
        else:
            _append_check(f"LLM connection ({label})", False, f"{key_attr} isn't set in config.py")

    # --- Speech recognition (Whisper) ---
    # get_engine_status() reports what's REALLY running (GPU vs CPU,
    # precision) rather than just what config.py requests, since "auto"
    # settings resolve differently per machine and a GPU load can silently
    # fall back to CPU - see transcribe.py.
    try:
        _append_check("Speech recognition", True, transcribe.get_engine_status())
    except Exception as e:
        _append_check("Speech recognition", False, f"couldn't determine the Whisper engine state ({e})")

    # --- Microphone ---
    try:
        import sounddevice as sd
        devices = sd.query_devices(kind="input")
        if isinstance(devices, list):
            device_info = next((d for d in devices if d.get("default") or d.get("name") == sd.default.device[0]), None)
            if isinstance(device_info, dict):
                name = device_info.get("name") or "unknown"
                _append_check("Microphone", True, f"default input device detected ({name})")
            else:
                _append_check("Microphone", True, "input devices detected, but no default device summary was available")
        else:
            _append_check("Microphone", True, "input devices detected")
    except Exception as e:
        _append_check("Microphone", False, f"no working input device found ({e})")

    # --- Text-to-speech ---
    if not getattr(config, "SPEAK_RESPONSES", True):
        _append_check("Text-to-speech", True, "turned off in settings - replies are text-only right now")
    else:
        provider = getattr(config, "TTS_PROVIDER", "edge")
        provider_label = "ElevenLabs" if provider == "elevenlabs" else "Edge TTS"
        try:
            test_path = voice._synthesize_to_temp_file("Diagnostics test.")
            ok = os.path.exists(test_path) and os.path.getsize(test_path) > 0
            try:
                os.remove(test_path)
            except OSError:
                pass
            if ok:
                voice_label = getattr(config, "ELEVENLABS_VOICE_ID", "") if provider == "elevenlabs" else config.EDGE_TTS_VOICE
                _append_check("Text-to-speech", True, f"{provider_label} is reachable, voice '{voice_label}' works")
            else:
                _append_check("Text-to-speech", False, f"{provider_label} returned an empty audio file")
        except Exception as e:
            _append_check("Text-to-speech", False, f"couldn't reach {provider_label} ({e})")

    # --- Persistent memory ---
    try:
        current = memory.load_memories()
        memory.save_memories(current)  # round-trip write, confirms the database is actually writable too
        _append_check("Memory", True, f"local memory database is readable and writable ({len(current)} fact(s) saved)")
    except Exception as e:
        _append_check("Memory", False, f"couldn't read/write the memory database ({e})")

    # --- Plugins ---
    if not getattr(config, "PLUGINS_ENABLED", True):
        _append_check("Plugins", True, "disabled in config.py")
    else:
        errors = plugin_loader.get_load_errors() + _PLUGIN_LOAD_PROBLEMS
        count = len(PLUGIN_FUNCTIONS)
        if errors:
            _append_check("Plugins", False, f"{count} loaded, but ran into: {'; '.join(errors)}")
        else:
            _append_check("Plugins", True, f"{count} loaded, no errors")

    # --- Background watcher (proactive alerts) ---
    if not getattr(config, "ENABLE_BACKGROUND_WATCHER", True):
        _append_check("Background watcher", True, "disabled in config.py")
    else:
        watchers = plugin_loader.get_watchers()
        if watchers:
            names = ", ".join(w["name"] for w in watchers)
            _append_check("Background watcher", True, f"monitoring: {names}")
        else:
            _append_check("Background watcher", True, "enabled, but no plugin currently registers a proactive check")

    # --- Voice ID ---
    if getattr(config, "VOICE_ID_ENABLED", False):
        try:
            import voice_id
        except Exception as e:
            _append_check("Voice ID", False, f"enabled, but the voice ID module could not be imported ({e})")
        else:
            try:
                enrolled = voice_id.is_enrolled()
            except Exception as e:
                _append_check("Voice ID", False, f"enabled, but checking enrollment failed ({e})")
            else:
                if enrolled:
                    _append_check("Voice ID", True, "enabled and a voiceprint is enrolled")
                else:
                    _append_check("Voice ID", False, "enabled, but no voiceprint enrolled yet - say 'enroll my voice'")

    problems = [c for c in checks if not c[1]]

    def _health_hint(label: str, detail: str) -> str:
        lowered = (detail or "").lower()
        if "ollama" in lowered and "running" in lowered:
            return "If this is unexpected, check that Ollama is installed and the model has been pulled."
        if "microphone" in lowered or "input device" in lowered:
            return "If this is unexpected, verify the microphone is connected and selected as the default input device."
        if "speech recognition" in lowered or "whisper" in lowered:
            return "If this is unexpected, check your Whisper installation and GPU/CUDA availability."
        if "text-to-speech" in lowered or "edge tts" in lowered:
            return "If this is unexpected, verify your TTS provider settings and network access."
        if "memory" in lowered:
            return "If this is unexpected, check that the memory.json file is writable and the folder permissions are correct."
        if "plugin" in lowered:
            return "If this is unexpected, review the plugin file for import errors or conflicting tool names."
        if "voice id" in lowered:
            return "If this is unexpected, run the voice enrollment flow again to create a new voiceprint."
        return ""

    if not problems:
        summary = (
            "All systems normal - everything Alyssa depends on is working fine. "
            "The assistant should be ready to listen, think, and act."
        )
    else:
        summary = (
            f"Found {len(problems)} issue(s) that may affect Alyssa's behavior. "
            f"The most relevant ones are: "
            + "; ".join(f"{label} - {detail}" for label, _, detail in problems)
        )

    detailed_lines = []
    detailed_lines.append(summary)
    detailed_lines.append("")
    detailed_lines.append("Detailed status:")
    for label, ok, detail in checks:
        prefix = "OK" if ok else "PROBLEM"
        detailed_lines.append(f"- [{prefix}] {label}: {detail}")
        hint = _health_hint(label, detail)
        if hint:
            detailed_lines.append(f"  Hint: {hint}")

    return "\n".join(detailed_lines)


# Registry mapping tool name -> python function, used by brain.py
_BUILTIN_FUNCTIONS = {
    "open_app": open_app,
    "type_text": type_text,
    "press_keys": press_keys,
    "open_url": open_url,
    "open_file": open_file,
    "delete_file": delete_file,
    "run_command": run_command,
    "remember_fact": remember_fact,
    "forget_fact": forget_fact,
    "search_files": search_files,
    "media_play_pause": media_play_pause,
    "media_next_track": media_next_track,
    "media_previous_track": media_previous_track,
    "play_music": play_music,
    "volume_up": volume_up,
    "volume_down": volume_down,
    "toggle_mute": toggle_mute,
    "set_volume_level": set_volume_level,
    "minimize_window": minimize_window,
    "maximize_window": maximize_window,
    "close_window": close_window,
    "switch_window": switch_window,
    "snap_window": snap_window,
    "show_desktop": show_desktop,
    "read_clipboard": read_clipboard,
    "set_clipboard": set_clipboard,
    "take_screenshot": take_screenshot,
    "describe_screen": describe_screen,
    "click_screen_element": click_screen_element,
    "enroll_voice": enroll_voice,
    "get_datetime": get_datetime,
    "system_power_action": system_power_action,
    "reset_conversation": reset_conversation,
    "run_diagnostics": run_diagnostics,
}

# Extra abilities dropped into plugins/ - see plugin_loader.py and
# plugins/example_dice_and_jokes.py. reload_plugins() (below) populates
# these and is called once at import time, then again any time the
# Settings > Plugins editor saves, enables/disables, adds, or removes a
# plugin file - so a live PySide6 session picks up plugin changes without
# restarting the whole app. brain.py reads PLUGIN_TOOLS (indirectly, via
# its own reload_plugin_tools()) to extend its TOOLS list.
FUNCTIONS = {}
PLUGIN_FUNCTIONS = {}
PLUGIN_TOOLS = []
# Problems from the most recent reload_plugins() call - a plugin whose tool
# name collides with a built-in action - recorded (not just printed) so
# run_diagnostics() above can report them as part of Alyssa's plugin health
# check.
_PLUGIN_LOAD_PROBLEMS = []


def reload_plugins():
    """(Re)loads every plugin from plugins/ and rebuilds FUNCTIONS,
    PLUGIN_FUNCTIONS, PLUGIN_TOOLS, and _PLUGIN_LOAD_PROBLEMS from scratch.
    Safe to call repeatedly - e.g. from the Settings > Plugins editor after
    a save, enable/disable, add, or delete - since it always starts back
    from _BUILTIN_FUNCTIONS rather than mutating the previous state.
    Callers that also need brain.TOOLS to reflect the change should call
    brain.reload_plugin_tools() right after this."""
    global FUNCTIONS, PLUGIN_FUNCTIONS, PLUGIN_TOOLS, _PLUGIN_LOAD_PROBLEMS

    plugin_functions, plugin_tools = plugin_loader.load_plugins()

    # Plugins are intentionally unable to replace a built-in action. Aside
    # from matching the documented contract, this prevents a stray plugin
    # file from silently changing core behavior such as delete_file or
    # run_command.
    problems = []
    for plugin_name in list(plugin_functions):
        if plugin_name in _BUILTIN_FUNCTIONS:
            print(
                f"[plugins] Ignoring '{plugin_name}' because it conflicts with "
                "a built-in action."
            )
            problems.append(f"'{plugin_name}' conflicts with a built-in action")
            del plugin_functions[plugin_name]

    plugin_tools = [
        tool
        for tool in plugin_tools
        if tool.get("function", {}).get("name") in plugin_functions
    ]

    PLUGIN_FUNCTIONS = plugin_functions
    PLUGIN_TOOLS = plugin_tools
    _PLUGIN_LOAD_PROBLEMS = problems
    FUNCTIONS = {**_BUILTIN_FUNCTIONS, **PLUGIN_FUNCTIONS}


reload_plugins()
