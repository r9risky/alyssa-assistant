"""Watch-Along Mode plugin for Alyssa.

Inspired by Capri Desktop's `WatchAlong.cs`:
Allows Alyssa to watch a video, film, Twitch/YouTube stream, or show together with the user:
- Activates on "watch this video with me", "let's watch together", "react to this".
- Stops on "stop watching", "we're done watching".
- Maintains a rolling memory of dialogue/events and reacts sparingly in-character
  with witty commentary without talking over the movie constantly.
"""

from __future__ import annotations

import collections
import logging
import re
import time
from typing import Any, Deque, Dict, Optional

from overlay.widgets import get_active_companion_window, run_on_gui_thread

logger = logging.getLogger("plugins.watch_along")


class WatchAlongState:
    """Session state for watch-along mode."""

    def __init__(self):
        self.active: bool = False
        self.title: str = "Video / Stream"
        self.started_at: float = 0.0
        self.last_reaction_at: float = 0.0
        self.reaction_count: int = 0
        self.recent_lines: Deque[str] = collections.deque(maxlen=24)
        self.reaction_interval: float = 25.0  # Chime in at most once every ~25 seconds

    def start(self, title: Optional[str] = None) -> None:
        self.active = True
        self.title = title or "Video / Stream"
        self.started_at = time.time()
        self.last_reaction_at = time.time()
        self.reaction_count = 0
        self.recent_lines.clear()

    def stop(self) -> None:
        self.active = False

    def add_line(self, line: str) -> None:
        if line and line.strip():
            self.recent_lines.append(line.strip())

    def can_react(self) -> bool:
        if not self.active:
            return False
        return (time.time() - self.last_reaction_at) >= self.reaction_interval


# Global singleton watch-along state
WATCH_ALONG_SESSION = WatchAlongState()


def start_watch_along(args: Optional[Dict[str, Any]] = None, **kwargs) -> str:
    """Start watching a video, show, or stream together."""
    args = {**(args or {}), **kwargs}  # the brain calls tools as func(**model_args)
    title = args.get("title") or "the video"

    WATCH_ALONG_SESSION.start(title)

    win = get_active_companion_window()
    if win and hasattr(win, "bubble") and win.bubble:
        run_on_gui_thread(lambda: win.bubble.show_message("Got my popcorn! Watching along with you. Let's see what happens."))

    return f"Watch-along mode started for '{title}'! Alyssa is watching and will share witty reactions sparingly."


def stop_watch_along(args: Optional[Dict[str, Any]] = None, **kwargs) -> str:
    """Stop watch-along mode."""
    if not WATCH_ALONG_SESSION.active:
        return "Watch-along mode is not currently active."

    duration = int(time.time() - WATCH_ALONG_SESSION.started_at)
    comments = WATCH_ALONG_SESSION.reaction_count
    WATCH_ALONG_SESSION.stop()

    dur_str = f"{duration}s" if duration < 60 else f"{duration // 60}m {duration % 60}s"

    win = get_active_companion_window()
    if win and hasattr(win, "bubble") and win.bubble:
        run_on_gui_thread(lambda: win.bubble.show_message("Fun watch! Thanks for sharing that with me."))

    return f"Watch-along ended. Watched for {dur_str} with {comments} comments."


def watch_along_react(args: Optional[Dict[str, Any]] = None, **kwargs) -> str:
    """Generate or log an in-character witty reaction to what just occurred."""
    args = {**(args or {}), **kwargs}  # the brain calls tools as func(**model_args)
    comment = args.get("comment", "").strip()

    if not WATCH_ALONG_SESSION.active:
        return "Watch-along mode is not active. Start it first with start_watch_along."

    WATCH_ALONG_SESSION.last_reaction_at = time.time()
    WATCH_ALONG_SESSION.reaction_count += 1

    reaction = comment or "Wait, did they really just do that?! Love it."

    win = get_active_companion_window()
    if win and hasattr(win, "bubble") and win.bubble:
        run_on_gui_thread(lambda: win.bubble.show_message(reaction))

    return f"Reacted: {reaction}"


def watch_along_status(args: Optional[Dict[str, Any]] = None, **kwargs) -> str:
    """Check watch-along mode status."""
    if not WATCH_ALONG_SESSION.active:
        return "Watch-along is currently inactive. Say 'watch this video with me' to begin!"

    elapsed = int(time.time() - WATCH_ALONG_SESSION.started_at)
    return (
        f"Watch-along is ACTIVE on '{WATCH_ALONG_SESSION.title}'.\n"
        f"Time elapsed: {elapsed}s\n"
        f"Comments shared: {WATCH_ALONG_SESSION.reaction_count}\n"
        f"Say 'stop watching' when finished."
    )


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "start_watch_along",
            "description": "Start watch-along mode to watch a video, film, YouTube clip, or stream together with Alyssa.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Title or description of what you're watching (e.g. 'Cyberpunk anime', 'Minecraft speedrun').",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stop_watch_along",
            "description": "Stop watch-along mode when finished watching.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "watch_along_react",
            "description": "Trigger an in-character reaction/commentary line during watch-along mode.",
            "parameters": {
                "type": "object",
                "properties": {
                    "comment": {
                        "type": "string",
                        "description": "Reaction commentary line.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "watch_along_status",
            "description": "Get current status, elapsed time, and comments count for watch-along mode.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
]

_WATCH_PHRASES = {
    f"{verb} {thing}{tail}"
    for verb in ("watch", "let's watch", "lets watch", "come watch", "react to")
    for thing in ("this", "that", "this video", "this stream", "this movie", "this show")
    for tail in ("", " with me", " together")
    if tail or verb != "watch"  # plain "watch this" is too vague on its own
}


def handle_intent(cleaned: str, text: str = "") -> Optional[str]:
    """'watch this with me' / 'stop watching' without a model call."""
    import config

    if cleaned.rstrip("!.?") in _WATCH_PHRASES:
        if not getattr(config, "WATCH_ALONG_ENABLED", True):
            return "Watch-along mode is currently disabled in Settings."
        return start_watch_along({"title": "the screen"})
    if WATCH_ALONG_SESSION.active and re.search(
        r"^(?:stop watching|end watch[- ]along|we'?re done watching)$", cleaned
    ):
        return stop_watch_along({})
    return None


FUNCTIONS = {
    "start_watch_along": start_watch_along,
    "stop_watch_along": stop_watch_along,
    "watch_along_react": watch_along_react,
    "watch_along_status": watch_along_status,
}
