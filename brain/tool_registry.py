"""Runtime tool registry shared by orchestration and LLM providers."""

import actions

from .tool_catalog import BASE_TOOLS


TOOLS = BASE_TOOLS + actions.PLUGIN_TOOLS


def refresh_tools() -> None:
    """Refresh plugin schemas in place so provider references stay valid."""
    TOOLS[:] = BASE_TOOLS + actions.PLUGIN_TOOLS
