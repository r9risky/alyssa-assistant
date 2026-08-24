"""PC automation actions and system control utilities for Alyssa."""

import plugin_loader


from .apps_and_files import (
    consume_restart_request, delete_file, open_app, open_file, open_url,
    relaunch_alyssa, restart_alyssa, run_command,
)
from .clipboard_and_screen import (
    click_screen_element, describe_screen, read_clipboard, set_clipboard, take_screenshot,
)
from .confirmation import (
    VoiceConfirmationRequired, set_critical_confirmation_callback,
    set_power_confirmation_callback, tool_confirmation_context,
)
from .bridges import configure_brain_services
from .input_sim import press_keys, type_text
from .media import (
    media_next_track, media_play_pause, media_previous_track, set_volume_level,
    toggle_mute, volume_down, volume_up,
)
from .music import play_music
from .system import (
    forget_fact, get_datetime, remember_fact, reset_conversation, run_diagnostics,
    search_files, system_power_action,
)
from .windows import (
    close_window, maximize_window, minimize_window, show_desktop, snap_window, switch_window,
)

_BUILTIN_FUNCTIONS = {
    "open_app": open_app,
    "type_text": type_text,
    "press_keys": press_keys,
    "open_url": open_url,
    "open_file": open_file,
    "delete_file": delete_file,
    "run_command": run_command,
    "remember_fact": remember_fact,
    "forget_fact": forget_fact,
    "search_files": search_files,
    "media_play_pause": media_play_pause,
    "media_next_track": media_next_track,
    "media_previous_track": media_previous_track,
    "play_music": play_music,
    "volume_up": volume_up,
    "volume_down": volume_down,
    "toggle_mute": toggle_mute,
    "set_volume_level": set_volume_level,
    "minimize_window": minimize_window,
    "maximize_window": maximize_window,
    "close_window": close_window,
    "switch_window": switch_window,
    "snap_window": snap_window,
    "show_desktop": show_desktop,
    "read_clipboard": read_clipboard,
    "set_clipboard": set_clipboard,
    "take_screenshot": take_screenshot,
    "describe_screen": describe_screen,
    "click_screen_element": click_screen_element,
    "get_datetime": get_datetime,
    "restart_alyssa": restart_alyssa,
    "system_power_action": system_power_action,
    "reset_conversation": reset_conversation,
    "run_diagnostics": run_diagnostics,
}


FUNCTIONS = {}


PLUGIN_FUNCTIONS = {}


PLUGIN_TOOLS = []


_PLUGIN_LOAD_PROBLEMS = []


def reload_plugins():
    """(Re)loads every plugin from plugins/ and rebuilds FUNCTIONS,
    PLUGIN_FUNCTIONS, PLUGIN_TOOLS, and _PLUGIN_LOAD_PROBLEMS from scratch.
    Safe to call repeatedly - e.g. from the Settings > Plugins editor after
    a save, enable/disable, add, or delete - since it always starts back
    from _BUILTIN_FUNCTIONS rather than mutating the previous state.
    Callers that also need brain.TOOLS to reflect the change should call
    brain.reload_plugin_tools() right after this."""
    global FUNCTIONS, PLUGIN_FUNCTIONS, PLUGIN_TOOLS, _PLUGIN_LOAD_PROBLEMS

    plugin_functions, plugin_tools = plugin_loader.load_plugins()

    # Plugins are intentionally unable to replace a built-in action. Aside
    # from matching the documented contract, this prevents a stray plugin
    # file from silently changing core behavior such as delete_file or
    # run_command.
    problems = []
    for plugin_name in list(plugin_functions):
        if plugin_name in _BUILTIN_FUNCTIONS:
            print(
                f"[plugins] Ignoring '{plugin_name}' because it conflicts with "
                "a built-in action."
            )
            problems.append(f"'{plugin_name}' conflicts with a built-in action")
            del plugin_functions[plugin_name]

    plugin_tools = [
        tool
        for tool in plugin_tools
        if tool.get("function", {}).get("name") in plugin_functions
    ]

    PLUGIN_FUNCTIONS = plugin_functions
    PLUGIN_TOOLS = plugin_tools
    _PLUGIN_LOAD_PROBLEMS = problems
    FUNCTIONS = {**_BUILTIN_FUNCTIONS, **PLUGIN_FUNCTIONS}



reload_plugins()


def __getattr__(name):
    if name in {"_critical_confirmation_callback", "_power_confirmation_callback", "_confirm"}:
        from . import confirmation
        return getattr(confirmation, name)
    if name == "_spotify_token_cache":
        from .music import _spotify_token_cache
        return _spotify_token_cache
    if name == "_resolve_placeholder_user_path":
        from .apps_and_files import _resolve_placeholder_user_path
        return _resolve_placeholder_user_path
    raise AttributeError(name)
