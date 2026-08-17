import base64
import json
import time

import requests

import config
import telemetry

from ..common import _HTTP_SESSION
from ..tool_registry import TOOLS

_anthropic_tools_cache = None

_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


_ANTHROPIC_VERSION = "2023-06-01"


def _tools_to_anthropic():
    """Converts our OpenAI-style TOOLS list into Anthropic's tool schema -
    same JSON Schema `parameters`, just renamed to `input_schema` and
    flattened (no nested "function" wrapper). Cached - see
    _anthropic_tools_cache above."""
    global _anthropic_tools_cache
    if _anthropic_tools_cache is None:
        _anthropic_tools_cache = [
            {
                "name": t["function"]["name"],
                "description": t["function"].get("description", ""),
                "input_schema": t["function"].get(
                    "parameters", {"type": "object", "properties": {}}
                ),
            }
            for t in TOOLS
        ]
    return _anthropic_tools_cache


def _messages_to_anthropic(messages):
    """Converts our internal message list into Anthropic's (system,
    messages) shape, where tool calls/results are content blocks rather
    than separate message roles."""
    system_text = None
    out = []

    for m in messages:
        role = m.get("role")

        if role == "system":
            piece = m.get("content") or ""
            system_text = f"{system_text}\n{piece}" if system_text else piece

        elif role == "user":
            out.append({"role": "user", "content": m.get("content") or ""})

        elif role in ("assistant", "model"):
            blocks = []
            text = (m.get("content") or "").strip()
            if text:
                blocks.append({"type": "text", "text": text})
            for call in m.get("tool_calls") or []:
                fn = call["function"]
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call.get("id") or f"call_{len(blocks)}",
                        "name": fn["name"],
                        "input": fn.get("arguments") or {},
                    }
                )
            out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})

        elif role == "tool":
            out.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": m.get("id") or "",
                            "content": str(m.get("content", "")),
                        }
                    ],
                }
            )

    return system_text, out


def _call_anthropic(messages, on_text_delta=None, cancel_event=None):
    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError(
            "ANTHROPIC_API_KEY isn't set. Add it in Settings or set the ANTHROPIC_API_KEY environment variable."
        )

    system_text, anthropic_messages = _messages_to_anthropic(messages)
    body = {
        "model": config.ANTHROPIC_MODEL,
        "max_tokens": getattr(config, "LLM_MAX_OUTPUT_TOKENS", 256),
        "messages": anthropic_messages,
        "tools": _tools_to_anthropic(),
        "temperature": 0.2,
        "stream": True,
    }
    if system_text:
        body["system"] = system_text

    headers = {
        "x-api-key": config.ANTHROPIC_API_KEY,
        "anthropic-version": _ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    response = _HTTP_SESSION.post(
        _ANTHROPIC_URL, headers=headers, json=body, timeout=(10, 60), stream=True
    )
    if response.status_code in (401, 403):
        raise RuntimeError("Anthropic rejected the API key - double check it in Settings or ANTHROPIC_API_KEY.")
    response.raise_for_status()
    text_chunks = []
    streamed_calls = {}
    first_token_at = None
    started_at = time.time()
    try:
        for event in _iter_sse_json(response, cancel_event):
            kind = event.get("type")
            index = event.get("index", 0)
            if kind == "content_block_start":
                block = event.get("content_block") or {}
                if block.get("type") == "tool_use":
                    streamed_calls[index] = {
                        "id": block.get("id"),
                        "name": block.get("name"),
                        "arguments": [],
                    }
            elif kind == "content_block_delta":
                delta = event.get("delta") or {}
                if delta.get("type") == "text_delta":
                    text = delta.get("text") or ""
                    if text:
                        if first_token_at is None:
                            first_token_at = time.time()
                            telemetry.log(f"[timing] LLM time-to-first-token: {first_token_at - started_at:.2f}s")
                        text_chunks.append(text)
                        if on_text_delta is not None:
                            on_text_delta(text)
                elif delta.get("type") == "input_json_delta":
                    current = streamed_calls.setdefault(
                        index, {"id": None, "name": None, "arguments": []}
                    )
                    current["arguments"].append(delta.get("partial_json") or "")
    finally:
        response.close()

    tool_calls = []
    for current in streamed_calls.values():
        try:
            arguments = json.loads("".join(current["arguments"]) or "{}")
        except (ValueError, TypeError):
            arguments = {}
        tool_calls.append(
            {
                "id": current["id"],
                "function": {"name": current["name"], "arguments": arguments},
            }
        )

    return {
        "message": {
            "role": "assistant",
            "content": "".join(text_chunks),
            "tool_calls": tool_calls,
        }
    }


def _describe_image_anthropic(image_bytes: bytes, mime_type: str, prompt: str) -> str:
    if not config.ANTHROPIC_API_KEY:
        return (
            "I can't look at the screen - ANTHROPIC_API_KEY isn't set. See "
            "the comments in config.py."
        )

    b64 = base64.b64encode(image_bytes).decode("ascii")
    body = {
        "model": config.ANTHROPIC_MODEL,
        "max_tokens": 200,
        "temperature": 0.2,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": mime_type, "data": b64},
                    },
                ],
            }
        ],
    }
    headers = {
        "x-api-key": config.ANTHROPIC_API_KEY,
        "anthropic-version": _ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    try:
        response = _HTTP_SESSION.post(_ANTHROPIC_URL, headers=headers, json=body, timeout=60)
        response.raise_for_status()
    except requests.exceptions.Timeout:
        return "Looking at the screen timed out - try again in a moment."
    except requests.exceptions.ConnectionError:
        return "I can't reach the Anthropic API to look at the screen - check your internet connection."
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        if e.response is not None:
            print(f"[Anthropic vision error {status}] {e.response.text}")
        if status in (401, 403):
            return "Anthropic rejected my API key - double check it in Settings or ANTHROPIC_API_KEY."
        if status == 429:
            return "I'm being rate-limited by Anthropic right now - give it a moment."
        return f"Anthropic returned an error ({status}) while looking at the screen."

    data = response.json()
    text = "".join(b.get("text", "") for b in data.get("content") or [] if b.get("type") == "text").strip()
    return text or "I looked, but didn't get a usable description back."
