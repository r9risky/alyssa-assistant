"""Core services and utilities for Alyssa Assistant."""

from .credential_store import (
    get_secret,
    set_secret,
    clear_secret,
    migrate_legacy_plaintext_keys,
)
from .startup_logging import (
    configure as configure_logging,
    format_content_log,
    format_tool_log,
    redact_sensitive_text,
)
from .telemetry import (
    log as log_telemetry,
)
from .nameutil import (
    contains_name,
    find_name_span,
    strip_name_at_span,
    name_pattern,
)
from .hotkeys import (
    GlobalHotkeyManager,
    format_qt_key_sequence,
    parse_hotkey_chord,
)
from .memory import (
    remember,
    forget,
    load_memories,
    relevant_memories,
)

__all__ = [
    "get_secret",
    "set_secret",
    "clear_secret",
    "migrate_legacy_plaintext_keys",
    "configure_logging",
    "format_content_log",
    "format_tool_log",
    "redact_sensitive_text",
    "log_telemetry",
    "contains_name",
    "find_name_span",
    "strip_name_at_span",
    "name_pattern",
    "GlobalHotkeyManager",
    "format_qt_key_sequence",
    "parse_hotkey_chord",
    "remember",
    "forget",
    "load_memories",
    "relevant_memories",
]
