"""Coding buddy mode plugin for Alyssa Assistant.

Inspired by Capri Desktop's `Husk.cs`:
Allows Alyssa to act as a live desktop companion for coding agents like
Claude Code, Codex, and OpenCode:
- Receives hook events (`PreToolUse`, `PostToolUse`, `Notification`, `PermissionRequest`, `Stop`)
  via a local loopback HTTP endpoint (`127.0.0.1:HUSK_PORT/hook`) or direct API calls.
- Shows live status captions in Alyssa's speech bubble while coding agents work.
- Alerts the user when a coding agent is waiting for tool permission or finishes a task.
"""

from __future__ import annotations

import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import startup_logging
import threading

import config

TAB_TITLE = "Coding buddy mode"
WATCH_INTERVAL_SECONDS = 10

_lock = threading.RLock()
_recent_events: list[dict] = []
_pending_alerts: list[str] = []
_http_server: HTTPServer | None = None
_http_thread: threading.Thread | None = None


def record_husk_event(
    event_type: str,
    tool_name: str = "",
    summary: str = "",
    requires_approval: bool = False,
) -> dict:
    """Record a coding-agent hook event and optionally queue a companion caption/alert."""
    now_str = datetime.datetime.now().strftime("%H:%M:%S")
    ev_type = (event_type or "Event").strip()
    t_name = (tool_name or "").strip()
    summ = " ".join((summary or "").split())

    caption = f"[{ev_type}]"
    if t_name:
        caption += f" {t_name}"
    if summ:
        caption += f": {summ}"

    entry = {
        "time": now_str,
        "event_type": ev_type,
        "tool_name": t_name,
        "summary": summ,
        "requires_approval": bool(requires_approval),
        "caption": caption,
    }
    with _lock:
        _recent_events.append(entry)
        if len(_recent_events) > 50:
            del _recent_events[:-50]
        if requires_approval:
            alert_msg = f"Your coding agent needs approval for {t_name or 'an action'}: {summ or 'check your terminal'}."
            _pending_alerts.append(alert_msg)
        elif ev_type.lower() in ("stop", "completed", "done", "subagentstop") and summ:
            _pending_alerts.append(f"Coding agent finished: {summ}")

    if getattr(config, "HUSK_MODE_ENABLED", False) and not getattr(config, "QUIET_MODE", False):
        try:
            from overlay.widgets import get_active_companion, run_on_gui_thread
            companion = get_active_companion()
            if companion is not None and companion.bubble is not None:
                run_on_gui_thread(lambda text=caption: companion.bubble.show_message(text))
        except Exception as e:
            startup_logging.warn_once("husk caption", e)

    return entry


def get_recent_husk_events(limit: int = 15) -> list[dict]:
    """Return recent coding agent events."""
    with _lock:
        return [dict(e) for e in _recent_events[-max(1, limit):]]


def clear_husk_events() -> None:
    with _lock:
        _recent_events.clear()
        _pending_alerts.clear()


def get_claude_code_hook_snippet(port: int | None = None) -> str:
    """Return a JSON snippet for `.claude/settings.json` to forward hook events to Alyssa."""
    p = int(port or getattr(config, "HUSK_PORT", 47831))
    cmd = (
        f"curl -sS -X POST http://127.0.0.1:{p}/hook "
        "-H 'Content-Type: application/json' -d @- >/dev/null 2>&1 || true"
    )
    hook_cfg = {
        "hooks": {
            "PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": cmd}]}],
            "Notification": [{"matcher": "*", "hooks": [{"type": "command", "command": cmd}]}],
            "Stop": [{"matcher": "*", "hooks": [{"type": "command", "command": cmd}]}],
        }
    }
    return json.dumps(hook_cfg, indent=2)


class _HuskRequestHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path not in ("/hook", "/husk", "/"):
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(min(length, 65536)) if length > 0 else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8", errors="replace"))
        except Exception:
            payload = {}
        if isinstance(payload, dict):
            ev_type = str(
                payload.get("hook_event_name")
                or payload.get("event")
                or payload.get("type")
                or "HookEvent"
            )
            tool = str(payload.get("tool_name") or payload.get("tool") or "")
            summary = str(
                payload.get("message")
                or payload.get("summary")
                or payload.get("command")
                or ""
            )
            needs_approval = bool(
                payload.get("requires_approval")
                or ev_type.lower() in ("permissionrequest", "approval_needed")
            )
            record_husk_event(ev_type, tool_name=tool, summary=summary, requires_approval=needs_approval)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, format, *args):
        return  # Keep console clean


def start_husk_server(port: int | None = None) -> bool:
    """Start the local 127.0.0.1 coding buddy webhook listener if not already running."""
    global _http_server, _http_thread
    with _lock:
        if _http_server is not None:
            return True
        target_port = int(port or getattr(config, "HUSK_PORT", 47831))
        try:
            server = HTTPServer(("127.0.0.1", target_port), _HuskRequestHandler)
        except OSError:
            return False
        _http_server = server
        _http_thread = threading.Thread(target=server.serve_forever, daemon=True)
        _http_thread.start()
        return True


def stop_husk_server() -> None:
    """Stop the local coding buddy webhook listener if running."""
    global _http_server, _http_thread
    with _lock:
        server = _http_server
        _http_server = None
        _http_thread = None
    if server is not None:
        try:
            server.shutdown()
            server.server_close()
        except Exception:
            pass


def husk_code_companion(action: str = "status", message: str = "") -> str:
    """Inspect or control Coding buddy mode for Claude Code, Codex, and OpenCode."""
    act = (action or "status").strip().lower()
    if act == "enable":
        config.HUSK_MODE_ENABLED = True
        started = start_husk_server()
        port = getattr(config, "HUSK_PORT", 47831)
        if started:
            return f"Coding buddy mode enabled and listening on 127.0.0.1:{port}."
        return "Coding buddy mode enabled."

    if act == "disable":
        config.HUSK_MODE_ENABLED = False
        stop_husk_server()
        return "Coding buddy mode disabled."

    if act in ("snippet", "install_hooks_snippet", "hooks"):
        return "Add this to `.claude/settings.json`:\n" + get_claude_code_hook_snippet()

    if act == "log" and message.strip():
        record_husk_event("Note", summary=message.strip())
        return f"Logged coding status: {message.strip()}"

    events = get_recent_husk_events(limit=6)
    enabled_str = "enabled" if getattr(config, "HUSK_MODE_ENABLED", False) else "disabled"
    if not events:
        return f"Coding buddy mode is {enabled_str}. No coding agent events recorded yet."
    lines = [f"Coding buddy mode is {enabled_str}. Recent coding agent activity:"]
    for ev in events:
        lines.append(f"- [{ev['time']}] {ev['caption']}")
    return "\n".join(lines)


def check_proactive() -> str | None:
    """Announce queued coding agent permission requests or completion alerts."""
    if not getattr(config, "HUSK_MODE_ENABLED", False) or getattr(config, "QUIET_MODE", False):
        return None
    with _lock:
        if not _pending_alerts:
            return None
        return _pending_alerts.pop(0)


def build_gui(parent=None):
    """Build the Settings tab for Coding buddy mode."""
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import (
        QCheckBox,
        QHBoxLayout,
        QLabel,
        QListWidget,
        QPlainTextEdit,
        QPushButton,
        QSpinBox,
        QVBoxLayout,
        QWidget,
    )

    panel = QWidget(parent)
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(12, 12, 12, 12)
    layout.setSpacing(10)

    enable_check = QCheckBox("Turn on coding buddy mode (live companion for Claude Code / Codex)")
    enable_check.setChecked(bool(getattr(config, "HUSK_MODE_ENABLED", False)))

    def _on_toggle(checked: bool):
        config.HUSK_MODE_ENABLED = bool(checked)
        if checked:
            start_husk_server()
        else:
            stop_husk_server()

    enable_check.toggled.connect(_on_toggle)
    layout.addWidget(enable_check)

    port_row = QHBoxLayout()
    port_row.addWidget(QLabel("Local Hook Port (127.0.0.1):"))
    port_spin = QSpinBox()
    port_spin.setRange(1024, 65535)
    port_spin.setValue(int(getattr(config, "HUSK_PORT", 47831)))
    port_spin.valueChanged.connect(lambda val: setattr(config, "HUSK_PORT", int(val)))
    port_row.addWidget(port_spin)
    port_row.addStretch(1)
    layout.addLayout(port_row)

    layout.addWidget(QLabel("Recent Coding Agent Hook Events:"))
    events_list = QListWidget()
    events_list.setMaximumHeight(110)
    layout.addWidget(events_list)

    def _refresh():
        events_list.clear()
        for ev in get_recent_husk_events(limit=20):
            events_list.addItem(f"[{ev['time']}] {ev['caption']}")

    btn_row = QHBoxLayout()
    refresh_btn = QPushButton("Refresh Events")
    clear_btn = QPushButton("Clear Events")
    btn_row.addWidget(refresh_btn)
    btn_row.addWidget(clear_btn)
    btn_row.addStretch(1)
    layout.addLayout(btn_row)

    refresh_btn.clicked.connect(_refresh)
    clear_btn.clicked.connect(lambda: (clear_husk_events(), _refresh()))
    _refresh()

    layout.addWidget(QLabel("Claude Code `.claude/settings.json` Hook Snippet:"))
    snippet_box = QPlainTextEdit()
    snippet_box.setReadOnly(True)
    snippet_box.setPlainText(get_claude_code_hook_snippet())
    layout.addWidget(snippet_box, 1)

    copy_btn = QPushButton("Copy Hook Snippet")
    copy_btn.clicked.connect(
        lambda: QGuiApplication.clipboard().setText(snippet_box.toPlainText())
        if QGuiApplication.clipboard() is not None
        else None
    )
    layout.addWidget(copy_btn)

    return panel


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "husk_code_companion",
            "description": (
                "Check or control Coding buddy mode (where Alyssa acts as a live desktop companion "
                "for Claude Code, Codex, or OpenCode sessions), view recent coding agent activity, "
                "or generate the `.claude/settings.json` hook configuration."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "One of: 'status', 'enable', 'disable', 'snippet', or 'log'.",
                        "enum": ["status", "enable", "disable", "snippet", "log"],
                    },
                    "message": {
                        "type": "string",
                        "description": "Optional status note when action is 'log'.",
                    },
                },
                "required": [],
            },
        },
    }
]

FUNCTIONS = {
    "husk_code_companion": husk_code_companion,
}
