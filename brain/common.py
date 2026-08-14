import json

import requests

_HTTP_SESSION = requests.Session()


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
