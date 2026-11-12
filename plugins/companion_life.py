"""Desktop Companion Life & Movement Plugin (`move_companion` tool).

Lets the user control Alyssa's desktop companion body by voice or chat:
- `come_here`: Move next to the mouse cursor
- `ride_cursor`: Ride alongside the mouse cursor until told to hop off (`Ctrl+Alt+C`)
- `hop_off`: Stop riding the mouse cursor
- `wander` / `stop_wander`: Start or stop wandering across window ledges and the desktop floor
- `sleep` / `wake`: Take a nap or wake up
- `hide` / `show`: Hide or show the desktop companion overlay
- `toggle_mini_player`: Show or hide the cyberpunk mini music player bar
"""

from __future__ import annotations



def move_companion(action: str = "come_here", wander_frequency: str = "sometimes") -> str:
    """Control the desktop companion's movement, posture, cursor-riding, sleep, or mini player."""
    act = (action or "come_here").strip().lower()
    try:
        from overlay.widgets import get_active_companion, run_on_gui_thread, save_overlay_settings
    except ImportError:
        return "The desktop companion overlay isn't available right now."

    companion = get_active_companion()
    if companion is None:
        return "The desktop companion window isn't active right now."

    if act in ("come_here", "come", "cursor"):
        run_on_gui_thread(companion.come_to_cursor)
        return "Coming over to your cursor."

    if act in ("ride_cursor", "ride", "follow_cursor"):
        run_on_gui_thread(companion.start_cursor_ride)
        return "Hopping onto your cursor! Press Ctrl+Alt+C or tell me to hop off anytime."

    if act in ("hop_off", "stop_riding", "unfollow"):
        run_on_gui_thread(companion.stop_cursor_ride)
        return "Hopped off your cursor."

    if act in ("wander", "roam", "walk"):
        freq = (wander_frequency or "sometimes").strip().lower()
        if freq not in ("rarely", "sometimes", "often", "restless"):
            freq = "sometimes"
        companion.settings["wander_mode"] = freq
        save_overlay_settings(companion.settings)
        run_on_gui_thread(companion.wander_step)
        return f"I'll wander around your desktop ({freq})."

    if act in ("stop_wander", "stay", "sit"):
        companion.settings["wander_mode"] = "off"
        save_overlay_settings(companion.settings)
        return "Got it — staying right in my spot."

    if act in ("sleep", "nap", "rest"):
        run_on_gui_thread(companion.enter_sleep)
        return "Taking a little nap. Just call my name when you need me."

    if act in ("wake", "wake_up"):
        run_on_gui_thread(companion.wake_up)
        return "I'm awake and ready!"

    if act == "hide":
        run_on_gui_thread(companion.hide)
        return "Hiding my overlay. Use the tray icon or Ctrl+Alt+H to bring me back."

    if act == "show":
        run_on_gui_thread(companion.show)
        return "Back on your screen!"

    if act in ("toggle_mini_player", "mini_player", "music_player"):
        enabled = not bool(companion.settings.get("mini_player_enabled", False))
        companion.settings["mini_player_enabled"] = enabled
        save_overlay_settings(companion.settings)
        if getattr(companion, "mini_player", None) is not None:
            run_on_gui_thread(companion.mini_player.apply_enabled)
        return "Showing the mini music player." if enabled else "Hid the mini music player."

    if act in ("mood", "emote", "react"):
        mood_type = (wander_frequency or "happy").strip().lower()
        if hasattr(companion, "set_mood"):
            run_on_gui_thread(lambda: companion.set_mood(mood_type))
            return f"Expressed mood: {mood_type}."
        return "Companion mood not supported."

    return (
        f"Unknown companion action '{action}'. Use come_here, ride_cursor, hop_off, "
        "wander, stop_wander, sleep, wake, hide, show, toggle_mini_player, or mood."
    )


def set_companion_mood(mood: str = "happy") -> str:
    """Set the desktop companion's mood and emote badge (e.g. happy, thinking, surprised, sparkle, sleepy, cool, neutral)."""
    try:
        from overlay.widgets import get_active_companion, run_on_gui_thread
    except ImportError:
        return "The desktop companion overlay isn't available right now."
    companion = get_active_companion()
    if companion is None:
        return "The desktop companion window isn't active right now."
    if hasattr(companion, "set_mood"):
        run_on_gui_thread(lambda: companion.set_mood(mood))
        return f"Expressed mood: {mood}."
    return "Companion mood not supported."


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "move_companion",
            "description": (
                "Control Alyssa's desktop companion avatar on screen: move to the user's cursor "
                "('come here'), ride the mouse cursor ('ride my cursor'), hop off the cursor "
                "('hop off'), wander between window ledges ('wander around' / 'stay still'), "
                "take a nap ('go to sleep' / 'wake up'), hide/show her overlay, toggle her "
                "cyberpunk mini music player, or express a mood emote badge ('mood')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": (
                            "One of: 'come_here', 'ride_cursor', 'hop_off', 'wander', "
                            "'stop_wander', 'sleep', 'wake', 'hide', 'show', 'toggle_mini_player', 'mood'."
                        ),
                        "enum": [
                            "come_here",
                            "ride_cursor",
                            "hop_off",
                            "wander",
                            "stop_wander",
                            "sleep",
                            "wake",
                            "hide",
                            "show",
                            "toggle_mini_player",
                            "mood",
                        ],
                    },
                    "wander_frequency": {
                        "type": "string",
                        "description": "When action is 'wander', how often: 'rarely', 'sometimes', 'often', or 'restless'. When action is 'mood', the mood type (e.g. 'happy', 'sparkle', 'thinking', 'surprised', 'sleepy', 'cool', 'neutral').",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_companion_mood",
            "description": "Set Alyssa's desktop companion mood and float an emote badge above her avatar.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mood": {
                        "type": "string",
                        "description": "The mood to express: 'happy', 'sparkle', 'thinking', 'surprised', 'sleepy', 'love', 'cool', 'music', 'neutral'.",
                        "enum": ["happy", "sparkle", "thinking", "surprised", "sleepy", "love", "cool", "music", "neutral"],
                    },
                },
                "required": ["mood"],
            },
        },
    },
]

FUNCTIONS = {
    "move_companion": move_companion,
    "set_companion_mood": set_companion_mood,
}
