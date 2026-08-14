import os
import re
from urllib.parse import urlsplit

import requests

def _volume_str_to_percent(value: str, default: int = 0) -> int:
    """Parses config.EDGE_TTS_VOLUME's edge-tts format ('+0%', '-15%',
    '20%') into a plain int for the slider. Falls back to `default` for
    anything unexpected rather than raising, since this runs while just
    building the Settings window."""
    try:
        return int(str(value).strip().rstrip("%"))
    except (TypeError, ValueError):
        return default


def _percent_to_volume_str(percent: int) -> str:
    """Inverse of _volume_str_to_percent - always includes an explicit
    sign (edge-tts accepts '+0%' but this keeps it consistent with the
    +N%/-N% style already used throughout config.py's own comments)."""
    return f"{percent:+d}%"


def _is_http_url(value: str) -> bool:
    if not value:
        return True
    try:
        parsed = urlsplit(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    except ValueError:
        return False


def _verify_gemini_key(api_key: str) -> "tuple[bool, str, list]":
    """Checks whether `api_key` is accepted by Gemini, without spending any
    of the key's generation quota the way an actual chat/vision call would.
    Uses the free "list models" endpoint purely as a cheap auth probe - and,
    since that same response already lists every model the key can use,
    also returns that list so the Settings UI can offer it as a dropdown
    instead of a free-text guess.

    Returns (is_valid, message, models) - message is empty on success, or a
    short human-readable reason on failure; models is a sorted list of
    model names usable for chat (empty on failure or if none qualify).
    Runs a blocking network call, so the caller is expected to run this off
    the GUI thread."""
    try:
        resp = requests.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            params={"key": api_key},
            timeout=10,
        )
    except requests.exceptions.RequestException:
        return False, "Couldn't reach Gemini - check your internet connection.", []

    if resp.status_code == 200:
        models = []
        try:
            for m in resp.json().get("models", []):
                # Only ones that actually support a chat-style call - the
                # list also includes embedding/imagen/etc. models that
                # would just fail if picked here.
                if "generateContent" not in m.get("supportedGenerationMethods", []):
                    continue
                name = m.get("name", "")
                models.append(name.split("/", 1)[1] if "/" in name else name)
        except ValueError:
            pass
        return True, "", sorted(set(models), reverse=True)

    try:
        detail = resp.json().get("error", {}).get("message", "")
    except ValueError:
        detail = ""
    if resp.status_code in (400, 401, 403):
        return False, detail or "Gemini rejected this key.", []
    return False, detail or f"Gemini returned an error ({resp.status_code}).", []


def _verify_openai_key(api_key: str) -> "tuple[bool, str, list]":
    """Same idea as _verify_gemini_key above, but against OpenAI's free
    "list models" endpoint. See that function's docstring for the return
    contract."""
    try:
        resp = requests.get(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
    except requests.exceptions.RequestException:
        return False, "Couldn't reach OpenAI - check your internet connection.", []

    if resp.status_code == 200:
        models = []
        # OpenAI's /v1/models also lists non-chat models (embeddings,
        # Whisper, TTS, DALL-E, moderation) that would just fail here -
        # filter those out so the dropdown only shows usable chat models.
        _non_chat_markers = ("embedding", "whisper", "tts", "dall-e", "moderation", "davinci-", "babbage-")
        try:
            for m in resp.json().get("data", []):
                model_id = m.get("id", "")
                if model_id and not any(marker in model_id for marker in _non_chat_markers):
                    models.append(model_id)
        except ValueError:
            pass
        return True, "", sorted(set(models), reverse=True)

    try:
        detail = resp.json().get("error", {}).get("message", "")
    except ValueError:
        detail = ""
    if resp.status_code in (400, 401, 403):
        return False, detail or "OpenAI rejected this key.", []
    return False, detail or f"OpenAI returned an error ({resp.status_code}).", []


def _verify_anthropic_key(api_key: str) -> "tuple[bool, str, list]":
    """Same idea as _verify_gemini_key above, but against Anthropic's free
    "list models" endpoint. See that function's docstring for the return
    contract."""
    try:
        resp = requests.get(
            "https://api.anthropic.com/v1/models",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
            timeout=10,
        )
    except requests.exceptions.RequestException:
        return False, "Couldn't reach Anthropic - check your internet connection.", []

    if resp.status_code == 200:
        models = []
        try:
            # Anthropic returns these newest-first already - keep that
            # order rather than re-sorting alphabetically, since it's more
            # useful here (newest model shown first).
            models = [m.get("id", "") for m in resp.json().get("data", []) if m.get("id")]
        except ValueError:
            pass
        return True, "", models

    try:
        detail = resp.json().get("error", {}).get("message", "")
    except ValueError:
        detail = ""
    if resp.status_code in (400, 401, 403):
        return False, detail or "Anthropic rejected this key.", []
    return False, detail or f"Anthropic returned an error ({resp.status_code}).", []


def _verify_spotify_credentials(client_id: str, client_secret: str) -> "tuple[bool, str]":
    """Checks whether `client_id`/`client_secret` are accepted by Spotify's
    client-credentials token endpoint - the exact same request
    actions._get_spotify_token() makes, so a green check here means
    play_music's Spotify lookups will actually work."""
    try:
        resp = requests.post(
            "https://accounts.spotify.com/api/token",
            data={"grant_type": "client_credentials"},
            auth=(client_id, client_secret),
            timeout=10,
        )
    except requests.exceptions.RequestException:
        return False, "Couldn't reach Spotify - check your internet connection."

    if resp.status_code == 200:
        return True, ""
    try:
        detail = resp.json().get("error_description", "")
    except ValueError:
        detail = ""
    if resp.status_code in (400, 401, 403):
        return False, detail or "Spotify rejected this Client ID/Secret."
    return False, detail or f"Spotify returned an error ({resp.status_code})."


def _verify_youtube_key(api_key: str) -> "tuple[bool, str]":
    """Checks whether `api_key` is accepted by the YouTube Data API v3.
    Uses the videoCategories endpoint rather than search - it costs a
    fraction of the API quota (1 unit vs. search's 100), so a Verify click
    doesn't eat into the same daily quota play_music's actual lookups use."""
    try:
        resp = requests.get(
            "https://www.googleapis.com/youtube/v3/videoCategories",
            params={"part": "snippet", "regionCode": "US", "key": api_key},
            timeout=10,
        )
    except requests.exceptions.RequestException:
        return False, "Couldn't reach YouTube - check your internet connection."

    if resp.status_code == 200:
        return True, ""
    try:
        detail = resp.json().get("error", {}).get("message", "")
    except ValueError:
        detail = ""
    if resp.status_code in (400, 401, 403):
        return False, detail or "YouTube rejected this key."
    return False, detail or f"YouTube returned an error ({resp.status_code})."


def _fetch_custom_openai_models(base_url: str, api_key: str) -> "tuple[bool, str, list]":
    """Lists models from a custom OpenAI-compatible endpoint (Groq,
    OpenRouter, Together, a local LM Studio/vLLM server, etc.) by hitting
    its standard GET {base_url}/models route - the same endpoint OpenAI's
    own client uses, which most compatible providers implement too.

    Returns (ok, message, models) - same contract as _verify_gemini_key,
    except message is also used for a non-fatal note on success (e.g. a
    server that doesn't support this endpoint at all)."""
    base_url = (base_url or "").rstrip("/")
    if not base_url:
        return False, "Enter a base URL first.", []

    url = base_url if base_url.endswith("/models") else f"{base_url}/models"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
    except requests.exceptions.RequestException as e:
        return False, f"Couldn't reach that server - {e}", []

    if resp.status_code != 200:
        try:
            detail = resp.json().get("error", {}).get("message", "")
        except ValueError:
            detail = ""
        if resp.status_code in (400, 401, 403):
            return False, detail or "That server rejected the request/key.", []
        return False, detail or f"Server returned an error ({resp.status_code}).", []

    try:
        data = resp.json()
    except ValueError:
        return False, "Server responded, but not with valid JSON.", []

    # Most OpenAI-compatible servers use {"data": [{"id": ...}, ...]},
    # same shape as OpenAI itself - fall back to a bare list of strings/
    # dicts in case a server returns something slightly different.
    raw = data.get("data") if isinstance(data, dict) else data
    models = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get("id"):
                models.append(item["id"])
            elif isinstance(item, str):
                models.append(item)

    if not models:
        return False, "Connected, but no models were listed - this server may not support that.", []
    return True, "", sorted(set(models))


def _elevenlabs_voice_display(v: dict) -> str:
    """Combo-box text for one ElevenLabs voice - shows the human name but
    keeps the voice_id recoverable (see _extract_elevenlabs_voice_id)
    since the id, not the name, is what actually goes in config.py."""
    name = v.get("name") or v.get("voice_id", "")
    category = f" ({v['category']})" if v.get("category") else ""
    return f"{name}{category} — {v.get('voice_id', '')}"


def _extract_elevenlabs_voice_id(text: str) -> str:
    """Reverses _elevenlabs_voice_display - pulls the voice_id back out of
    a combo entry. A bare id (no ' — ' separator), e.g. one typed in by
    hand or loaded straight from config.py, passes through unchanged."""
    text = (text or "").strip()
    if " — " in text:
        return text.rsplit(" — ", 1)[-1].strip()
    return text


def _verify_elevenlabs_key(api_key: str) -> "tuple[bool, str, list]":
    """Fetches the account's ElevenLabs voice list as both the auth check
    and the data the Settings voice dropdown needs - there's no separate
    cheap auth-only probe, same as the custom OpenAI-compatible provider
    above. Returns (ok, message, display_strings) - display_strings are
    _elevenlabs_voice_display() text, ready to drop straight into the
    voice combo."""
    import voice as voice_module
    try:
        voices = voice_module.list_elevenlabs_voices(api_key)
    except requests.exceptions.RequestException as e:
        return False, f"Couldn't reach ElevenLabs - {e}", []
    except RuntimeError as e:
        return False, str(e), []
    if not voices:
        return False, "Connected, but no voices are available on this account.", []
    return True, "", [_elevenlabs_voice_display(v) for v in voices]


def _patch_config_line(text: str, key: str, value_literal: str) -> str:
    """Replaces the value of a top-level `KEY = ...` assignment in
    config.py's source text, preserving any trailing inline comment. Adds
    the line at the end if the key isn't already present."""
    import re

    pattern = re.compile(rf"(?m)^{re.escape(key)}\s*=\s*.*?(\s*#.*)?$")

    def _sub(m):
        comment = m.group(1) or ""
        return f"{key} = {value_literal}{comment}"

    new_text, n = pattern.subn(_sub, text, count=1)
    if n == 0:
        new_text = text.rstrip("\n") + f"\n{key} = {value_literal}\n"
    return new_text


def _atomic_write_text(path: str, text: str) -> None:
    """Replace a text file only after its complete new contents reach disk."""
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except OSError:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
