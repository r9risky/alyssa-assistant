"""Every config value the Settings window writes must be read by code that runs it.

A setting nothing reads looks like it works and silently does nothing (the old
"Barge-in tolerance" did exactly that), so this fails as soon as one appears.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
SETTINGS_UI = [
    *sorted((ROOT / "overlay" / "settings_tabs").glob("*.py")),
    ROOT / "overlay" / "settings_dialog.py",
    ROOT / "overlay" / "setup_wizard.py",
]
NOT_RUNTIME = ("overlay/settings_tabs/", "overlay/settings_dialog.py", "overlay/setup_wizard.py",
               "config.py", "user_settings.py", "tests/", "scripts/")


def _config_keys() -> set:
    return set(re.findall(r"^([A-Z][A-Z0-9_]+)\s*=", (ROOT / "config.py").read_text(), re.M))


def _keys_written_by_settings() -> set:
    found = set()
    for path in SETTINGS_UI:
        found |= set(re.findall(r"[\"']([A-Z][A-Z0-9_]{3,})[\"']\s*:", path.read_text(encoding="utf-8")))
    return found & _config_keys()


def _runtime_source() -> str:
    parts = []
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith(NOT_RUNTIME) or "/." in "/" + rel:
            continue
        parts.append(path.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(parts)


def test_settings_window_writes_settings():
    assert len(_keys_written_by_settings()) > 40  # the scan itself still finds them


def test_every_setting_is_read_somewhere():
    runtime = _runtime_source()
    unused = sorted(k for k in _keys_written_by_settings() if not re.search(rf"\b{k}\b", runtime))
    assert not unused, f"Settings that nothing reads (wire them up or remove them): {unused}"
