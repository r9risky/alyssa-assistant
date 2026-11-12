"""Runtime tool registry shared by orchestration and LLM providers."""

import re

import actions

from .tool_catalog import BASE_TOOLS


TOOLS = BASE_TOOLS + actions.PLUGIN_TOOLS

_CORE_TOOL_NAMES = {
    "open_app", "type_text", "press_keys", "open_url", "get_datetime", "reset_conversation",
}
_IGNORED_WORDS = {
    "about", "and", "are", "can", "check", "could", "find", "for", "from", "get",
    "give", "have", "how", "into", "look", "make", "next", "open", "please", "run", "search",
    "set", "show", "something", "start", "stop", "tell", "that", "the", "this", "turn",
    "use", "want", "what", "when", "where", "which", "with", "would", "your",
    # Small talk and filler: these match many descriptions and pull in unrelated tools.
    # "time" is safe because get_datetime is always sent.
    "you", "yours", "me", "my", "mine", "our", "him", "her", "his", "them", "they", "their",
    "its", "was", "were", "been", "being", "did", "does", "doing", "done", "will",
    "just", "really", "very", "much", "too", "also", "like", "some", "any", "all", "one", "out",
    "now", "then", "than", "there", "here", "not", "don", "yes", "yeah", "okay", "hey",
    "hello", "thank", "thanks", "good", "morning", "afternoon", "evening", "night",
    "why", "know", "think", "need", "let", "again", "user",
    "alyssa", "time",
}


def _keywords(text: str):
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {
        word[:-1] if len(word) > 4 and word.endswith("s") else word
        for word in words
        if len(word) > 2 and word not in _IGNORED_WORDS
    }


def select_tools(user_text: str, conversation=()):
    """Returns a per-request schema subset without mutating the registry."""
    recent_users = " ".join(
        str(message.get("content", ""))
        for message in conversation[-4:]
        if message.get("role") == "user"
    )
    request_words = _keywords(f"{recent_users} {user_text}")
    selected = []
    for tool in TOOLS:
        function = tool.get("function", {})
        name = function.get("name", "")
        searchable = _keywords(f"{name.replace('_', ' ')} {function.get('description', '')}")
        if name in _CORE_TOOL_NAMES or request_words & searchable:
            selected.append(tool)
    return selected


def refresh_tools() -> None:
    """Refresh plugin schemas in place so provider references stay valid."""
    TOOLS[:] = BASE_TOOLS + actions.PLUGIN_TOOLS
