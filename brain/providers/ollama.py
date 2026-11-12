import base64
import json
import time

import requests

from config import PROVIDER_SETTINGS as config
import telemetry

from ..common import GenerationCancelled, _HTTP_SESSION
from .. import usage as _usage
from ..tool_registry import TOOLS

def _call_ollama(messages, on_text_delta=None, cancel_event=None, tools=None):
    tool_list = TOOLS if tools is None else tools
    response = _HTTP_SESSION.post(
        config.OLLAMA_URL,
        json={
            "model": config.OLLAMA_MODEL,
            "messages": messages,
            **({"tools": tool_list} if tool_list else {}),
            "stream": True,
            # Low temperature: more reliable tool-call formatting on small models.
            "options": {
                "temperature": 0.2,
                "num_predict": getattr(config, "LLM_MAX_OUTPUT_TOKENS", 256),
            },
            # keep_alive on every request — Ollama resets the countdown from the last value.
            "keep_alive": getattr(config, "OLLAMA_KEEP_ALIVE", "10m"),
        },
        timeout=(10, 120),
        stream=True,
    )
    response.raise_for_status()
    text_chunks = []
    tool_calls = []
    first_token_at = None
    started_at = time.time()
    usage = {}
    try:
        for line in response.iter_lines(decode_unicode=True):
            if cancel_event is not None and cancel_event.is_set():
                raise GenerationCancelled()
            if not line:
                continue
            event = json.loads(line)
            if event.get("done"):
                usage = event
            message = event.get("message") or {}
            delta = message.get("content") or ""
            if delta:
                if first_token_at is None:
                    first_token_at = time.time()
                    telemetry.log(f"[timing] LLM time-to-first-token: {first_token_at - started_at:.2f}s")
                text_chunks.append(delta)
                if on_text_delta is not None:
                    on_text_delta(delta)
            for call in message.get("tool_calls") or []:
                if call not in tool_calls:
                    tool_calls.append(call)
    finally:
        response.close()
    _usage.record("ollama", usage.get("prompt_eval_count"), usage.get("eval_count"))
    return {
        "message": {
            "role": "assistant",
            "content": "".join(text_chunks),
            "tool_calls": tool_calls,
        }
    }


def warm_up_ollama():
    """Best-effort: preload OLLAMA_MODEL. No-op unless LLM_PROVIDER is ollama.

    Safe on a background thread; failures are swallowed.
    """
    if config.LLM_PROVIDER != "ollama":
        return
    try:
        _HTTP_SESSION.post(
            config.OLLAMA_URL,
            json={
                "model": config.OLLAMA_MODEL,
                "messages": [{"role": "user", "content": "hi"}],
                "stream": False,
                # Force a load only; reply is unused.
                "options": {"temperature": 0.2, "num_predict": 1},
                "keep_alive": getattr(config, "OLLAMA_KEEP_ALIVE", "10m"),
            },
            timeout=120,
        )
    except requests.exceptions.RequestException:
        pass


def _describe_image_ollama(image_bytes: bytes, prompt: str) -> str:
    model = getattr(config, "OLLAMA_VISION_MODEL", "") or config.OLLAMA_MODEL
    b64 = base64.b64encode(image_bytes).decode("ascii")
    try:
        response = _HTTP_SESSION.post(
            config.OLLAMA_URL,
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt, "images": [b64]}],
                "stream": False,
                "options": {"temperature": 0.2, "num_predict": 200},
            },
            timeout=120,
        )
        response.raise_for_status()
    except requests.exceptions.Timeout:
        return "Looking at the screen timed out - try again in a moment."
    except requests.exceptions.ConnectionError:
        return (
            "I can't reach Ollama to look at the screen. Make sure it's "
            "installed and running (open the Ollama app, or run 'ollama "
            "serve' in a terminal)."
        )
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        if e.response is not None:
            print(f"[Ollama vision error {status}] {e.response.text}")
            if status == 404:
                return (
                    f"I can't look at the screen - the model '{model}' "
                    "isn't pulled yet. Run 'ollama pull "
                    f"{model}' in a terminal, or change "
                    "OLLAMA_VISION_MODEL in config.py to a vision model "
                    "you do have."
                )
        return f"Ollama returned an error ({status}) while looking at the screen."

    data = response.json()
    text = ((data.get("message") or {}).get("content") or "").strip()
    return text or "I looked, but didn't get a usable description back."
