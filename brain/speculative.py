"""Streaming speculative intent execution engine for sub-100ms conversational responses."""

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Any, Callable, Dict, List, Optional, Tuple

import actions
import config


# Whitelist of strictly safe, idempotent, read-only tools that can be pre-fetched speculatively.
# Destructive or state-mutating actions (delete_file, run_command, system_power_action) are NEVER speculated.
SAFE_SPECULATIVE_TOOLS = {
    "get_weather",
    "get_datetime",
    "run_diagnostics",
    "get_system_status",
    "read_clipboard",
    "search_files",
    "list_reminders",
    "summarize_webpage",
}


class SpeculativeExecutionEngine:
    """Pre-executes safe read-only tool calls concurrently while speech recognition is in flight.

    When the final user utterance is processed, matching tool results are returned from
    the pre-fetch cache instantly, eliminating network round-trip delays.
    """

    def __init__(self, max_workers: int = 4):
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="SpeculativeWorker")
        self._cache: Dict[Tuple[str, str], Tuple[Future, float]] = {}  # (tool_name, serialized_args) -> (future, timestamp)
        self._lock = threading.Lock()
        self._ttl_seconds = 10.0

    def parse_speculative_intents(self, text: str) -> List[Tuple[str, dict]]:
        """Identify high-confidence read-only tool calls from partial or early transcripts."""
        if not text:
            return []
        cleaned = text.strip()
        intents = []

        # 1. Weather intent: e.g. "what's the weather in Tokyo", "weather in London", "how's the weather"
        weather_match = re.search(
            r"(?:(?:what(?:'s|\s+is)?|tell\s+me)\s+(?:the\s+)?weather(?:\s+like)?|how(?:'s|\s+is)?\s+the\s+weather|weather\s+forecast)(?:\s+(?:in|for|at)\s+([a-zA-Z\s,]+))?",
            cleaned,
            re.IGNORECASE,
        )
        if weather_match:
            loc = (weather_match.group(1) or "").strip()
            loc = re.sub(r"[?.!]+$", "", loc).strip()
            intents.append(("get_weather", {"location": loc}))

        # 2. Date and Time intent: e.g. "what time is it", "what's the date", "what day is it", "tell me what time it is"
        time_match = re.search(
            r"(?:(?:what(?:'s|\s+is)?|tell\s+me(?:\s+what)?)\s+(?:the\s+)?(?:time|date|day)(?:\s+is\s+it)?|current\s+time|tell\s+me\s+(?:the\s+)?time)",
            cleaned,
            re.IGNORECASE,
        )
        if time_match:
            intents.append(("get_datetime", {}))

        # 3. System diagnostics: e.g. "how is the computer doing", "system status", "check cpu"
        sys_match = re.search(
            r"(?:system\s+status|check\s+(?:the\s+)?(?:system|cpu|ram|computer)|how(?:'s|\s+is)\s+(?:the\s+)?computer\s+doing|diagnostics)",
            cleaned,
            re.IGNORECASE,
        )
        if sys_match:
            intents.append(("run_diagnostics", {}))
            intents.append(("get_system_status", {}))

        # 4. Search files: e.g. "find file report.docx", "search for file budget"
        file_match = re.search(
            r"(?:search\s+for\s+(?:file|document)\s+|find\s+(?:file|document)\s+)([a-zA-Z0-9_.-]+)",
            cleaned,
            re.IGNORECASE,
        )
        if file_match:
            filename = file_match.group(1).strip()
            intents.append(("search_files", {"query": filename}))

        # 5. Read clipboard: e.g. "what's on my clipboard", "read clipboard"
        clip_match = re.search(
            r"(?:what(?:'s|\s+is)?\s+on\s+(?:my\s+|the\s+)?clipboard|read\s+(?:the\s+|my\s+)?clipboard)",
            cleaned,
            re.IGNORECASE,
        )
        if clip_match:
            intents.append(("read_clipboard", {}))

        return intents

    def _normalize_args(self, args: dict) -> tuple:
        normalized = {}
        for k, v in args.items():
            if isinstance(v, str):
                normalized[k] = v.strip().lower()
            else:
                normalized[k] = v
        return tuple(sorted(normalized.items()))

    def speculate(self, text: str) -> None:
        """Trigger background execution of safe read-only intents identified in text."""
        if not getattr(config, "SPECULATIVE_EXECUTION_ENABLED", True):
            return

        intents = self.parse_speculative_intents(text)
        now = time.time()

        for name, args in intents:
            if name not in SAFE_SPECULATIVE_TOOLS:
                continue
            func = actions.FUNCTIONS.get(name)
            if func is None:
                continue

            args_key = self._normalize_args(args)
            cache_key = (name, str(args_key))

            with self._lock:
                if cache_key in self._cache:
                    fut, ts = self._cache[cache_key]
                    if (now - ts) < self._ttl_seconds and not (fut.done() and fut.exception()):
                        continue

                fut = self._executor.submit(self._run_tool_safely, func, args)
                self._cache[cache_key] = (fut, now)

    def _run_tool_safely(self, func: Callable, args: dict) -> Any:
        return func(**args)

    def get_result(self, name: str, args: dict, timeout: Optional[float] = None) -> Optional[Any]:
        """Check if a speculative result exists or is in flight for (name, args).
        If present, awaits the result and returns it. Otherwise returns None.
        """
        if not getattr(config, "SPECULATIVE_EXECUTION_ENABLED", True):
            return None

        if timeout is None:
            timeout = float(getattr(config, "SPECULATIVE_TIMEOUT_SECONDS", 3.0))

        args_key = self._normalize_args(args)
        cache_key = (name, str(args_key))

        with self._lock:
            entry = self._cache.get(cache_key)
            if entry is None:
                return None
            fut, ts = entry

        if (time.time() - ts) > self._ttl_seconds:
            return None

        try:
            return fut.result(timeout=timeout)
        except Exception:
            return None

    def on_partial_transcript(self, text: str) -> None:
        """Hook called when intermediate partial speech recognition transcripts arrive."""
        if text:
            self.speculate(text)

    def clear(self, force: bool = False) -> None:
        """Clean up expired entries or reset cache between major conversation boundaries."""
        with self._lock:
            if force:
                self._cache.clear()
            else:
                now = time.time()
                self._cache = {
                    k: v for k, v in self._cache.items()
                    if (now - v[1]) < self._ttl_seconds
                }


_engine_instance = None
_engine_lock = threading.Lock()


def get_speculative_engine() -> SpeculativeExecutionEngine:
    global _engine_instance
    if _engine_instance is None:
        with _engine_lock:
            if _engine_instance is None:
                _engine_instance = SpeculativeExecutionEngine()
    return _engine_instance
