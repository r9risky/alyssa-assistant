"""Game Co-Pilot plugin for Alyssa.

Equips Alyssa with:
- Running game detection & genre wisdom (tactical shooter, survival, RPG, soulslike).
- "Clip that!" instant 30-second rolling replay buffer recording to MP4 in Videos/Alyssa/.
- Game controls cheat-sheet and persistent keybind memory.
- Floating cyberpunk Game HUD overlay toggle.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from actions.clips import save_replay_clip
from actions.game_sense import (
    detect_active_game,
    get_game_controls,
    get_game_notes,
    save_game_controls,
)
from overlay.widgets import get_active_companion_window

logger = logging.getLogger("plugins.game_copilot")


def detect_game(args: Optional[Dict[str, Any]] = None) -> str:
    """Detect currently running game and return co-pilot tips."""
    detected = detect_active_game()
    if not detected:
        return "No known game detected in running processes. You can specify a game by name, or ask me to open one."

    game_name = detected["name"]
    genre = detected["genre"]
    tips = detected["tips"]

    # If companion window has game_hud, update it
    win = get_active_companion_window()
    if win and hasattr(win, "game_hud") and win.game_hud:
        win.game_hud.set_game(game_name)
        win.game_hud.say(f"Ready for {game_name}. {genre} mode engaged.", mood="focus")
        win.game_hud.show()

    notes = get_game_notes(game_name)
    extra_notes = f"\nYour notes for {game_name}:\n{notes}" if notes else ""

    return (
        f"Active game detected: {game_name} ({genre})\n"
        f"Tactical tips: {tips}{extra_notes}\n"
        f"Game HUD is ready. Say 'clip that!' anytime to save recent gameplay."
    )


def clip_gameplay(args: Optional[Dict[str, Any]] = None) -> str:
    """Save the last N seconds of gameplay as an MP4 clip."""
    args = args or {}
    seconds = int(args.get("seconds", 30))
    game = args.get("game")

    result = save_replay_clip(seconds=seconds, game_name=game)
    win = get_active_companion_window()

    if result.get("success"):
        path = result.get("path")
        msg = result.get("message")
        if win and hasattr(win, "game_hud") and win.game_hud:
            win.game_hud.say("Clipped that! Saved to Videos/Alyssa.", mood="happy")
        return f"Clip recorded! {msg} (Path: {path})"
    else:
        err = result.get("message", "Unknown error")
        if win and hasattr(win, "game_hud") and win.game_hud:
            win.game_hud.say("Couldn't grab the clip yet. Replay buffer was empty.", mood="surprise")
        return f"Could not clip gameplay: {err}"


def view_game_controls(args: Optional[Dict[str, Any]] = None) -> str:
    """View saved or builtin keybinds/controls for a game."""
    args = args or {}
    game = args.get("game")
    if not game:
        detected = detect_active_game()
        game = detected["name"] if detected else "cyberpunk 2077"

    controls = get_game_controls(game)
    if not controls:
        return f"No controls found for '{game}'. You can teach me with save_game_controls."

    formatted = "\n".join([f"- **{k}**: {v}" for k, v in controls.items()])
    return f"Keybinds & Controls for {game.title()}:\n{formatted}"


def teach_game_controls(args: Optional[Dict[str, Any]] = None) -> str:
    """Save user-taught controls or keybinds for a game."""
    args = args or {}
    game = args.get("game", "").strip()
    text = args.get("text", "").strip()

    if not game:
        detected = detect_active_game()
        if detected:
            game = detected["name"]
        else:
            return "Please provide the name of the game."

    if not text:
        return "Please provide the controls text to save (e.g. 'Jump: Space, Dash: Shift')."

    file_path = save_game_controls(game, text)

    win = get_active_companion_window()
    if win and hasattr(win, "game_hud") and win.game_hud:
        win.game_hud.set_game(game)
        win.game_hud.say(f"Saved custom controls for {game}.", mood="happy")

    return f"Saved controls for {game} to {file_path}."


def toggle_game_hud(args: Optional[Dict[str, Any]] = None) -> str:
    """Toggle the floating cyberpunk Game HUD overlay."""
    args = args or {}
    show = args.get("show", True)
    game = args.get("game")

    win = get_active_companion_window()
    if not win or not hasattr(win, "game_hud") or not win.game_hud:
        return "Game HUD widget is not available in the current companion window."

    if show:
        if game:
            win.game_hud.set_game(game)
        win.game_hud.show()
        return "Game HUD overlay shown."
    else:
        win.game_hud.hide()
        return "Game HUD overlay hidden."


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "detect_game",
            "description": "Detect active running game, get genre wisdom, tactical advice, and activate the gaming co-pilot.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clip_gameplay",
            "description": "Clip recent gameplay (default 30s) and save to Videos/Alyssa as an MP4 video file ('clip that!').",
            "parameters": {
                "type": "object",
                "properties": {
                    "seconds": {
                        "type": "integer",
                        "description": "Duration in seconds to clip (e.g. 15, 30, 60). Default 30.",
                    },
                    "game": {
                        "type": "string",
                        "description": "Optional name of the game being clipped.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "view_game_controls",
            "description": "Look up keybind cheat-sheet and controls for a game (e.g. Cyberpunk 2077, Minecraft, CS2).",
            "parameters": {
                "type": "object",
                "properties": {
                    "game": {
                        "type": "string",
                        "description": "Game name (e.g. 'Cyberpunk 2077', 'Minecraft'). If omitted, checks active game.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "teach_game_controls",
            "description": "Save custom controls, keybinds, or notes for a game in Alyssa's persistent game memory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "game": {
                        "type": "string",
                        "description": "Name of the game.",
                    },
                    "text": {
                        "type": "string",
                        "description": "Controls to save (e.g. 'Dodge: Alt, Heal: H, Ultimate: Z').",
                    },
                },
                "required": ["game", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "toggle_game_hud",
            "description": "Show or hide the floating cyberpunk Game HUD overlay on screen.",
            "parameters": {
                "type": "object",
                "properties": {
                    "show": {
                        "type": "boolean",
                        "description": "True to show, False to hide.",
                    },
                    "game": {
                        "type": "string",
                        "description": "Optional game name to display on the HUD.",
                    },
                },
            },
        },
    },
]

FUNCTIONS = {
    "detect_game": detect_game,
    "clip_gameplay": clip_gameplay,
    "view_game_controls": view_game_controls,
    "teach_game_controls": teach_game_controls,
    "toggle_game_hud": toggle_game_hud,
}
