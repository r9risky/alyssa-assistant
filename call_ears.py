"""Call Mode Audio Ingestion, Security Firewall, and Session Manager.

Inspired by Capri Desktop's `CallEars.cs` and `CallMode.cs`:
- Separates physical microphone audio (`You`) from desktop/call audio (`Call`).
- Strict Security Gate: call audio lines are quarantined with prefix `(someone on call) `,
  cannot wake the assistant, cannot execute actions, and hold friend requests for user confirmation.
- Errand resolution: handles delegation like "launch whatever they're playing" or "play the song they want".
- Provides real-time audio energy meters (You, Her, Call) for the animated Call HUD rings.
"""

from __future__ import annotations

import random
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
            import config as root_config
            root_config.CALL_MODE_ENABLED = True
        self._notify_state_change()

    def end_call(self):
        with self._lock:
            self.enabled = False
            self.errand = None
            self.pending_request = None
            self.level_you = 0.0
            self.level_her = 0.0
            self.level_call = 0.0
            import config as root_config
            root_config.CALL_MODE_ENABLED = False
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
    import config as root_config
    return bool(
        getattr(root_config, "CALL_MODE_ENABLED", False)
        or getattr(config, "CALL_MODE_ENABLED", False)
        or _session_instance.enabled
    )


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


# ---------------------------------------------------------------------------
# Friends talking in the Discord call (see discord_bot.FriendListener)
# ---------------------------------------------------------------------------

_recent_call_lines: list[str] = []
_MAX_CALL_LINES = 8
_last_reply_at = 0.0
REPLY_COOLDOWN_SECONDS = 3.0
# After she talks in the call, people can answer her without saying her name first.
FOLLOW_UP_SECONDS = 20.0
_MAX_FOLLOW_UPS = 2  # then they need her name again, so game sound can't keep her chatting
_FOLLOW_UP_MIN_WORDS = 3  # "yeah" / "lol" alone isn't worth a reply
_follow_up_until = 0.0
_follow_ups_left = 0


def recent_call_lines() -> list[str]:
    return list(_recent_call_lines)


def open_follow_up(now=None) -> None:
    """She just spoke in the call: answers for the next little while count as talking to her."""
    global _follow_up_until, _follow_ups_left
    _follow_up_until = (time.time() if now is None else now) + FOLLOW_UP_SECONDS
    _follow_ups_left = _MAX_FOLLOW_UPS


def _owner_name() -> str:
    import config as root_config

    return str(getattr(root_config, "USER_NICKNAME", "") or "").strip() or "my person"


def _join_names(names) -> str:
    names = [n for n in names if n]
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def greeting(people=(), message: str = "") -> str:
    """What she says when she joins a call: short, casual, and different each time."""
    import config as root_config

    me = getattr(root_config, "ASSISTANT_NAME", "Alyssa")
    owner = _owner_name()
    people = [p for p in people if p and p.casefold() != owner.casefold()][:3]
    hi = random.choice(["Hey", "Hi", "Hey hey", "Heyy"])
    hello = f"{hi} {_join_names(people)}!" if people else random.choice(
        ["Hey everyone!", "Hi all!", "Hey hey, hi everyone!"]
    )
    if message:
        body = random.choice([
            f"{owner} asked me to tell you: {message.strip().rstrip('.!')}.",
            f"Message from {owner}: {message.strip().rstrip('.!')}.",
            f"{owner} sent me with a message: {message.strip().rstrip('.!')}.",
        ])
    else:
        body = random.choice([
            f"It's {me}, {owner} brought me along. What are we up to?",
            f"{me} here, {owner} sent me in to hang out. What are we playing?",
            f"It's {me}. {owner} said you guys were here, so here I am.",
            f"{me} here. Don't mind me, just say my name if you need me.",
        ])
    return f"{hello} {body}"


_greeted: dict[str, float] = {}
GREET_AGAIN_AFTER_SECONDS = 600.0


def greet_joiner(name: str, *, speak_fn=None, now=None) -> Optional[str]:
    """Someone joined her voice channel: say hi (once per person every 10 minutes, no AI call)."""
    now = time.time() if now is None else now
    key = (name or "").casefold()
    if not key or now - _greeted.get(key, -1e9) < GREET_AGAIN_AFTER_SECONDS:
        return None
    _greeted[key] = now
    line = random.choice([f"Hey {name}!", f"Oh hey {name}, welcome in.", f"{name}! Hi.", f"Hey {name}, good timing."])
    _say(line, speak_fn)
    return line


def _say(text: str, speak_fn=None) -> None:
    import config as root_config

    get_call_session().post_caption(getattr(root_config, "ASSISTANT_NAME", "Alyssa"), text)
    if speak_fn is not None:
        speak_fn(text)
        return
    import voice_playback

    # Wait for her to finish any reply first; overlapping voices sounded doubled.
    with voice_playback.speech_lock:
        voice_playback.speak(text)


def _addressed_to_her(text: str, now: float):
    """The words meant for her, or None. Her name anywhere counts; so does an answer right
    after she spoke (a couple of times, and only real sentences)."""
    global _follow_ups_left
    import nameutil

    span = nameutil.find_name_span(text)
    if span is not None:
        return nameutil.strip_name_at_span(text, span) or text
    if now < _follow_up_until and _follow_ups_left > 0 and len(text.split()) >= _FOLLOW_UP_MIN_WORDS:
        _follow_ups_left -= 1
        return text
    return None


def handle_friend_line(speaker: str, text: str, *, reply_fn=None, speak_fn=None, now=None) -> Optional[str]:
    """React to one transcribed line from a friend in the call. Returns what she said, if anything.

    Token budget: lines that aren't for her only go into a short local memory of the
    conversation (no AI call). Game/song requests are parsed locally and wait for the user's
    yes. Only a line that names her (or answers her right after she spoke), and isn't a
    request, costs one small chat-only AI call.
    """
    global _last_reply_at

    text = sanitize_call_transcript(text).removeprefix(CALL_SPEAKER_TAG).strip()
    if not text:
        return None
    session = get_call_session()
    session.post_caption(speaker, text)
    context = recent_call_lines()
    _recent_call_lines.append(f"{speaker}: {text}")
    del _recent_call_lines[:-_MAX_CALL_LINES]

    now = time.time() if now is None else now
    if now - _last_reply_at < REPLY_COOLDOWN_SECONDS:
        return None
    asked = _addressed_to_her(text, now)
    if asked is None:
        return None
    _last_reply_at = now

    import config as root_config

    owner = _owner_name()
    request = parse_call_friend_intent(asked) if getattr(root_config, "CALL_FRIENDS_CAN_ASK", True) else None
    if request is not None:
        request.who = speaker
        session.pending_request = request
        what = request.arguments.get("app_name") or request.arguments.get("query") or "that"
        verb = "open" if request.action == "open_app" else "play"
        reply = f"{speaker} wants me to {verb} {what}. {owner}, should I? Say yes or no."
    else:
        if reply_fn is None:
            from brain.dialogue import reply_to_call as reply_fn
        reply = reply_fn(speaker, asked, context)
    if reply:
        _recent_call_lines.append(f"{getattr(root_config, 'ASSISTANT_NAME', 'Alyssa')}: {reply}")
        del _recent_call_lines[:-_MAX_CALL_LINES]
        _say(reply, speak_fn)
        open_follow_up(now)
    return reply
