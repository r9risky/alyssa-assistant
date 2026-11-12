"""Desktop Chaos & Pranks plugin for Alyssa.

Inspired by Capri Desktop:
- 'trigger_fake_crash': Harmless 5-second CHOOM_OVERFLOW blue screen with quick Esc exit.
- 'spawn_clones': Spawns harmless cheeky Alyssa clone cards on screen.
- 'clear_chaos_pranks': Instantly closes all prank widgets.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from actions.desktop_chaos import dismiss_all_pranks, show_fake_crash, spawn_clone_swarm


def trigger_fake_crash(args: Optional[Dict[str, Any]] = None, **kwargs) -> str:
    """Show the harmless 5-second CHOOM_OVERFLOW fake BSOD."""
    return show_fake_crash()


def spawn_clones(args: Optional[Dict[str, Any]] = None, **kwargs) -> str:
    """Spawn harmless wandering Alyssa clone notes across the screen."""
    args = {**(args or {}), **kwargs}  # the brain calls tools as func(**model_args)
    count = int(args.get("count", 4))
    return spawn_clone_swarm(count=count)


def clear_chaos_pranks(args: Optional[Dict[str, Any]] = None, **kwargs) -> str:
    """Dismiss any active chaos pranks or clones."""
    return dismiss_all_pranks()


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "trigger_fake_crash",
            "description": "Trigger the harmless 5-second CHOOM_OVERFLOW fake blue screen prank (press Esc or click to dismiss).",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "spawn_clones",
            "description": "Spawn harmless cheeky Alyssa clone notes across the desktop.",
            "parameters": {
                "type": "object",
                "properties": {
                    "count": {
                        "type": "integer",
                        "description": "Number of clones to spawn (1 to 10). Default 4.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clear_chaos_pranks",
            "description": "Dismiss and close any active fake crash screens or desktop clones.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
]

FUNCTIONS = {
    "trigger_fake_crash": trigger_fake_crash,
    "spawn_clones": spawn_clones,
    "clear_chaos_pranks": clear_chaos_pranks,
}
