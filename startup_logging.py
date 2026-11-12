"""Early startup logging that works with or without a Windows console."""

import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import sys
import threading
import time


_configured = False
_log_path = None

FULL_CONTENT_LOG_ENV = "ALYSSA_LOG_FULL_CONTENT"
_SECRET_CONFIG_FIELDS = (
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "CUSTOM_API_KEY",
    "ELEVENLABS_API_KEY",
    "SPOTIFY_CLIENT_ID",
    "SPOTIFY_CLIENT_SECRET",
    "YOUTUBE_API_KEY",
)
_SECRET_ENV_VARS = (
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "CUSTOM_LLM_API_KEY",
    "SPOTIFY_CLIENT_ID",
    "SPOTIFY_CLIENT_SECRET",
    "YOUTUBE_API_KEY",
)
_LABELED_SECRET_RE = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token|"
    r"client[_-]?secret|authorization|token)\b\s*[:=]\s*[\"']?)([^\s,;\"'}&]+)"
)
_BEARER_RE = re.compile(r"(?i)(\bbearer\s+)([A-Za-z0-9._~+/=-]+)")
_SIGNED_URL_RE = re.compile(
    r"(?i)([?&](?:x-amz-signature|signature|sig|token|access_token)=)([^&\s]+)"
)
_KEY_SHAPE_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{8,}|AIza[A-Za-z0-9_-]{16,}|"
    r"gh[pousr]_[A-Za-z0-9_]{12,}|xox[baprs]-[A-Za-z0-9-]{10,}|"
    r"(?:token|tok|key)_[A-Za-z0-9_-]{8,})\b"
)
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")


def full_content_logging_enabled() -> bool:
    """Whether plaintext conversation/tool details were explicitly enabled."""
    return os.environ.get(FULL_CONTENT_LOG_ENV, "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def format_content_log(label: str, content, *, full_text: str | None = None) -> str:
    """Return content only for the explicit debug opt-in; otherwise metadata."""
    content_text = "" if content is None else str(content)
    if full_content_logging_enabled():
        return full_text if full_text is not None else content_text
    return f"{label}, {len(content_text)} chars"


def format_tool_log(name: str, arguments, result) -> str:
    """Format tool activity without persisting arguments/results by default."""
    arguments_text = str(arguments)
    result_text = str(result)
    return format_content_log(
        f"[tool] {name} called; arguments received, {len(arguments_text)} chars; "
        "result received",
        result_text,
        full_text=f"[tool] {name}({arguments}) -> {result}",
    )


def _known_secret_values() -> set[str]:
    values = {os.environ.get(name, "") for name in _SECRET_ENV_VARS}
    config_module = sys.modules.get("config")
    if config_module is not None:
        values.update(
            str(getattr(config_module, name, "") or "")
            for name in _SECRET_CONFIG_FIELDS
        )
    return {value for value in values if len(value) >= 4}


def redact_sensitive_text(text: str) -> str:
    """Scrub likely credentials and known configured secret values from a log line."""
    redacted = str(text)
    for value in sorted(_known_secret_values(), key=len, reverse=True):
        redacted = redacted.replace(value, "[REDACTED]")
    redacted = _LABELED_SECRET_RE.sub(r"\1[REDACTED]", redacted)
    redacted = _BEARER_RE.sub(r"\1[REDACTED]", redacted)
    redacted = _SIGNED_URL_RE.sub(r"\1[REDACTED]", redacted)
    redacted = _KEY_SHAPE_RE.sub("[REDACTED]", redacted)
    return _JWT_RE.sub("[REDACTED]", redacted)


# Set while this thread is inside a _Tee write. If logging fails (a log rotation the OS
# refuses because something else has alyssa.log open), logging prints its error to
# sys.stderr, another _Tee, from inside the first one; that used to deadlock the thread
# with the log locks held, and every thread that printed after it froze too.
_in_tee = threading.local()


class _SafeRotatingFileHandler(RotatingFileHandler):
    """Keeps writing to the same file when Windows won't let it rotate, and retries later."""

    _RETRY_SECONDS = 60.0
    _retry_at = 0.0

    def shouldRollover(self, record):
        return time.monotonic() >= self._retry_at and super().shouldRollover(record)

    def doRollover(self):
        try:
            super().doRollover()
        except OSError:
            self._retry_at = time.monotonic() + self._RETRY_SECONDS
            if self.stream is None:
                self.stream = self._open()


class _Tee:
    def __init__(self, original, logger, level):
        self._original = original
        self._logger = logger
        self._level = level
        self._buffer = ""
        self._lock = threading.Lock()

    def write(self, text):
        if not text:
            return 0
        if getattr(_in_tee, "active", False):
            # Logging's own error report while we were logging: console only.
            if self._original is not None:
                self._original.write(text)
            return len(text)
        _in_tee.active = True
        try:
            return self._write(text)
        finally:
            _in_tee.active = False

    def _write(self, text):
        with self._lock:
            if self._original is not None:
                self._original.write(text)
            self._buffer += text
            while "\n" in self._buffer:
                line, self._buffer = self._buffer.split("\n", 1)
                if line.rstrip("\r"):
                    self._logger.log(
                        self._level, redact_sensitive_text(line.rstrip("\r"))
                    )
        return len(text)

    def flush(self):
        if getattr(_in_tee, "active", False):
            return
        with self._lock:
            if self._buffer:
                self._logger.log(self._level, redact_sensitive_text(self._buffer))
                self._buffer = ""
            if self._original is not None:
                self._original.flush()

    def isatty(self):
        return bool(self._original and self._original.isatty())

    @property
    def encoding(self):
        return getattr(self._original, "encoding", "utf-8")


def configure(app_dir):
    """Mirror stdout/stderr to a rotating file and return its path."""
    global _configured, _log_path
    if _configured:
        return _log_path

    requested_dir = os.environ.get("ALYSSA_LOG_DIR")
    log_dir = Path(requested_dir) if requested_dir else Path(app_dir) / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        _log_path = log_dir / "alyssa.log"
        logger = logging.getLogger("alyssa.output")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        handler = _SafeRotatingFileHandler(
            _log_path, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        sys.stdout = _Tee(sys.stdout, logger, logging.INFO)
        sys.stderr = _Tee(sys.stderr, logger, logging.ERROR)
        _configured = True
        print(f"Startup log: {_log_path}")
        if full_content_logging_enabled():
            print(
                f"WARNING: {FULL_CONTENT_LOG_ENV} is enabled. Conversation and tool "
                "content may be stored in rotating plaintext log files."
            )
        return _log_path
    except OSError as exc:
        print(f"WARNING: Could not create Alyssa startup log in {log_dir}: {exc}")
        return None
