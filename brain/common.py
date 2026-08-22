import json
from urllib.parse import urlsplit

import requests

_HTTP_SESSION = requests.Session()


def api_origin(value: str) -> str:
    """Return a normalized network origin, or an empty string if invalid."""
    try:
        parsed = urlsplit(value)
        return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}" if parsed.netloc else ""
    except ValueError:
        return ""


def is_api_key_transport_secure(value: str, api_key: str = "") -> bool:
    """API keys require HTTPS, except for explicit loopback HTTP endpoints."""
    try:
        parsed = urlsplit(value)
        scheme = parsed.scheme.lower()
        host = (parsed.hostname or "").rstrip(".").lower()
    except ValueError:
        return False
    if scheme == "https" and host:
        return True
    if scheme != "http" or not host:
        return False
    if not api_key:
        return True
    return host in {"localhost", "127.0.0.1", "::1"}

class GenerationCancelled(Exception):
    pass


def _iter_sse_json(response, cancel_event=None):
    for line in response.iter_lines(decode_unicode=True):
        if cancel_event is not None and cancel_event.is_set():
            response.close()
            raise GenerationCancelled()
        if not line or not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        yield json.loads(payload)
