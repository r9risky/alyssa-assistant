"""Web sources gathered during one reply, so the bubble can link them.

Web tools call record(); main clears at the start of a turn and take()s after
the reply. Only http(s) URLs are kept (results come from untrusted pages).
"""
import threading
import urllib.parse

MAX_SOURCES = 3

_lock = threading.Lock()
_found: list[dict] = []


def clear() -> None:
    with _lock:
        _found.clear()


def record(url: str, title: str = "") -> None:
    url = (url or "").strip()
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return
    site = parts.hostname.removeprefix("www.")
    with _lock:
        if any(s["site"] == site for s in _found):
            return  # one link per site keeps the footer short
        _found.append({"url": url, "title": (title or "").strip(), "site": site})


def take() -> list[dict]:
    """Up to MAX_SOURCES sources from this turn, in the order found; clears them."""
    with _lock:
        found = _found[:MAX_SOURCES]
        _found.clear()
    return found
