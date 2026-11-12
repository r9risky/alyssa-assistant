"""Plugin permission manifests and the user's approvals.

A plugin declares what it needs at module level, e.g.

    PERMISSIONS = ["network", "files"]

The loader reads that with `ast` *without running the plugin*. Plugins that ship
with Alyssa load as before. Any other plugin (dropped in by hand, or from the
gallery) waits until the user approves it in Settings > Plugins, and asks again
if a later version declares more permissions.

shortcut: this is informed consent, not a sandbox - Python plugins can still do
anything once approved. A real sandbox (separate process with OS limits) is the
upgrade if plugins from strangers become common.
"""
import ast
import json
import os
import sys

PERMISSIONS = {
    "network": "Use the internet",
    "files": "Read or change files on this PC",
    "keyboard_mouse": "Type, click and move the mouse",
    "screen": "See what's on your screen",
    "microphone": "Listen through the microphone",
    "camera": "Use the camera",
    "shell": "Run programs and commands",
    "background": "Run in the background and speak up on its own",
}
UNDECLARED = "undeclared"  # no PERMISSIONS list: it could do anything

# Shipped with Alyssa (tests/test_plugin_permissions.py keeps this in sync with plugins/).
BUILTIN_PLUGINS = frozenset({
    "calculator_converter", "calendar_gmail", "call_mode", "caveman_mode", "code_husk_mode",
    "companion_life", "daily_briefing", "desktop_chaos", "file_editor", "fondness_meter",
    "game_autoplay", "game_copilot", "location", "mini_games_and_ink", "news_digest",
    "ponytail_mode", "process_manager", "reminders", "routines", "screen_lookout", "security_camera",
    "sticky_notes", "system_watch", "timers", "watch_along", "weather", "web_search",
    "web_summarizer",
})

if getattr(sys, "frozen", False):
    _BASE_DIR = os.path.dirname(sys.executable)
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APPROVALS_FILE = os.path.join(_BASE_DIR, "plugin_approvals.json")


def _stem(filename: str) -> str:
    """'_weather.py' and 'weather.py' share one approval (the underscore only disables it)."""
    return os.path.basename(filename).removesuffix(".py").lstrip("_")


def is_builtin(filename: str) -> bool:
    return _stem(filename) in BUILTIN_PLUGINS


def declared(path: str) -> list[str] | None:
    """The plugin's PERMISSIONS list, read from source without executing it; None if absent."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=path)
    except (OSError, SyntaxError, ValueError):
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "PERMISSIONS" for t in node.targets
        ):
            try:
                value = ast.literal_eval(node.value)
            except ValueError:
                return None
            if isinstance(value, (list, tuple, set)):
                return sorted({str(v).strip().lower() for v in value if str(v).strip()})
    return None


def _load() -> dict:
    try:
        with open(APPROVALS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    tmp = APPROVALS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, sort_keys=True)
    os.replace(tmp, APPROVALS_FILE)


def _wanted(path: str) -> list[str]:
    return declared(path) or [UNDECLARED]


def needs_approval(filename: str, path: str) -> list[str] | None:
    """Permissions awaiting the user's OK, or None when the plugin may load."""
    if is_builtin(filename):
        return None
    wanted = _wanted(path)
    approved = _load().get(_stem(filename))
    if isinstance(approved, list) and set(wanted) <= set(approved):
        return None
    return wanted


def approve(filename: str, path: str) -> None:
    data = _load()
    data[_stem(filename)] = _wanted(path)
    _save(data)


def grandfather_existing(plugins_dir: str) -> None:
    """First run of this feature: approve the custom plugins the user already had."""
    if os.path.exists(APPROVALS_FILE):
        return
    data = {}
    try:
        names = os.listdir(plugins_dir)
    except OSError:
        names = []
    for name in names:
        if name.endswith(".py") and not is_builtin(name) and not name.startswith("__"):
            data[_stem(name)] = _wanted(os.path.join(plugins_dir, name))
    try:
        _save(data)
    except OSError as e:
        print(f"(couldn't save plugin approvals: {e})")


def describe(permissions: list[str]) -> str:
    """One line per permission, for the approval prompt."""
    lines = []
    for p in permissions:
        if p == UNDECLARED:
            lines.append("• Doesn't say what it needs, so it could do anything")
        else:
            lines.append(f"• {PERMISSIONS.get(p, p)}")
    return "\n".join(lines)
