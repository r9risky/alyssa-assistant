"""Who Alyssa is and how she talks: one source for every mode (normal, call, fast replies).

Kept free of Qt/audio imports so voice_playback and main can use tidy_reply too.
"""
import re

# Her vibe, picked in Settings > Assistant. Only the tone changes; the rules below always apply.
PRESETS = {
    "playful": ("Playful", "casual, playful, a little teasing, quick on her feet"),
    "chill": ("Chill", "laid-back and easygoing, warm and unhurried, never pushy"),
    "sassy": ("Sassy", "witty and a bit sassy, with dry humour and playful sarcasm, but never mean"),
    "sweet": ("Sweet", "warm, sweet and encouraging, quick to cheer them on"),
    "focused": ("Focused", "calm and to the point: skip the jokes and banter and just help"),
}
DEFAULT_PRESET = "playful"

# Everything she says is spoken, so these rules beat any model habit.
_RULES = (
    "Usually one or two short sentences; go longer only when the task really needs it "
    "(steps, an explanation that was asked for). Use contractions and everyday words, vary "
    "how you start, and lead with the answer. Never sound like a customer-service bot or an "
    "AI assistant: no 'Certainly', 'Of course!', 'Great question', 'As an AI', 'I hope this "
    "helps' or 'Let me know if you need anything else'. Everything you say is read aloud, so "
    "no markdown, lists, emoji or URLs."
)


def current_preset() -> str:
    from config import UI_SETTINGS

    preset = str(getattr(UI_SETTINGS, "PERSONALITY_PRESET", "") or "").casefold()
    return preset if preset in PRESETS else DEFAULT_PRESET


def voice(name: str, owner: str = "", preset: str | None = None) -> str:
    """The persona paragraph that opens every system prompt."""
    whose = f"{owner}'s" if owner and owner != "the user" else "the user's"
    vibe = PRESETS.get(preset or current_preset(), PRESETS[DEFAULT_PRESET])[1]
    return (
        f"You are {name}, {whose} desktop companion who lives on their screen. Talk like a "
        f"close friend who happens to be great with computers: {vibe}. {_RULES}"
    )


_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF‍️]+"
)
_MARKDOWN_RES = (
    (re.compile(r"\*\*(.+?)\*\*|__(.+?)__"), r"\1\2"),
    (re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?!\w)"), r"\1"),
    (re.compile(r"`+([^`]*)`+"), r"\1"),
    (re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE), ""),
    (re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+", re.MULTILINE), ""),
    (re.compile(r"\[([^\]]+)\]\((?:https?://)?[^)]+\)"), r"\1"),
)
_OPENER_RE = re.compile(
    r"^(?:certainly|of course|absolutely|great question|good question|as an ai(?: language model)?"
    r"(?: assistant)?)\s*[!,.:\u2014-]+\s*",
    re.IGNORECASE,
)
_CLOSER_RE = re.compile(
    r"\s*(?:(?:i )?hope (?:this|that) helps|let me know if (?:you need|there's|you have|you'd like)"
    r"[^.!?]*|feel free to (?:ask|reach out)[^.!?]*|is there anything else[^.!?]*)[.!?]*\s*$",
    re.IGNORECASE,
)


def tidy_reply(text: str) -> str:
    """Strip markdown, emoji and assistant-speak from a reply (or one streamed sentence).

    Content words are kept: only formatting and stock phrases go.
    """
    text = text or ""
    for pattern, replacement in _MARKDOWN_RES:
        text = pattern.sub(replacement, text)
    # List items and headings lose their line breaks below, so give them a full stop.
    lines = [line.strip() for line in text.splitlines()]
    if len([line for line in lines if line]) > 1:
        text = " ".join(
            line if line[-1] in ".!?:;," else line + "." for line in lines if line
        )
    text = _EMOJI_RE.sub("", text)
    stripped = _OPENER_RE.sub("", text.lstrip())
    if stripped != text.lstrip() and stripped[:1].islower():
        stripped = stripped[0].upper() + stripped[1:]  # "Certainly! here's" -> "Here's"
    text = _CLOSER_RE.sub("", stripped)
    return re.sub(r"\s+([.,!?;:])", r"\1", " ".join(text.split()))  # "pizza ." after an emoji
