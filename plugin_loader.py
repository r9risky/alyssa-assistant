"""Dynamic plugin loader for user-defined actions in plugins/."""
import importlib.util
import os
import sys
import types

from config import PLUGIN_SETTINGS as config
import plugin_permissions

if getattr(sys, "frozen", False):
    _base_dir = os.path.dirname(sys.executable)
    PLUGINS_DIR = os.path.join(_base_dir, "plugins")
    if not os.path.isdir(PLUGINS_DIR) and hasattr(sys, "_MEIPASS"):
        PLUGINS_DIR = os.path.join(sys._MEIPASS, "plugins")
else:
    PLUGINS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugins")

# plugins/ on sys.path so a plugin can `from _location import ...` siblings.
# Without this, spec_from_file_location isolation can't see neighbors.
if os.path.isdir(PLUGINS_DIR) and PLUGINS_DIR not in sys.path:
    sys.path.insert(0, PLUGINS_DIR)


def _expose_as_plugins_package(stem, module):
    """Make `from plugins.<stem> import X` return the loaded instance. Core code
    imports a few plugins directly; without this it got a second copy with its own
    state (e.g. a watch-along started by voice that its tools couldn't see)."""
    package = sys.modules.get("plugins")
    if package is None:
        package = types.ModuleType("plugins")
        package.__path__ = [PLUGINS_DIR]
        sys.modules["plugins"] = package
    sys.modules[f"plugins.{stem}"] = module
    setattr(package, stem, module)


# From the most recent load_plugins(); actions.run_diagnostics() reports these.
_load_errors = []

# Watchers from the most recent load_plugins() — see get_watchers().
# Populated as a side effect; ready by the time the watcher loop starts.
_watchers = []

# Plugins skipped until the user approves their PERMISSIONS: [{"name", "permissions"}].
_pending_approvals = []

# GUI panels from the most recent load_plugins() — see get_gui_panels().
# Opt in with GUI_PANEL_TITLE + build_gui_panel() at module level.
# Qt is not imported here; overlay builds the widget later (headless-safe).
_gui_panels = []


def get_load_errors():
    return _load_errors


def get_watchers():
    return _watchers


def get_gui_panels():
    return _gui_panels


def get_pending_approvals():
    return _pending_approvals


def load_plugins():
    """(functions, tools) merged from every .py in plugins/.
    functions: {name: callable}; tools: tool-schema dicts.
    Skip files starting with '_' (including __init__.py) — rename to disable."""
    global _load_errors, _watchers, _gui_panels, _pending_approvals
    _load_errors = []
    _watchers = []
    _gui_panels = []
    _pending_approvals = []
    functions = {}
    tools = []

    if not getattr(config, "PLUGINS_ENABLED", True):
        return functions, tools

    if not os.path.isdir(PLUGINS_DIR):
        return functions, tools
    plugin_permissions.grandfather_existing(PLUGINS_DIR)

    for filename in sorted(os.listdir(PLUGINS_DIR)):
        if not filename.endswith(".py") or filename.startswith("_"):
            continue

        path = os.path.join(PLUGINS_DIR, filename)
        module_name = f"alyssa_plugin_{filename[:-3]}"
        # Checked before running any of its code: an unapproved plugin never executes.
        pending = plugin_permissions.needs_approval(filename, path)
        if pending is not None:
            _pending_approvals.append({"name": filename, "permissions": pending})
            print(f"[plugins] '{filename}' is waiting for approval ({', '.join(pending)}).")
            _load_errors.append(f"'{filename}' is waiting for your approval in Settings > Plugins")
            continue

        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            _expose_as_plugins_package(filename[:-3], module)
        except Exception as e:
            print(f"[plugins] Skipping '{filename}' - failed to load: {e}")
            _load_errors.append(f"'{filename}' failed to load: {e}")
            continue

        plugin_functions = getattr(module, "FUNCTIONS", {}) or {}
        plugin_tools = getattr(module, "TOOLS", []) or []
        untrusted_outputs = set(getattr(module, "UNTRUSTED_OUTPUTS", ()) or ())
        if not isinstance(plugin_functions, dict):
            print(f"[plugins] Skipping '{filename}' - FUNCTIONS must be a dict.")
            _load_errors.append(f"'{filename}': FUNCTIONS must be a dict")
            continue
        if not isinstance(plugin_tools, (list, tuple)):
            print(f"[plugins] Skipping tools in '{filename}' - TOOLS must be a list.")
            _load_errors.append(f"'{filename}': TOOLS must be a list")
            plugin_tools = []

        loaded_names = []
        for name, func in plugin_functions.items():
            if not isinstance(name, str) or not callable(func):
                print(f"[plugins] Skipping invalid function in '{filename}'.")
                _load_errors.append(f"'{filename}': one of its FUNCTIONS entries isn't a valid callable")
                continue
            if name in functions:
                print(
                    f"[plugins] '{filename}' defines '{name}', which a "
                    "previously-loaded plugin already defines - skipping the duplicate."
                )
                _load_errors.append(f"'{filename}': '{name}' duplicates another plugin's tool name")
                continue
            functions[name] = func
            if name in untrusted_outputs:
                func._alyssa_untrusted_output = True
            loaded_names.append(name)

        for tool in plugin_tools:
            if not isinstance(tool, dict):
                print(f"[plugins] Skipping a malformed tool in '{filename}'.")
                continue
            tool_name = tool.get("function", {}).get("name")
            if not tool_name or tool_name not in loaded_names:
                continue  # malformed, or its function was skipped as a duplicate above
            tools.append(tool)

        if loaded_names:
            print(f"[plugins] Loaded from {filename}: {', '.join(loaded_names)}")

        watch_func = getattr(module, "check_watch", None)
        if callable(watch_func):
            interval = getattr(module, "WATCH_INTERVAL_SECONDS", 120)
            try:
                interval = max(10, int(interval))
            except (TypeError, ValueError):
                interval = 120
            _watchers.append({"name": filename, "func": watch_func, "interval": interval})
            print(f"[plugins] '{filename}' registered a background watcher (every {interval}s)")

        panel_title = getattr(module, "GUI_PANEL_TITLE", None)
        panel_builder = getattr(module, "build_gui_panel", None)
        if panel_title or panel_builder is not None:
            if not panel_title or not callable(panel_builder):
                print(
                    f"[plugins] '{filename}' has GUI_PANEL_TITLE/build_gui_panel but "
                    "not both - skipping its Settings tab."
                )
                _load_errors.append(
                    f"'{filename}': needs both GUI_PANEL_TITLE (str) and a callable "
                    "build_gui_panel(parent) to register a Settings tab"
                )
            else:
                _gui_panels.append({
                    "name": filename,
                    "title": str(panel_title),
                    "build": panel_builder,
                    # GUI_PANEL_SCROLLABLE = False opts out of the QScrollArea wrapper.
                    "scrollable": bool(getattr(module, "GUI_PANEL_SCROLLABLE", True)),
                })
                print(f"[plugins] '{filename}' registered a Settings tab: {panel_title}")

    return functions, tools
