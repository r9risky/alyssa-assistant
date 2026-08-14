"""Non-blocking console metrics for the latency-critical voice path."""

import queue
import threading

_messages = queue.SimpleQueue()
_started = False
_lock = threading.Lock()


def _run():
    while True:
        print(_messages.get())


def log(message: str) -> None:
    global _started
    if not _started:
        with _lock:
            if not _started:
                threading.Thread(target=_run, daemon=True).start()
                _started = True
    _messages.put(message)
