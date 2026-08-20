"""Early startup logging that works with or without a Windows console."""

import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
import threading


_configured = False
_log_path = None


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
        with self._lock:
            if self._original is not None:
                self._original.write(text)
            self._buffer += text
            while "\n" in self._buffer:
                line, self._buffer = self._buffer.split("\n", 1)
                if line.rstrip("\r"):
                    self._logger.log(self._level, line.rstrip("\r"))
        return len(text)

    def flush(self):
        with self._lock:
            if self._buffer:
                self._logger.log(self._level, self._buffer)
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
        handler = RotatingFileHandler(
            _log_path, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        sys.stdout = _Tee(sys.stdout, logger, logging.INFO)
        sys.stderr = _Tee(sys.stderr, logger, logging.ERROR)
        _configured = True
        print(f"Startup log: {_log_path}")
        return _log_path
    except OSError as exc:
        print(f"WARNING: Could not create Alyssa startup log in {log_dir}: {exc}")
        return None
