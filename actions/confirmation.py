import contextlib
import threading

import config

_power_confirmation_callback = None


_critical_confirmation_callback = None


_tool_confirmation = threading.local()


SECURITY_PRESETS: dict[str, dict[str, bool]] = {
    "ask_always": {
        "open_apps_urls": False,
        "window_management": False,
        "media_volume": False,
        "clipboard_screen": False,
        "typing_keys": False,
        "screen_clicking": False,
        "file_deletion": False,
        "shell_commands": False,
        "power_actions": False,
        "plugins": False,
    },
    "careful": {
        "open_apps_urls": False,
        "window_management": False,
        "media_volume": True,
        "clipboard_screen": True,
        "typing_keys": False,
        "screen_clicking": False,
        "file_deletion": False,
        "shell_commands": False,
        "power_actions": False,
        "plugins": True,
    },
    "balanced": {
        "open_apps_urls": True,
        "window_management": True,
        "media_volume": True,
        "clipboard_screen": True,
        "typing_keys": True,
        "screen_clicking": False,
        "file_deletion": False,
        "shell_commands": False,
        "power_actions": False,
        "plugins": True,
    },
    "autopilot": {
        "open_apps_urls": True,
        "window_management": True,
        "media_volume": True,
        "clipboard_screen": True,
        "typing_keys": True,
        "screen_clicking": True,
        "file_deletion": True,
        "shell_commands": True,
        "power_actions": True,
        "plugins": True,
    },
}

TOOL_CAPABILITY_MAP: dict[str, str] = {
    "open_app": "open_apps_urls",
    "open_url": "open_apps_urls",
    "open_file": "open_apps_urls",
    "minimize_window": "window_management",
    "maximize_window": "window_management",
    "close_window": "window_management",
    "switch_window": "window_management",
    "snap_window": "window_management",
    "show_desktop": "window_management",
    "media_play_pause": "media_volume",
    "media_next_track": "media_volume",
    "media_previous_track": "media_volume",
    "play_music": "media_volume",
    "volume_up": "media_volume",
    "volume_down": "media_volume",
    "toggle_mute": "media_volume",
    "set_volume_level": "media_volume",
    "read_clipboard": "clipboard_screen",
    "set_clipboard": "clipboard_screen",
    "take_screenshot": "clipboard_screen",
    "describe_screen": "clipboard_screen",
    "type_text": "typing_keys",
    "press_keys": "typing_keys",
    "click_screen_element": "screen_clicking",
    "play_game": "typing_keys",  # sends keys/clicks, but only into the game window
    "delete_file": "file_deletion",
    "clean_temp_files": "file_deletion",
    "run_command": "shell_commands",
    "kill_process": "shell_commands",
    "system_power_action": "power_actions",
}


# Names used before v1.8.0, still found in older config files.
_OLD_PRESET_NAMES = {"lockdown": "ask_always", "guarded": "careful", "trusted": "balanced", "blackwall": "autopilot"}
DEFAULT_PRESET = "balanced"


def normalize_preset(preset: str | None) -> str:
    """A known preset key for `preset`, mapping old names and falling back to the default."""
    key = str(preset or DEFAULT_PRESET).strip().lower()
    key = _OLD_PRESET_NAMES.get(key, key)
    return key if key in SECURITY_PRESETS else DEFAULT_PRESET


def get_preset_permissions(preset: str) -> dict[str, bool]:
    """Return a copy of the capability permissions for a named security preset."""
    return dict(SECURITY_PRESETS[normalize_preset(preset)])


def apply_security_preset(preset: str) -> dict[str, bool]:
    """Set config.SECURITY_PRESET and sync config.SECURITY_PERMISSIONS & CONFIRM_BEFORE_ACTIONS."""
    key = normalize_preset(preset)
    perms = get_preset_permissions(key)
    config.SECURITY_PRESET = key
    config.SECURITY_PERMISSIONS = perms
    config.CONFIRM_BEFORE_ACTIONS = key == "ask_always"
    return perms


def infer_capability(tool_name: str | None = None, description: str = "") -> str:
    """Resolve a tool name or confirmation description to a capability key."""
    if tool_name and tool_name in TOOL_CAPABILITY_MAP:
        return TOOL_CAPABILITY_MAP[tool_name]
    desc = (description or "").lower()
    if any(k in desc for k in ("delete", "recycle bin", "clean temp")):
        return "file_deletion"
    if any(k in desc for k in ("run command", "shell", "kill process", "terminate")):
        return "shell_commands"
    if any(k in desc for k in ("click", "screen element")):
        return "screen_clicking"
    if any(k in desc for k in ("shutdown", "restart", "signout", "sleep", "power")):
        return "power_actions"
    if any(k in desc for k in ("type ", "press key", "shortcut", "hotkey")):
        return "typing_keys"
    if any(k in desc for k in ("window", "minimize", "maximize", "close ", "snap", "desktop")):
        return "window_management"
    if any(k in desc for k in ("volume", "mute", "music", "track", "play", "pause")):
        return "media_volume"
    if any(k in desc for k in ("clipboard", "screenshot", "screen")):
        return "clipboard_screen"
    if any(k in desc for k in ("open ", "launch ", "website", "url", "file")):
        return "open_apps_urls"
    return "plugins"


def requires_confirmation(
    tool_name: str | None = None,
    description: str = "",
    force: bool = False,
) -> bool:
    """Determine whether an action requires user confirmation under current security settings."""
    if getattr(config, "CONFIRM_BEFORE_ACTIONS", False):
        return True
    preset = normalize_preset(getattr(config, "SECURITY_PRESET", DEFAULT_PRESET))
    if preset == "ask_always":
        return True
    if preset == "autopilot":
        # Fail closed only when a forced critical check has no confirmation callback wired at all.
        if force and _critical_confirmation_callback is None:
            return True
        return False
    perms = getattr(config, "SECURITY_PERMISSIONS", None)
    if not isinstance(perms, dict):
        perms = get_preset_permissions(preset)
    capability = infer_capability(tool_name, description)
    allowed = perms.get(capability, SECURITY_PRESETS[preset].get(capability, True))
    if force:
        # Critical tools (force=True) require confirmation unless the user explicitly enabled that high-risk capability.
        if capability in ("file_deletion", "shell_commands", "screen_clicking", "power_actions"):
            if _critical_confirmation_callback is None:
                return True
            return not bool(allowed)
        return True
    return not bool(allowed)


class VoiceConfirmationRequired(Exception):
    """Raised internally when a tool has been deferred for spoken approval."""


@contextlib.contextmanager
def tool_confirmation_context(name: str, arguments: dict, approved: bool = False):
    """Makes the current tool call visible to the shared confirmation hook."""
    previous = getattr(_tool_confirmation, "current", None)
    _tool_confirmation.current = (name, dict(arguments), approved)
    try:
        yield
    finally:
        _tool_confirmation.current = previous


def set_power_confirmation_callback(callback):
    """Sets the callback that begins a spoken power-action confirmation."""
    global _power_confirmation_callback
    _power_confirmation_callback = callback


def set_critical_confirmation_callback(callback):
    """Sets the callback for spoken command and deletion approvals."""
    global _critical_confirmation_callback
    _critical_confirmation_callback = callback


def _confirm(description: str, force: bool = False) -> bool:
    """y/n unless CONFIRM_BEFORE_ACTIONS is off and security preset/capability allows it.
    force=True asks for critical actions unless allowed by Blackwall/explicit capability toggle.
    Voice callback may raise VoiceConfirmationRequired."""
    current = getattr(_tool_confirmation, "current", None)
    if current and current[2]:
        return True
    tool_name = current[0] if current else None
    if not requires_confirmation(tool_name=tool_name, description=description, force=force):
        return True
    if current and _critical_confirmation_callback is not None:
        name, arguments, _approved = current
        decision = _critical_confirmation_callback(name, description, arguments)
        if decision is None:
            raise VoiceConfirmationRequired
        return bool(decision)
    answer = input(f'About to: {description}\nProceed? [y/N] ').strip().lower()
    return answer == "y"

