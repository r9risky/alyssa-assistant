import base64
import time

import requests

import config
import telemetry

from ..common import GenerationCancelled, _HTTP_SESSION, _iter_sse_json
from ..dialogue import TOOLS

_gemini_tools_cache = None

_GEMINI_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


_GEMINI_STREAM_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent"


def _tools_to_gemini_declarations():
    """Converts our OpenAI-style TOOLS list into Gemini's functionDeclarations
    format. The parameters schema itself (JSON Schema) is compatible as-is.
    Cached - see _gemini_tools_cache above."""
    global _gemini_tools_cache
    if _gemini_tools_cache is None:
        _gemini_tools_cache = [
            {
                "name": t["function"]["name"],
                "description": t["function"].get("description", ""),
                "parameters": t["function"].get(
                    "parameters", {"type": "object", "properties": {}}
                ),
            }
            for t in TOOLS
        ]
    return _gemini_tools_cache


def _messages_to_gemini(messages):
    """Converts our internal OpenAI-style message list into Gemini's
    (systemInstruction, contents) shape."""
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
                # Field name Gemini expects here is "id" (matching the
                # functionCall part above), not "call_id" - the API 400s
                # ("Unknown name \"call_id\"") if it's wrong.
                function_response["id"] = m["id"]
            contents.append(
                {
                    "role": "user",
                    "parts": [{"functionResponse": function_response}],
                }
            )

    return system_text, contents


def _call_gemini(messages, force_tools: bool = False, on_text_delta=None, cancel_event=None):
    if not config.GEMINI_API_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY isn't set. Set it as an environment variable "
            "(or paste it directly into config.py) - see the comments in "
            "config.py for exact steps."
        )

    system_text, contents = _messages_to_gemini(messages)
    body = {
        "contents": contents,
        "tools": [{"functionDeclarations": _tools_to_gemini_declarations()}],
        # Gemini 3.x models prefer thinkingLevel ("minimal"/"low"/"medium"/
        # "high") over the older numeric thinkingBudget, which Google's docs
        # call unpredictable on 3.x. "minimal" keeps this fixed-vocabulary
        # "pick a tool + args" task fast and cheap - fires on every voice
        # command, up to handle_command()'s max_turns=6 times per command.
        "generationConfig": {
            "thinkingConfig": {"thinkingLevel": "minimal"},
            "maxOutputTokens": getattr(config, "LLM_MAX_OUTPUT_TOKENS", 256),
        },
        # temperature/top_p/top_k are deprecated for gemini-3.6-flash+ and
        # 400 if present - don't add them back.
    }

    # Gemini's function calling defaults to AUTO (model decides whether to
    # call a tool), left alone so small talk/trivia get a plain text reply
    # with no tool call. The catch: smaller/faster models sometimes take
    # the lazy path even on a real command ("Certainly." with no tool
    # call). Rather than forcing every first turn into a tool call (which
    # broke plain questions and "say X"), the caller retries just this one
    # turn with force_tools=True only if AUTO came back with no tool call
    # and a reply that looks like that exact dodge (see _is_degenerate_reply).
    if force_tools:
        body["toolConfig"] = {"functionCallingConfig": {"mode": "ANY"}}

    if system_text:
        body["systemInstruction"] = {"parts": [{"text": system_text}]}

    url = _GEMINI_STREAM_URL_TEMPLATE.format(model=config.GEMINI_MODEL)
    response = _HTTP_SESSION.post(
        url,
        params={"key": config.GEMINI_API_KEY, "alt": "sse"},
        json=body,
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
    try:
        for data in _iter_sse_json(response, cancel_event):
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
        # Keep this cheap: it's a plain "describe what you see" call, not a
        # reasoning task, so no reason to spend thinking tokens (billed as
        # output tokens). maxOutputTokens caps the reply to 1-2 sentences,
        # since handle_command() re-phrases this into the final spoken reply.
        # thinkingLevel "minimal" (not the older numeric thinkingBudget,
        # which 400s here on Gemini 3.x) is the documented, reliable choice.
        "generationConfig": {
            "maxOutputTokens": 200,
            "thinkingConfig": {"thinkingLevel": "minimal"},
        },
    }

    url = _GEMINI_URL_TEMPLATE.format(model=config.GEMINI_MODEL)
    try:
        for attempt in range(3):
            response = _HTTP_SESSION.post(
                url, params={"key": config.GEMINI_API_KEY}, json=body, timeout=60
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
    """Gemini returns HTTP 429 for two different situations needing
    different advice: a short-lived rate limit (clears on its own) vs. a
    fully exhausted daily quota (doesn't clear until midnight Pacific, so
    "wait a moment" is misleading). Google's error body includes a quotaId
    like 'GenerateRequestsPerDayPerProjectPerModel-FreeTier' for the daily
    case - the one reliable signal, since retryDelay can't be trusted alone."""
    is_daily_quota = False
    try:
        body = response.json()
        for detail in body.get("error", {}).get("details", []):
            for violation in detail.get("violations", []):
                if "PerDay" in (violation.get("quotaId") or ""):
                    is_daily_quota = True
    except Exception:
        pass

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
