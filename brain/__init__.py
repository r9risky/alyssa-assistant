"""LLM integration and tool dispatch for Alyssa."""

from . import dialogue
from .dialogue import (
    TOOLS,
    clear_conversation_history,
    handle_command,
    has_pending_power_confirmation,
    reload_plugin_tools,
    warm_up_connections,
)
from .vision import describe_screen_with_vision, locate_screen_element_with_vision

__all__ = [
    "TOOLS",
    "clear_conversation_history",
    "describe_screen_with_vision",
    "handle_command",
    "has_pending_power_confirmation",
    "locate_screen_element_with_vision",
    "reload_plugin_tools",
    "warm_up_connections",
]
