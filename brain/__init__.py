"""LLM integration and tool dispatch for Alyssa."""

from . import dialogue
from .dialogue import (
    clear_conversation_history,
    handle_command,
    has_pending_power_confirmation,
    reload_plugin_tools,
    warm_up_connections,
)
from .speculative import get_speculative_engine
from .tool_registry import TOOLS
from .vision import ask_vision, describe_screen_with_vision, locate_screen_element_with_vision

# One-way bridge: brain -> actions. Actions never import brain.
import actions as _actions

_actions.configure_brain_services(
    describe_screen=describe_screen_with_vision,
    locate_screen_element=locate_screen_element_with_vision,
    clear_conversation=clear_conversation_history,
    ask_vision=ask_vision,
)

__all__ = [
    "TOOLS",
    "clear_conversation_history",
    "describe_screen_with_vision",
    "get_speculative_engine",
    "handle_command",
    "has_pending_power_confirmation",
    "locate_screen_element_with_vision",
    "reload_plugin_tools",
    "warm_up_connections",
]
