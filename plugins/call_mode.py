"""Call & Meeting Mode Plugin (`join_call`, `leave_call`, `relay_to_call`, `confirm_friend_request`).

Inspired by Capri Desktop's `CallMode.cs`:
- Joins Discord/Vesktop voice channels, Zoom, Microsoft Teams, WhatsApp, Steam, or Browser calls.
- Relays messages to friends on the call (e.g. "tell them I'm joining in ten minutes, and open the game they're on").
- Arms delegated errands to launch games or music that friends in the call choose.
- Strict Security Gate: Friends' requests are quarantined and held for the user's explicit confirmation.
"""

from __future__ import annotations

import random
import sys
import time
from typing import Optional

from call_ears import get_call_session, parse_call_friend_intent
import config

TAB_TITLE = "Call & Meeting Mode"


# Process name -> display name used by call detection. Vesktop is a Discord client.
CALL_APP_PROCESSES = {
    "discord.exe": "Discord",
    "discordptb.exe": "Discord",
    "discordcanary.exe": "Discord",
    "vesktop.exe": "Vesktop",
    "zoom.exe": "Zoom",
    "ms-teams.exe": "Teams",
    "teams.exe": "Teams",
    "whatsapp.exe": "WhatsApp",
    "telegram.exe": "Telegram",
    "slack.exe": "Slack",
    "signal.exe": "Signal",
    "skype.exe": "Skype",
    "ts3client_win64.exe": "TeamSpeak",
    "teamspeak.exe": "TeamSpeak",
    "mumble.exe": "Mumble",
    "steam.exe": "Steam",
}
# Asking for one of these also accepts the others (e.g. "join the Discord call" while Vesktop runs).
_APP_FAMILIES = {"discord": {"Discord", "Vesktop"}, "vesktop": {"Discord", "Vesktop"}}


def detect_call_apps() -> dict[str, list[int]]:
    """Running call apps as {display name: [pids]}, in CALL_APP_PROCESSES order."""
    try:
        import psutil
    except ImportError:
        return {}
    found: dict[str, list[int]] = {}
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = (proc.info.get("name") or "").lower()
        except Exception:
            continue
        app = CALL_APP_PROCESSES.get(name)
        if app:
            found.setdefault(app, []).append(proc.info["pid"])
    order = list(dict.fromkeys(CALL_APP_PROCESSES.values()))
    return {app: found[app] for app in order if app in found}


def resolve_call_app(requested: str = "") -> tuple[str, list[int]]:
    """Pick which call app to use and the pids of its running processes (empty if not running)."""
    wanted = (requested or "").strip()
    if not wanted or wanted.lower() == "auto":
        wanted = str(getattr(config, "CALL_APP", "auto") or "auto").strip()
    running = detect_call_apps()
    if wanted.lower() == "auto":
        if running:
            app = next(iter(running))
            return app, running[app]
        return "Discord", []
    accepted = _APP_FAMILIES.get(wanted.lower(), set())
    for app, pids in running.items():
        if app.lower() == wanted.lower() or app in accepted:
            return app, pids
    return wanted[:1].upper() + wanted[1:], []


def _focus_process_window(pids: list[int]) -> bool:
    """Bring the first visible titled window of *pids* to the front (Windows only)."""
    if sys.platform != "win32" or not pids:
        return False
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    wanted = set(pids)
    hits: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _enum(hwnd, _lparam):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in wanted and user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
            hits.append(hwnd)
            return False
        return True

    user32.EnumWindows(_enum, 0)
    if not hits:
        return False
    hwnd = hits[0]
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    return bool(user32.SetForegroundWindow(hwnd))


def _discord_quick_switch(query: str) -> None:
    """Open Discord's quick switcher (Ctrl+K), type *query*, and pick the top match.
    Prefixes: '*' searches servers, '!' searches voice channels (picking one joins it)."""
    import pyautogui

    pyautogui.hotkey("ctrl", "k")
    time.sleep(0.4)
    if query.isascii():
        pyautogui.write(query, interval=0.02)
    else:
        import pyperclip  # pyautogui can't type non-ASCII names; paste them instead

        pyperclip.copy(query)
        pyautogui.hotkey("ctrl", "v")
    time.sleep(0.7)
    pyautogui.press("enter")
    time.sleep(0.9)


# The message/errand from a "join the call, tell them ..." that had to stop and ask
# which server/channel, so the follow-up answer doesn't lose it.
_pending_join: dict = {}


def join_call(
    args: Optional[dict] = None,
    app: str = "auto",
    server: str = "",
    channel: str = "",
    message: str = "",
    errand: str = "open_app",
    **kwargs,
) -> str:
    """Join a Discord/Vesktop voice channel, Zoom, Teams, WhatsApp, or Browser call and relay a message."""
    if isinstance(args, dict):
        app = args.get("app") or app
        server = args.get("server") or server
        channel = args.get("channel") or channel
        message = args.get("message") or message
        errand = args.get("errand") or errand

    server = (server or "").strip().lstrip("*")
    channel = (channel or "").strip().lstrip("#!")
    if _pending_join:
        message = message or _pending_join.get("message", "")
        server = server or _pending_join.get("server", "")
        channel = channel or _pending_join.get("channel", "")
        errand = _pending_join.get("errand", errand) if errand == "open_app" else errand

    import discord_bot

    if discord_bot.ready() and str(app or "auto").strip().lower() in ("", "auto", "discord", "vesktop"):
        # She joins as her own bot account: no clicking around in your Discord.
        _pending_join.clear()
        result = discord_bot.join(server, channel)
        if not discord_bot.connected():
            return result
        session = get_call_session()
        session.start_call(app_name="Discord", channel_name=channel)
        if errand in ("open_app", "play_music", "game", "song"):
            session.set_errand(action="open_app" if errand in ("open_app", "game") else "play_music", ask=message, ttl_seconds=180.0)
        name = getattr(config, "USER_NICKNAME", "") or "my person"
        hello = f"Hey everyone! {name} sent me in." + (f" They said: {message}." if message else "")
        if not discord_bot.can_listen():
            result += " I can talk here, but I can't hear the call (listening isn't installed)."
        return f"{result} Say exactly this next, word for word: {hello}"

    app_choice, pids = resolve_call_app(app)
    auto_join = getattr(config, "CALL_AUTO_JOIN_ENABLED", True)
    is_discord = app_choice in ("Discord", "Vesktop")

    # Discord users are in many servers: ask instead of guessing which call is meant.
    if is_discord and pids and auto_join and not (server and channel):
        _pending_join.clear()
        _pending_join.update(message=message, errand=errand, server=server, channel=channel)
        missing = " and ".join(
            part for part, value in (("server", server), ("voice channel", channel)) if not value
        )
        known = f" on {server}" if server else (f" for #{channel}" if channel else "")
        return f"Which {missing} should I join{known}?"
    _pending_join.clear()

    session = get_call_session()

    # 1. Start call session (shows the HUD via a queued Qt signal)
    session.start_call(app_name=app_choice, channel_name=channel)

    # 2. Arm errand if requested (e.g. "launch whatever they're playing")
    if errand in ("open_app", "play_music", "game", "song"):
        action = "open_app" if errand in ("open_app", "game") else "play_music"
        session.set_errand(action=action, ask=message, ttl_seconds=180.0)

    # 3. Focus the call app and click the voice channel / Join button if auto-join is on.
    # The user's own "join the call" plus the auto-join setting is the approval for the click.
    join_note = ""
    if auto_join and pids:
        try:
            if _focus_process_window(pids):
                time.sleep(0.4)
                if is_discord:
                    _discord_quick_switch(f"*{server}")
                    _discord_quick_switch(f"!{channel}")
                else:
                    import actions
                    target_desc = f"{channel} voice channel" if channel else "Join Voice button"
                    result = actions.click_screen_element(target_desc, confirmed=True)
                    if not str(result).startswith("Clicked"):
                        join_note = f" (Couldn't click into the channel myself: {result})"
            else:
                join_note = f" (Couldn't bring {app_choice} to the front to join.)"
        except Exception as e:
            join_note = f" (Couldn't click into the channel myself: {e})"
    elif not pids:
        join_note = f" ({app_choice} doesn't seem to be running, so I couldn't click into the call.)"

    # 4. Spoken intro for friends. Returned as the reply so it goes through the normal
    # speech path (which also feeds the virtual mic) instead of a second, overlapping speaker.
    user_name = getattr(config, "USER_NICKNAME", "your friend")
    excuses = [
        f"Hey everyone! {user_name} asked me to hop in and let you know they'll be on shortly.",
        f"Hey! {user_name} is running about 15 minutes late — probably fighting a rogue burrito.",
        f"Yo! {user_name} sent me to cover for them for a few minutes while they get ready.",
        f"Hey all! {user_name} will be here in a few minutes.",
    ]
    base_intro = random.choice(excuses)
    if message:
        spoken_text = f"{base_intro} They said: '{message}'. What game are we playing tonight?"
    else:
        spoken_text = f"{base_intro} What game or music are we putting on today?"

    session.post_caption(getattr(config, "ASSISTANT_NAME", "Alyssa"), spoken_text)

    channel_info = f" in #{channel}" if channel else ""
    if server:
        channel_info += f" on {server}"
    return f"Joined {app_choice}{channel_info} with Call Mode on.{join_note} {spoken_text}"


def leave_call(args: Optional[dict] = None, **kwargs) -> str:
    """Hang up or leave the active voice call/meeting and disable Call Mode."""
    session = get_call_session()
    if not session.enabled:
        return "You're not currently in a call."

    app_name = session.app_name
    duration = int(session.call_duration)
    import discord_bot

    if discord_bot.connected():
        discord_bot.leave()
    mins = duration // 60
    secs = duration % 60
    session.end_call()

    return f"Left the {app_name} call (call duration: {mins:02d}:{secs:02d}). Call Mode is now off."


def relay_to_call(args: Optional[dict] = None, message: str = "", **kwargs) -> str:
    """Speak a message directly into the active voice call via the virtual microphone."""
    if isinstance(args, dict):
        message = args.get("message") or message

    session = get_call_session()
    if not session.enabled:
        return "Call Mode isn't active right now. Say 'join the Discord call' first."

    cleaned = (message or "").strip()
    if not cleaned:
        return "No message specified."

    session.post_caption(getattr(config, "ASSISTANT_NAME", "Alyssa"), cleaned)
    # Never play audio from this tool thread (two players at once crash pygame).
    # The reply is spoken by the normal speech path, which also feeds the call.
    return f"Relaying to the call. Your whole reply must be exactly this, word for word: {cleaned}"


def confirm_friend_request(args: Optional[dict] = None, confirm: bool = True, **kwargs) -> str:
    """Authorize or reject a game or music request made by friends on the call."""
    if isinstance(args, dict):
        confirm = args.get("confirm", True)

    session = get_call_session()
    req = session.pending_request
    if req is None or req.is_expired:
        session.pending_request = None
        return "There are no pending requests from people on the call."

    session.pending_request = None
    if not confirm:
        return f"Declined request from {req.who} to {req.action.replace('_', ' ')}."

    # Execute the confirmed request
    try:
        import actions
        func = actions.FUNCTIONS.get(req.action)
        if func is not None:
            res = func(**req.arguments)
            return f"Approved! Running {req.action}: {res}"
        return f"Unknown action {req.action}."
    except Exception as e:
        return f"Error executing {req.action}: {e}"


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "join_call",
            "description": (
                "Join a voice call or meeting on Discord (or Vesktop), Zoom, Microsoft Teams, WhatsApp, "
                "Steam, or Browser. Automatically clicks into the voice channel, speaks your message "
                "with a cheeky excuse, activates Call Mode with live HUD, and listens for what game "
                "or music your friends pick."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "app": {
                        "type": "string",
                        "description": "App name: 'auto' (detect the running call app), 'Discord', 'Vesktop', 'Zoom', 'Teams', 'WhatsApp', 'Steam', or 'Browser'.",
                        "default": "auto",
                    },
                    "server": {
                        "type": "string",
                        "description": "Discord server name. If the user didn't say, call without it and she will ask.",
                        "default": "",
                    },
                    "channel": {
                        "type": "string",
                        "description": "Voice channel name in that server. If the user didn't say, call without it and she will ask.",
                        "default": "",
                    },
                    "message": {
                        "type": "string",
                        "description": "Message to pass on to your friends (e.g. 'I'm joining in ten minutes').",
                        "default": "",
                    },
                    "errand": {
                        "type": "string",
                        "description": "What to do with their reply: 'open_app' (launch the game they pick) or 'play_music'.",
                        "enum": ["open_app", "play_music"],
                        "default": "open_app",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "leave_call",
            "description": "Hang up or disconnect from the active Discord/Zoom voice call and disable Call Mode.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "relay_to_call",
            "description": "Speak a message out loud into the active voice call/meeting so friends hear it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "The message to speak into the call.",
                    },
                },
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "confirm_friend_request",
            "description": "Confirm or decline a game or music request made by friends on the Discord/voice call.",
            "parameters": {
                "type": "object",
                "properties": {
                    "confirm": {
                        "type": "boolean",
                        "description": "True to authorize and execute the request; False to decline.",
                        "default": True,
                    },
                },
            },
        },
    },
]

FUNCTIONS = {
    "join_call": join_call,
    "leave_call": leave_call,
    "relay_to_call": relay_to_call,
    "confirm_friend_request": confirm_friend_request,
}
