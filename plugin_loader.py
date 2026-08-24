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


def get_load_errors():
    return _load_errors


def get_watchers():
    return _watchers


def load_plugins():
    """Returns (functions, tools) merged from every .py file in plugins/.
    functions is a dict of {name: callable}; tools is a list of tool-schema
    dicts. Files starting with '_' (including __init__.py) are skipped -
    rename a plugin to start with '_' to disable it without deleting it."""
    global _load_errors, _watchers
    _load_errors = []
    _watchers = []
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

    return functions, tools
