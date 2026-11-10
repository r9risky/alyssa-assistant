"""Call & Meeting Mode Plugin (`join_call`, `leave_call`, `relay_to_call`, `confirm_friend_request`).

Inspired by Capri Desktop's `CallMode.cs`:
- Joins Discord voice channels, Zoom, Microsoft Teams, WhatsApp, Steam, or Browser calls.
- Relays messages to friends on the call (e.g. "tell them I'll be on in 15, and launch whatever they're playing").
- Arms delegated errands to launch games or music that friends in the call choose.
- Strict Security Gate: Friends' requests are quarantined and held for the user's explicit confirmation.
"""

from __future__ import annotations

import random
import threading
import time
from typing import Optional

from call_ears import get_call_session, parse_call_friend_intent
import config

TAB_TITLE = "Call & Meeting Mode"


def join_call(
    args: Optional[dict] = None,
    app: str = "Discord",
    channel: str = "",
    message: str = "",
    errand: str = "open_app",
    **kwargs,
) -> str:
    """Join a Discord voice channel, Zoom, Teams, WhatsApp, or Browser call and relay a message."""
    if isinstance(args, dict):
        app = args.get("app") or app
        channel = args.get("channel") or channel
        message = args.get("message") or message
        errand = args.get("errand") or errand

    app_choice = (app or "Discord").strip()
    session = get_call_session()

    # 1. Start call session
    session.start_call(app_name=app_choice, channel_name=channel)

    # 2. Arm errand if requested (e.g. "launch whatever they're playing")
    if errand in ("open_app", "play_music", "game", "song"):
        action = "open_app" if errand in ("open_app", "game") else "play_music"
        session.set_errand(action=action, ask=message, ttl_seconds=180.0)

    # 3. Attempt to focus app and click voice channel / Join button if auto-join is on
    if getattr(config, "CALL_AUTO_JOIN_ENABLED", True):
        try:
            import actions
            # Bring app window to front
            actions.switch_window(app_choice)
            time.sleep(0.4)
            # Locate Join button or channel name on screen
            target_desc = f"{channel} voice channel" if channel else "Join Voice button"
            actions.click_screen_element(target_desc)
        except Exception:
            pass

    # 4. Formulate spoken intro message to friends on the call
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

    # Queue message to call
    asst_name = getattr(config, "ASSISTANT_NAME", "Alyssa")
    session.post_caption(asst_name, spoken_text)

    try:
        import voice_playback
        def _speak_delayed():
            time.sleep(0.5)
            voice_playback.speak(spoken_text)
        threading.Thread(target=_speak_delayed, daemon=True).start()
    except Exception:
        pass

    channel_info = f" in #{channel}" if channel else ""
    return (
        f"Joined {app_choice}{channel_info} and enabled Call Mode! "
        f"Spoke your message to the call: '{spoken_text}'. "
        "I'll listen for what game or music your friends pick and ask for your OK before launching."
    )


def leave_call(args: Optional[dict] = None, **kwargs) -> str:
    """Hang up or leave the active voice call/meeting and disable Call Mode."""
    session = get_call_session()
    if not session.enabled:
        return "You're not currently in a call."

    app_name = session.app_name
    duration = int(session.call_duration)
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
    try:
        import voice_playback
        voice_playback.speak(cleaned)
    except Exception as e:
        return f"Couldn't speak to call: {e}"

    return f"Spoke to call: '{cleaned}'"


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
                "Join a voice call or meeting on Discord, Zoom, Microsoft Teams, WhatsApp, "
                "Steam, or Browser. Automatically clicks into the voice channel, speaks your message "
                "with a cheeky excuse, activates Call Mode with live HUD, and listens for what game "
                "or music your friends pick."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "app": {
                        "type": "string",
                        "description": "App name: 'Discord', 'Zoom', 'Teams', 'WhatsApp', 'Steam', or 'Browser'.",
                        "default": "Discord",
                    },
                    "channel": {
                        "type": "string",
                        "description": "Voice channel name in Discord (optional).",
                        "default": "",
                    },
                    "message": {
                        "type": "string",
                        "description": "Message to pass on to your friends (e.g. 'I will be on in 15').",
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
