import base64
import json
import time

import requests

from config import PROVIDER_SETTINGS as config
import telemetry

from ..common import _HTTP_SESSION, _iter_sse_json, is_api_key_transport_secure
from ..tool_registry import TOOLS

def _messages_to_openai(messages):
    """Converts our internal OpenAI-style message list into the exact wire
    format the OpenAI chat-completions endpoint (and anything that mimics
    it - Groq, OpenRouter, Together, etc.) expects. Our internal shape is
    already very close to this (it's modeled on it), the only real
    differences being: tool_calls need string-encoded JSON arguments (not
    a dict), and tool results are addressed by "tool_call_id" rather than
    the "id"/"name" pair Ollama/Gemini use."""
    out = []
    for m in messages:
        role = m.get("role")
        if role in ("system", "user"):
            out.append({"role": role, "content": m.get("content") or ""})
        elif role in ("assistant", "model"):
            entry = {"role": "assistant", "content": (m.get("content") or "").strip() or None}
            tool_calls = m.get("tool_calls") or []
            if tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": call.get("id") or f"call_{i}",
                        "type": "function",
                        "function": {
                            "name": call["function"]["name"],
                            "arguments": json.dumps(call["function"].get("arguments") or {}),
                        },
                    }
                    for i, call in enumerate(tool_calls)
                ]
            out.append(entry)
        elif role == "tool":
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": m.get("id") or "",
                    "content": str(m.get("content", "")),
                }
            )
    return out


def _call_openai_compatible(
    messages,
    base_url,
    api_key,
    model,
    provider_label,
    on_text_delta=None,
    cancel_event=None,
    tools=None,
):
    """Shared implementation for any provider that speaks the OpenAI
    chat-completions format - used directly for "openai" and
    "custom_openai", since they're wire-compatible aside from the base URL/
    key/model. api_key may be blank (e.g. a local LM Studio server that
    doesn't check one)."""
    if not is_api_key_transport_secure(base_url, api_key):
        raise RuntimeError(
            f"{provider_label} requires HTTPS when an API key is configured; "
            "HTTP is allowed only for loopback endpoints."
        )
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    body = {
        "model": model,
        "messages": _messages_to_openai(messages),
        "tools": TOOLS if tools is None else tools,
        "tool_choice": "auto",
        "stream": True,
    }
    if provider_label != "OpenAI":
        body["temperature"] = 0.2
    token_field = "max_completion_tokens" if provider_label == "OpenAI" else "max_tokens"
    body[token_field] = getattr(config, "LLM_MAX_OUTPUT_TOKENS", 256)

    url = base_url.rstrip("/") + "/chat/completions"
    response = _HTTP_SESSION.post(
        url, headers=headers, json=body, timeout=(10, 60), stream=True
    )
    if response.status_code in (401, 403):
        raise RuntimeError(
            f"{provider_label} rejected the API key - double check it in config.py."
        )
    response.raise_for_status()
    text_chunks = []
    streamed_calls = {}
    first_token_at = None
    started_at = time.time()
    try:
        for event in _iter_sse_json(response, cancel_event):
            choices = event.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            text = delta.get("content") or ""
            if text:
                if first_token_at is None:
                    first_token_at = time.time()
                    telemetry.log(f"[timing] LLM time-to-first-token: {first_token_at - started_at:.2f}s")
                text_chunks.append(text)
                if on_text_delta is not None:
                    on_text_delta(text)
            for call in delta.get("tool_calls") or []:
                index = call.get("index", 0)
                current = streamed_calls.setdefault(
                    index, {"id": None, "name": None, "arguments": []}
                )
                current["id"] = call.get("id") or current["id"]
                function = call.get("function") or {}
                current["name"] = function.get("name") or current["name"]
                if function.get("arguments"):
                    current["arguments"].append(function["arguments"])
    finally:
        response.close()

    tool_calls = []
    for current in streamed_calls.values():
        try:
            args = json.loads("".join(current["arguments"]) or "{}")
        except (ValueError, TypeError):
            args = {}
        tool_calls.append(
            {
                "id": current["id"],
                "function": {"name": current["name"], "arguments": args},
            }
        )

    return {
        "message": {
            "role": "assistant",
            "content": "".join(text_chunks),
            "tool_calls": tool_calls,
        }
    }


def _call_openai(messages, on_text_delta=None, cancel_event=None, tools=None):
    if not config.OPENAI_API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY isn't set. Set it as an environment variable "
            "(or paste it directly into config.py) - see the comments in "
            "config.py for exact steps."
        )
    return _call_openai_compatible(
        messages,
        getattr(config, "OPENAI_BASE_URL", "https://api.openai.com/v1"),
        config.OPENAI_API_KEY,
        config.OPENAI_MODEL,
        "OpenAI",
        on_text_delta,
        cancel_event,
        tools,
    )


def _call_custom_openai(messages, on_text_delta=None, cancel_event=None, tools=None):
    return _call_openai_compatible(
        messages,
        getattr(config, "CUSTOM_BASE_URL", ""),
        getattr(config, "CUSTOM_API_KEY", ""),
        getattr(config, "CUSTOM_MODEL", ""),
        "Your custom provider",
        on_text_delta,
        cancel_event,
        tools,
    )


def _describe_image_openai_compatible(
    image_bytes: bytes, mime_type: str, prompt: str, base_url: str, api_key: str, model: str, provider_label: str
) -> str:
    if not api_key and provider_label != "Your custom provider":
        return (
            f"I can't look at the screen - the {provider_label} API key isn't "
            "set. See the comments in config.py."
        )
    if not model:
        return "I can't look at the screen - no model is configured for this provider in config.py."

    b64 = base64.b64encode(image_bytes).decode("ascii")
    if not is_api_key_transport_secure(base_url, api_key):
        raise RuntimeError(
            f"{provider_label} requires HTTPS when an API key is configured; "
            "HTTP is allowed only for loopback endpoints."
        )
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    body = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64}"}},
                ],
            }
        ],
        "max_tokens": 200,
        "temperature": 0.2,
    }

    url = base_url.rstrip("/") + "/chat/completions"
    try:
        response = _HTTP_SESSION.post(url, headers=headers, json=body, timeout=60)
        response.raise_for_status()
    except requests.exceptions.Timeout:
        return "Looking at the screen timed out - try again in a moment."
    except requests.exceptions.ConnectionError:
        return f"I can't reach {provider_label} to look at the screen - check your internet connection."
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        if e.response is not None:
            print(f"[{provider_label} vision error {status}] {e.response.text}")
        if status in (401, 403):
            return f"{provider_label} rejected my API key - double check it in config.py."
        if status == 429:
            return f"I'm being rate-limited by {provider_label} right now - give it a moment."
        return f"{provider_label} returned an error ({status}) while looking at the screen."

    data = response.json()
    choices = data.get("choices") or []
    if not choices:
        return "I looked, but didn't get a usable description back."
    text = ((choices[0].get("message") or {}).get("content") or "").strip()
    return text or "I looked, but didn't get a usable description back."
