import base64
import time

import requests

from config import PROVIDER_SETTINGS as config
import telemetry

from ..common import _HTTP_SESSION, _iter_sse_json
from .. import usage as _usage
import startup_logging
from ..tool_registry import TOOLS

_gemini_tools_cache = None

_GEMINI_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


_GEMINI_STREAM_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent"


# Set once a model rejects thinkingConfig, so later calls skip the doomed first request.
_thinking_rejected = False


def _record_usage(usage: dict) -> None:
    # Thinking tokens are billed as output but reported apart from candidatesTokenCount.
    output = int(usage.get("candidatesTokenCount") or 0) + int(usage.get("thoughtsTokenCount") or 0)
    _usage.record("gemini", usage.get("promptTokenCount"), output)


def _post_with_thinking_fallback(url, body, **kwargs):
    """POST; if the model 400s on thinkingConfig, retry without it and remember that."""
    global _thinking_rejected
    config_block = body.get("generationConfig", {})
    if _thinking_rejected:
        config_block.pop("thinkingConfig", None)
    response = _HTTP_SESSION.post(url, json=body, **kwargs)
    if response.status_code == 400 and "thinkingConfig" in config_block:
        config_block.pop("thinkingConfig", None)
        response.close()
        response = _HTTP_SESSION.post(url, json=body, **kwargs)
        _thinking_rejected = response.status_code != 400
    return response


def _tools_to_gemini_declarations(tools=None):
    """OpenAI-style tools -> Gemini `functionDeclarations`. Cached on `_gemini_tools_cache`."""
    global _gemini_tools_cache
    tool_list = TOOLS if tools is None else tools
    key = tuple(t["function"]["name"] for t in tool_list)
    if _gemini_tools_cache is None or _gemini_tools_cache[0] != key:
        _gemini_tools_cache = (key, [
            {
                "name": t["function"]["name"],
                "description": t["function"].get("description", ""),
                "parameters": t["function"].get(
                    "parameters", {"type": "object", "properties": {}}
                ),
            }
            for t in tool_list
        ])
    return _gemini_tools_cache[1]


def _messages_to_gemini(messages):
    """OpenAI-style messages -> Gemini `(systemInstruction, contents)`."""
    system_text = None
    contents = []

    for m in messages:
        role = m.get("role")

        if role == "system":
            piece = m.get("content") or ""
            system_text = f"{system_text}\n{piece}" if system_text else piece

        elif role == "user":
            contents.append({"role": "user", "parts": [{"text": m.get("content") or ""}]})

        elif role in ("assistant", "model"):
            parts = []
            text = (m.get("content") or "").strip()
            if text:
                parts.append({"text": text})
            for call in m.get("tool_calls") or []:
                fn = call["function"]
                function_call = {"name": fn["name"], "args": fn.get("arguments") or {}}
                if call.get("id"):
                    function_call["id"] = call["id"]
                part = {"functionCall": function_call}
                if call.get("thought_signature"):
                    part["thoughtSignature"] = call["thought_signature"]
                parts.append(part)
            contents.append({"role": "model", "parts": parts or [{"text": ""}]})

        elif role == "tool":
            function_response = {
                "name": m.get("name", "unknown_function"),
                "response": {"result": m.get("content", "")},
            }
            if m.get("id"):
                # Gemini field is "id", not "call_id" (400s otherwise).
                function_response["id"] = m["id"]
            contents.append(
                {
                    "role": "user",
                    "parts": [{"functionResponse": function_response}],
                }
            )

    return system_text, contents


def _call_gemini(messages, force_tools: bool = False, on_text_delta=None, cancel_event=None, tools=None):
    if not config.GEMINI_API_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY isn't set. Set it as an environment variable "
            "(or paste it directly into config.py) - see the comments in "
            "config.py for exact steps."
        )

    system_text, contents = _messages_to_gemini(messages)
    body = {
        "contents": contents,
        "tools": [{"functionDeclarations": _tools_to_gemini_declarations(tools)}],
        # Gemini 3.x: thinkingLevel not thinkingBudget. Do not add temperature/top_p/top_k (400 on 3.6-flash+).
        "generationConfig": {
            "thinkingConfig": {"thinkingLevel": "minimal"},
            "maxOutputTokens": getattr(config, "LLM_MAX_OUTPUT_TOKENS", 256),
        },
    }

    if not body["tools"][0]["functionDeclarations"]:  # chat-only call
        del body["tools"]
    # Default AUTO so small talk needs no tool. Caller sets force_tools only on
    # retry after a lazy/degenerate no-tool reply — do not force on first turn.
    if force_tools:
        body["toolConfig"] = {"functionCallingConfig": {"mode": "ANY"}}

    if system_text:
        body["systemInstruction"] = {"parts": [{"text": system_text}]}

    url = _GEMINI_STREAM_URL_TEMPLATE.format(model=config.GEMINI_MODEL)
    response = _post_with_thinking_fallback(
        url,
        body,
        params={"key": config.GEMINI_API_KEY, "alt": "sse"},
        timeout=(10, 60),
        stream=True,
    )
    response.raise_for_status()
    text_chunks = []
    tool_calls = []
    block_reason = None
    safety_blocked = False
    first_token_at = None
    started_at = time.time()
    usage = {}
    try:
        for data in _iter_sse_json(response, cancel_event):
            usage = data.get("usageMetadata") or usage  # cumulative; the last chunk has the totals
            block_reason = block_reason or data.get("promptFeedback", {}).get("blockReason")
            candidates = data.get("candidates") or []
            if not candidates:
                continue
            safety_blocked = safety_blocked or candidates[0].get("finishReason") == "SAFETY"
            for part in candidates[0].get("content", {}).get("parts", []):
                delta = part.get("text")
                if delta:
                    if first_token_at is None:
                        first_token_at = time.time()
                        telemetry.log(f"[timing] LLM time-to-first-token: {first_token_at - started_at:.2f}s")
                    text_chunks.append(delta)
                    if on_text_delta is not None:
                        on_text_delta(delta)
                elif "functionCall" in part:
                    fc = part["functionCall"]
                    call = {"function": {"name": fc.get("name"), "arguments": fc.get("args") or {}}}
                    if fc.get("id"):
                        call["id"] = fc["id"]
                    if part.get("thoughtSignature"):
                        call["thought_signature"] = part["thoughtSignature"]
                    if call not in tool_calls:
                        tool_calls.append(call)
    finally:
        response.close()
    _record_usage(usage)

    if block_reason:
        text_chunks = [f"Gemini blocked that request ({block_reason})."]
        tool_calls = []
    elif safety_blocked:
        text_chunks = ["Gemini's safety filters blocked that response."]
        tool_calls = []

    return {
        "message": {
            "role": "assistant",
            "content": "".join(text_chunks),
            "tool_calls": tool_calls,
        }
    }


def _describe_image_gemini(image_bytes: bytes, mime_type: str, prompt: str) -> str:
    if not config.GEMINI_API_KEY:
        return (
            "I can't look at the screen - GEMINI_API_KEY isn't set. See "
            "the comments in config.py, or switch LLM_PROVIDER to "
            "\"ollama\" with a local vision model instead."
        )

    b64 = base64.b64encode(image_bytes).decode("ascii")
    body = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": prompt},
                    {"inlineData": {"mimeType": mime_type, "data": b64}},
                ],
            }
        ],
        # thinkingLevel "minimal" not thinkingBudget (400s on Gemini 3.x).
        "generationConfig": {
            "maxOutputTokens": 200,
            "thinkingConfig": {"thinkingLevel": "minimal"},
        },
    }

    url = _GEMINI_URL_TEMPLATE.format(model=config.GEMINI_MODEL)
    try:
        for attempt in range(3):
            response = _post_with_thinking_fallback(
                url, body, params={"key": config.GEMINI_API_KEY}, timeout=60
            )
            if response.status_code != 503 or attempt == 2:
                response.raise_for_status()
                break
            print(f"[Gemini vision unavailable] Retrying in {attempt + 1}s...")
            time.sleep(attempt + 1)
    except requests.exceptions.Timeout:
        return "Looking at the screen timed out - try again in a moment."
    except requests.exceptions.ConnectionError:
        return "I can't reach the Gemini API to look at the screen - check your internet connection."
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        if e.response is not None:
            print(f"[Gemini vision error {status}] {e.response.text}")
        if status in (401, 403):
            return "Gemini rejected my API key - double check GEMINI_API_KEY in config.py."
        if status == 429:
            return _describe_gemini_429(e.response)
        if status == 503:
            return "Gemini is busy right now, even after retrying - try again in a moment."
        return f"Gemini API returned an error ({status}) while looking at the screen."

    data = response.json()
    _record_usage(data.get("usageMetadata") or {})
    candidates = data.get("candidates") or []
    if not candidates:
        block_reason = data.get("promptFeedback", {}).get("blockReason")
        if block_reason:
            return f"Gemini declined to look at that screenshot ({block_reason})."
        return "I looked, but didn't get a usable description back."

    finish_reason = candidates[0].get("finishReason")
    if finish_reason == "SAFETY":
        return "Gemini's safety filters blocked a description of that screenshot."

    parts = candidates[0].get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts).strip()
    return text or "I looked, but didn't get a usable description back."


def _describe_gemini_429(response) -> str:
    """Gemini 429: short rate limit vs daily quota (quotaId contains 'PerDay').

    Daily quota resets midnight Pacific; retryDelay is not a reliable signal.
    """
    is_daily_quota = False
    try:
        body = response.json()
        for detail in body.get("error", {}).get("details", []):
            for violation in detail.get("violations", []):
                if "PerDay" in (violation.get("quotaId") or ""):
                    is_daily_quota = True
    except Exception as e:
        startup_logging.warn_once("gemini quota parse", e)

    if is_daily_quota:
        print(
            f"NOTE: That's Gemini's free-tier DAILY quota for "
            f"'{config.GEMINI_MODEL}' being fully used up - it resets at "
            "midnight Pacific time, so waiting a few seconds/minutes won't "
            "help. Either switch GEMINI_MODEL in config.py to a model with "
            "a higher free daily quota (e.g. gemini-3.5-flash-lite), or "
            "switch LLM_PROVIDER to \"ollama\" for unlimited free local use."
        )
        return (
            "I've used up today's free Gemini quota for this model - it "
            "won't come back until midnight Pacific time. You can switch "
            "me to a lighter Gemini model, or to the free local Ollama "
            "option, in config.py."
        )
    return "I'm being rate-limited by Gemini right now - give it a moment."
