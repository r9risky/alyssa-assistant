"""Answers to fixed questions (who made you, which model, say/spell X) with no model call."""

import re
from functools import lru_cache


from config import (
    PROVIDER_SETTINGS as provider_config,
    UI_SETTINGS as ui_config,
)


_CREATOR_QUESTION_RE = re.compile(
    r"who\s+(?:made|make|created|create|built|build|developed|develop|"
    r"programmed|program|coded|code|designed|design)\s+(?:you|"
    + re.escape(ui_config.ASSISTANT_NAME.lower()) + r")\b"
    r"|who'?s?\s+(?:is\s+)?your\s+(?:creator|maker|developer|programmer|author)\b",
    re.IGNORECASE,
)


def _handle_creator_question(user_text: str):
    """Creator-attribution reply, or None if `user_text` is not that question."""
    creator = getattr(ui_config, "CREATOR_NAME", "")
    if not creator or not _CREATOR_QUESTION_RE.search(user_text or ""):
        return None
    return f"{creator} made me."


_MODEL_QUESTION_RE = re.compile(
    r"\bwhat\s+(?:ai\s+)?(?:model|llm)\s+(?:are\s+you(?:\s+(?:running|using))?|"
    r"do\s+you\s+(?:run\s+on|use)|is\s+(?:this|that)|you'?re\s+(?:running|using))\b"
    r"|\bwhich\s+(?:model|llm)\s+(?:are\s+you(?:\s+(?:running|using))?|do\s+you\s+(?:run\s+on|use))\b"
    r"|\bwhat'?s\s+your\s+model\b"
    r"|\bwhat\s+(?:ai\s+)?are\s+you\s+(?:running|powered)\s+(?:on|by)\b",
    re.IGNORECASE,
)


def _current_model_description() -> str:
    """Spoken description of the live-configured LLM (reads config each call)."""
    provider = provider_config.LLM_PROVIDER
    if provider == "ollama":
        return f"I'm running on {provider_config.OLLAMA_MODEL}, a local model through Ollama."
    if provider == "gemini":
        return f"I'm running on Google's {provider_config.GEMINI_MODEL}."
    if provider == "openai":
        return f"I'm running on OpenAI's {getattr(provider_config, 'OPENAI_MODEL', 'GPT')}."
    if provider == "anthropic":
        return f"I'm running on Anthropic's {getattr(provider_config, 'ANTHROPIC_MODEL', 'Claude')}."
    if provider == "custom_openai":
        model = getattr(provider_config, "CUSTOM_MODEL", "")
        return f"I'm running on {model}." if model else "I'm running on a custom model provider."
    return "I'm not sure which model I'm running on right now."


def _handle_model_question(user_text: str):
    """Current-model reply, or None if `user_text` is not that question."""
    if not _MODEL_QUESTION_RE.search(user_text or ""):
        return None
    return _current_model_description()


_ENGINE_QUESTION_RE = re.compile(
    r"\bwhat\s+(?:whisper\s+)?engine\s+(?:are\s+you(?:\s+(?:running|using))?|"
    r"do\s+you\s+(?:run\s+on|use)|is\s+(?:this|that|whisper))\b"
    r"|\bwhich\s+(?:whisper\s+)?engine\b"
    r"|\bare\s+you\s+(?:running|using)\s+(?:on\s+)?(?:the\s+)?(?:gpu|cpu)\b"
    r"|\b(?:is\s+)?whisper\s+(?:running|using)\s+(?:on\s+)?(?:the\s+)?(?:gpu|cpu)\b"
    r"|\bwhat\s+(?:speech\s+recognition|stt|transcription)\s+engine\b",
    re.IGNORECASE,
)


def _handle_engine_question(user_text: str):
    """Speech-engine status reply, or None if `user_text` is not that question."""
    if not _ENGINE_QUESTION_RE.search(user_text or ""):
        return None
    # Import transcribe only here so text-only reasoning does not depend on Whisper.
    import transcribe
    return transcribe.get_engine_status()


@lru_cache(maxsize=16)
def _get_strip_filler_patterns(assistant_name: str):
    return [
        re.compile(r"^(?:um+|uh+|so|okay|ok)[,]?\s+", re.IGNORECASE),
        re.compile(r"^(?:can|could|would)\s+you\s+(?:please\s+)?", re.IGNORECASE),
        re.compile(r"^please\s+", re.IGNORECASE),
        re.compile(r"^just\s+", re.IGNORECASE),
        re.compile(r"^(?:hey\s+)?" + re.escape(assistant_name) + r"[,]?\s+", re.IGNORECASE),
    ]


def _strip_leading_filler(text: str) -> str:
    """Strip polite/filler prefixes so 'can you please say X' matches 'say X'."""
    result = (text or "").strip()
    changed = True
    while changed:
        changed = False
        for pattern in _get_strip_filler_patterns(ui_config.ASSISTANT_NAME.lower()):
            new_result = pattern.sub("", result, count=1)
            if new_result != result:
                result = new_result
                changed = True
    return result.strip()


_SAY_AGAIN_RE = re.compile(
    r"^(?:say|repeat)\s+that(?:\s+again)?\.?$"
    r"|^say\s+(?:it\s+)?again\.?$"
    r"|^what\s+did\s+you\s+(?:just\s+)?say\.?$",
    re.IGNORECASE,
)


_SPELL_RE = re.compile(r"^spell(?:\s+out)?[:,]?\s+(.+?)\.?$", re.IGNORECASE)


_SAY_PHRASE_RE = re.compile(
    r"^(?:say|repeat(?:\s+after\s+me)?|echo)[:,]?\s+(.+)$", re.IGNORECASE
)


_SAY_PHRASE_EXCLUSIONS_RE = re.compile(
    r"\b(?:times|twice|slowly|quickly|loudly|softly|backwards|again|"
    r"open|close|launch|play|pause|type|press|search|volume|mute)\b",
    re.IGNORECASE,
)


def _handle_echo_request(user_text: str):
    """Echo/spell reply, or None to fall through to the model."""
    text = _strip_leading_filler(user_text)
    if not text:
        return None

    if _SAY_AGAIN_RE.match(text):
        from .dialogue import _conversation_history  # dialogue owns the history

        for turn in reversed(_conversation_history):
            if turn.get("role") == "assistant" and turn.get("content"):
                return turn["content"]
        return None  # nothing to repeat yet - let the model handle it

    spell_match = _SPELL_RE.match(text)
    if spell_match:
        word = spell_match.group(1).strip().strip("\"'")
        if word and " " not in word and len(word) <= 40:
            return ", ".join(list(word.upper()))
        return None  # not a single word - let the model interpret it

    say_match = _SAY_PHRASE_RE.match(text)
    if say_match:
        phrase = say_match.group(1).strip().strip("\"'")
        if not phrase or _SAY_PHRASE_EXCLUSIONS_RE.search(phrase):
            return None
        # Trailing "?" is usually from the wrapper ("can you say X?"), not X.
        phrase = re.sub(r"[?.!]+$", "", phrase).strip()
        if not phrase:
            return None
        phrase = phrase[0].upper() + phrase[1:]
        return phrase + "."

    return None
