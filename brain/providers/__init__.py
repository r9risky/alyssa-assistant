import config

from .anthropic import _call_anthropic
from .gemini import _call_gemini
from .ollama import _call_ollama
from .openai import _call_custom_openai, _call_openai

def _call_model(messages, force_tools: bool = False, on_text_delta=None, cancel_event=None):
    """Dispatches to whichever LLM provider config.py is set to, always
    returning the same normalized {"message": {"content", "tool_calls"}}
    shape so the rest of this module doesn't need to know which one it is.

    `force_tools` only affects Gemini - see _call_gemini for why. Other
    providers don't currently need it: this codebase hasn't observed the
    same "replies with plain text instead of calling a tool" laziness from
    them, so they're left on their normal default tool-calling behavior."""
    provider = config.LLM_PROVIDER
    if provider == "gemini":
        return _call_gemini(messages, force_tools, on_text_delta, cancel_event)
    if provider == "openai":
        return _call_openai(messages, on_text_delta, cancel_event)
    if provider == "anthropic":
        return _call_anthropic(messages, on_text_delta, cancel_event)
    if provider == "custom_openai":
        return _call_custom_openai(messages, on_text_delta, cancel_event)
    return _call_ollama(messages, on_text_delta, cancel_event)
