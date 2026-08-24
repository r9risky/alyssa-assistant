"""
Dynamic plugin loader for user-defined actions in plugins/.
"""
import importlib.util
import os
import sys

from config import PLUGIN_SETTINGS as config

PLUGINS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugins")

# So a plugin can do `from _location import get_ip_location` etc. to share
# code with another file in plugins/, the same way any normal Python package
# would resolve a sibling import - without this, each plugin is loaded via
# spec_from_file_location in isolation and has no way to find its neighbors.
if os.path.isdir(PLUGINS_DIR) and PLUGINS_DIR not in sys.path:
    sys.path.insert(0, PLUGINS_DIR)

# Problems from the most recent load_plugins() call, so
# actions.run_diagnostics() can report plugin health without parsing console output.
_load_errors = []

# Watchers (name, check_watch callable, interval_seconds) collected by the
# most recent load_plugins() call - see get_watchers() and the module
# docstring above. Populated as a side effect of load_plugins(), which
# actions.py already calls once at import time, so by the time main.py's
# watcher loop starts, this is ready without a second plugin scan.
_watchers = []

# GUI tab contributions (dicts of filename/title/build/scroll) collected by
# the most recent load_plugins() call - see get_gui_tabs() and
# ConfigDialog._add_plugin_gui_tabs() in overlay/settings_dialog.py. Same
# side-effect pattern as _watchers above: actions.reload_plugins() already
# triggers a load_plugins() scan, so Settings just reads whatever that left
# here instead of importing every plugin file a second time itself.
_gui_tabs = []


def get_load_errors():
    return _load_errors


def get_watchers():
    return _watchers


def get_gui_tabs():
    return _gui_tabs


def load_plugins():
    """Returns (functions, tools) merged from every .py file in plugins/.
    functions is a dict of {name: callable}; tools is a list of tool-schema
    dicts. Files starting with '_' (including __init__.py) are skipped -
    rename a plugin to start with '_' to disable it without deleting it.

    A plugin can also contribute a tab to the Settings window: define a
    module-level GUI_TAB_TITLE string plus a build_gui_tab(dialog) function
    that returns a QWidget (or None to skip), and it'll show up alongside
    Assistant/Engine/etc. See get_gui_tabs() and plugins/_gui_notes_example.py
    for a working example (rename the file to drop the leading underscore
    to enable it). This is a side channel from the FUNCTIONS/TOOLS contract
    above - a plugin can define either, both, or neither."""
    global _load_errors, _watchers, _gui_tabs
    _load_errors = []
    _watchers = []
    _gui_tabs = []
    functions = {}
    tools = []

    if not getattr(config, "PLUGINS_ENABLED", True):
        return functions, tools

    if not os.path.isdir(PLUGINS_DIR):
        return functions, tools

    for filename in sorted(os.listdir(PLUGINS_DIR)):
        if not filename.endswith(".py") or filename.startswith("_"):
            continue

        path = os.path.join(PLUGINS_DIR, filename)
        module_name = f"alyssa_plugin_{filename[:-3]}"

        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
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

        build_gui_tab = getattr(module, "build_gui_tab", None)
        gui_title = getattr(module, "GUI_TAB_TITLE", None)
        if callable(build_gui_tab) and gui_title:
            _gui_tabs.append({
                "filename": filename,
                "title": str(gui_title),
                "build": build_gui_tab,
                # Most plugin tabs are a plain form/list and want the same
                # free scrolling the built-in tabs get. A plugin doing its
                # own internal layout (splitters, a canvas, etc.) can set
                # GUI_TAB_SCROLL = False to manage its own space instead.
                "scroll": bool(getattr(module, "GUI_TAB_SCROLL", True)),
            })
            print(f"[plugins] '{filename}' registered a Settings tab: {gui_title}")

    return functions, tools
