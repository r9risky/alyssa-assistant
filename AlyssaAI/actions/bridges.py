"""Callbacks supplied by the reasoning layer to desktop actions.

Actions are infrastructure: they should not import the high-level ``brain``
package.  The brain registers the few capabilities that actions need during
application composition, keeping dependency direction one-way.
"""

from __future__ import annotations

from collections.abc import Callable

_describe_screen: Callable[[str], str] | None = None
_locate_screen_element: Callable[[str], tuple[float, float] | None] | None = None
_clear_conversation: Callable[[], None] | None = None


def configure_brain_services(
    *,
    describe_screen: Callable[[str], str],
    locate_screen_element: Callable[[str], tuple[float, float] | None],
    clear_conversation: Callable[[], None],
) -> None:
    """Bind brain-owned services once the application has been composed."""
    global _describe_screen, _locate_screen_element, _clear_conversation
    _describe_screen = describe_screen
    _locate_screen_element = locate_screen_element
    _clear_conversation = clear_conversation


def describe_screen(question: str = "") -> str:
    if _describe_screen is None:
        return "Couldn't look at the screen because the vision service is not initialized."
    return _describe_screen(question)


def locate_screen_element(description: str) -> tuple[float, float] | None:
    if _locate_screen_element is None:
        return None
    return _locate_screen_element(description)


def clear_conversation() -> bool:
    if _clear_conversation is None:
        return False
    _clear_conversation()
    return True
