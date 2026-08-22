from config import PROVIDER_SETTINGS as config

from .anthropic import _call_anthropic
from .gemini import _call_gemini
from .ollama import _call_ollama
from .openai import _call_custom_openai, _call_openai

_PROVIDER_API_KEYS = {
    "ollama": None,
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "custom_openai": "CUSTOM_API_KEY",
}


def _provider_for_tier(tier: str):
    fast_provider = config.LLM_PROVIDER
    if tier != "strong":
        return fast_provider, None

    strong_provider = getattr(config, "LLM_STRONG_PROVIDER", fast_provider)
    key_name = _PROVIDER_API_KEYS.get(strong_provider)
    if strong_provider not in _PROVIDER_API_KEYS or (
        key_name and not getattr(config, key_name, None)
    ):
        return fast_provider, strong_provider
    return strong_provider, None


def _call_model(
    messages,
    force_tools: bool = False,
    on_text_delta=None,
    cancel_event=None,
    provider=None,
    tools=None,
):
    """Dispatches to the selected LLM provider (defaulting to config.py), always
    returning the same normalized {"message": {"content", "tool_calls"}}
    shape so the rest of this module doesn't need to know which one it is.

    `force_tools` only affects Gemini - see _call_gemini for why. Other
    providers don't currently need it: this codebase hasn't observed the
    same "replies with plain text instead of calling a tool" laziness from
    them, so they're left on their normal default tool-calling behavior."""
    provider = provider or config.LLM_PROVIDER
    if provider == "gemini":
        return _call_gemini(messages, force_tools, on_text_delta, cancel_event, tools)
    if provider == "openai":
        return _call_openai(messages, on_text_delta, cancel_event, tools)
    if provider == "anthropic":
        return _call_anthropic(messages, on_text_delta, cancel_event, tools)
    if provider == "custom_openai":
        return _call_custom_openai(messages, on_text_delta, cancel_event, tools)
    return _call_ollama(messages, on_text_delta, cancel_event, tools)
