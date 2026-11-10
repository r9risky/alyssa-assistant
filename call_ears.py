"""Call Mode Audio Ingestion, Security Firewall, and Session Manager.

Inspired by Capri Desktop's `CallEars.cs` and `CallMode.cs`:
- Separates physical microphone audio (`You`) from desktop/call audio (`Call`).
- Strict Security Gate: call audio lines are quarantined with prefix `(someone on call) `,
  cannot wake the assistant, cannot execute actions, and hold friend requests for user confirmation.
- Errand resolution: handles delegation like "launch whatever they're playing" or "play the song they want".
- Provides real-time audio energy meters (You, Her, Call) for the animated Call HUD rings.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Callable, Optional

import numpy as np

from config import AUDIO_SETTINGS as config

# Tag prefix attached to transcripts originating from the call/loopback stream
CALL_SPEAKER_TAG = "(someone on call) "


class CallErrand:
    """Delegated request left to people on the call (e.g. game or song choice)."""

    def __init__(self, action: str = "open_app", ask: str = "", ttl_seconds: float = 120.0):
        self.action = action  # "open_app" or "play_music"
        self.ask = ask
        self.expires_at = time.time() + ttl_seconds

    @property
    def is_expired(self) -> bool:
        return time.time() >= self.expires_at


class FriendRequest:
    """Request made by a friend on the call, held for the user's explicit confirmation."""

    def __init__(self, who: str, action: str, arguments: dict, raw_text: str, ttl_seconds: float = 120.0):
        self.who = who
        self.action = action
        self.arguments = arguments
        self.raw_text = raw_text
        self.expires_at = time.time() + ttl_seconds
        self.confirmed = False
        self.requested_action = arguments.get("app_name") or arguments.get("query") or action

    @property
    def is_expired(self) -> bool:
        return time.time() >= self.expires_at

    @property
    def is_pending(self) -> bool:
        return not self.confirmed and not self.is_expired

    def confirm(self, approver: str = "user") -> bool:
        if approver.lower() in ("user", "self", "me", "owner"):
            self.confirmed = True
            return True
        return False


class CallSession:
    """Coordinates the active call/meeting state, security boundaries, and HUD updates."""

    def __init__(self):
        self._lock = threading.RLock()
        self.enabled = False
        self.app_name = "Discord"
        self.channel_name = ""
        self.connected_at = 0.0
        self.errand: Optional[CallErrand] = None
        self.pending_request: Optional[FriendRequest] = None

        # Live audio meter levels (0.0 to 1.0)
        self.level_you = 0.0
        self.level_her = 0.0
        self.level_call = 0.0

        # Captions
        self.last_speaker = ""
        self.last_caption = ""
        self.caption_time = 0.0

        # UI Callbacks
        self._on_state_change: list[Callable[[], None]] = []
        self._on_caption_callbacks: list[Callable[[str, str], None]] = []
        self._on_levels_callbacks: list[Callable[[float, float, float], None]] = []

    def start_call(self, app_name: str = "Discord", channel_name: str = ""):
        with self._lock:
            self.enabled = True
            self.app_name = app_name or "Discord"
            self.channel_name = channel_name
            self.connected_at = time.time()
            self.errand = None
            self.pending_request = None
            config.CALL_MODE_ENABLED = True
        self._notify_state_change()

    def end_call(self):
        with self._lock:
            self.enabled = False
            self.errand = None
            self.pending_request = None
            self.level_you = 0.0
            self.level_her = 0.0
            self.level_call = 0.0
            config.CALL_MODE_ENABLED = False
        self._notify_state_change()

    def set_errand(self, action: str, ask: str = "", ttl_seconds: float = 120.0):
        with self._lock:
            self.errand = CallErrand(action=action, ask=ask, ttl_seconds=ttl_seconds)

    def cancel_errand(self):
        with self._lock:
            self.errand = None

    def post_caption(self, speaker: str, text: str):
        with self._lock:
            self.last_speaker = speaker
            self.last_caption = text
            self.caption_time = time.time()
        for cb in self._on_caption_callbacks:
            try:
                cb(speaker, text)
            except Exception:
                pass

    def update_levels(self, you: float, her: float, call: float):
        with self._lock:
            self.level_you = max(0.0, min(1.0, float(you)))
            self.level_her = max(0.0, min(1.0, float(her)))
            self.level_call = max(0.0, min(1.0, float(call)))
        for cb in self._on_levels_callbacks:
            try:
                cb(self.level_you, self.level_her, self.level_call)
            except Exception:
                pass

    def add_state_listener(self, callback: Callable[[], None]):
        if callback not in self._on_state_change:
            self._on_state_change.append(callback)

    def add_caption_listener(self, callback: Callable[[str, str], None]):
        if callback not in self._on_caption_callbacks:
            self._on_caption_callbacks.append(callback)

    def add_levels_listener(self, callback: Callable[[float, float, float], None]):
        if callback not in self._on_levels_callbacks:
            self._on_levels_callbacks.append(callback)

    def _notify_state_change(self):
        for cb in self._on_state_change:
            try:
                cb()
            except Exception:
                pass

    @property
    def is_active(self) -> bool:
        return self.enabled

    @property
    def duration_seconds(self) -> float:
        return self.call_duration

    def add_caption(self, speaker: str, text: str):
        self.post_caption(speaker, text)

    @property
    def call_duration(self) -> float:
        if not self.enabled or self.connected_at == 0.0:
            return 0.0
        return max(0.0, time.time() - self.connected_at)


_session_instance = CallSession()


def get_call_session() -> CallSession:
    return _session_instance


def is_call_mode_active() -> bool:
    return bool(getattr(config, "CALL_MODE_ENABLED", False) or _session_instance.enabled)


def sanitize_call_transcript(text: str, is_from_call: bool = False) -> str:
    """Strict security tagging: mark any audio coming from Discord/call so the LLM
    knows it is from a third party and cannot authorize sensitive PC actions.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return ""
    if (is_from_call or is_call_mode_active()) and not cleaned.startswith(CALL_SPEAKER_TAG):
        return f"{CALL_SPEAKER_TAG}{cleaned}"
    return cleaned


def parse_call_friend_intent(text: str) -> Optional[FriendRequest]:
    """Identify if a friend on the call asked for a game or song."""
    cleaned = text.strip()
    if cleaned.startswith(CALL_SPEAKER_TAG):
        cleaned = cleaned[len(CALL_SPEAKER_TAG):].strip()

    # e.g. "Alyssa play Cyberpunk", "launch Minecraft", "play Arknights"
    m_game = re.search(
        r"(?:launch|open|start|play)\s+(?:game\s+)?([a-zA-Z0-9\s:_-]{2,30})",
        cleaned,
        re.IGNORECASE,
    )
    if m_game:
        target = m_game.group(1).strip()
        if target.lower() not in ("a song", "music", "something", "the call", "call"):
            return FriendRequest(
                who="friend on call",
                action="open_app",
                arguments={"app_name": target},
                raw_text=text,
            )

    # e.g. "play some synthwave", "play song Blinding Lights"
    m_song = re.search(
        r"(?:play|put on)\s+(?:the\s+)?(?:song|track|music)\s+([a-zA-Z0-9\s:_-]{2,40})",
        cleaned,
        re.IGNORECASE,
    )
    if m_song:
        song = m_song.group(1).strip()
        return FriendRequest(
            who="friend on call",
            action="play_music",
            arguments={"query": song},
            raw_text=text,
        )

    return None
