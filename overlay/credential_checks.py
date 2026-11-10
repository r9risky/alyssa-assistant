import os
import re

import requests

from brain.common import is_api_key_transport_secure

def _volume_str_to_percent(value: str, default: int = 0) -> int:
    """EDGE_TTS_VOLUME ('+0%', '-15%', '20%') → int for the slider. Returns `default` on bad input (Settings build)."""
    try:
        return int(str(value).strip().rstrip("%"))
    except (TypeError, ValueError):
        return default


def _percent_to_volume_str(percent: int) -> str:
    """Inverse of _volume_str_to_percent; always signed (`+0%`, `-15%`)."""
    return f"{percent:+d}%"


def _is_http_url(value: str, api_key: str = "") -> bool:
    if not value:
        return True
    return is_api_key_transport_secure(value, api_key)


def _verify_gemini_key(api_key: str) -> "tuple[bool, str, list]":
    """Auth-probe Gemini via GET /v1beta/models (no generation quota). Blocking; run off the GUI thread.

    Returns (ok, message, models): message empty on success; models are generateContent names, sorted desc (empty on failure).
    """
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
    """Auth-probe OpenAI GET /v1/models. Same return as _verify_gemini_key; drops embedding/whisper/tts/dall-e/moderation/davinci/babbage ids."""
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
    """Auth-probe Anthropic GET /v1/models. Same return contract as _verify_gemini_key, but keep API order (newest first)."""
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
    """Probe Spotify client-credentials token (same request as actions._get_spotify_token). Returns (ok, message)."""
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
    """Probe YouTube Data API v3 via videoCategories (1 quota unit, not search's 100). Returns (ok, message)."""
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
    """GET {base_url}/models on an OpenAI-compatible server. Blocking.

    Returns (ok, message, models) like _verify_gemini_key; message may be a non-fatal note.
    HTTPS required when sending an API key; HTTP only for loopback. Appends /models unless already present.
    """
    base_url = (base_url or "").rstrip("/")
    if not base_url:
        return False, "Enter a base URL first.", []
    if not _is_http_url(base_url, api_key):
        return False, "Use HTTPS when sending an API key; HTTP is allowed only for loopback.", []

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

    # Prefer {"data": [{"id": ...}]}; else a bare list of ids / dicts.
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
    """Combo text: `{name} ({category}) — {voice_id}` so the id is recoverable."""
    name = v.get("name") or v.get("voice_id", "")
    category = f" ({v['category']})" if v.get("category") else ""
    return f"{name}{category} — {v.get('voice_id', '')}"


def _extract_elevenlabs_voice_id(text: str) -> str:
    """Inverse of _elevenlabs_voice_display: id after ` — `, or the whole string if none."""
    text = (text or "").strip()
    if " — " in text:
        return text.rsplit(" — ", 1)[-1].strip()
    return text


def _verify_elevenlabs_key(api_key: str) -> "tuple[bool, str, list]":
    """List ElevenLabs voices as auth + dropdown data (no cheaper probe). Returns (ok, message, display_strings)."""
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


def _verify_groq_key(api_key: str) -> "tuple[bool, str, list]":
    """Auth-probe Groq via GET https://api.groq.com/openai/v1/models.
    Returns (ok, message, whisper_models).
    """
    try:
        resp = requests.get(
            "https://api.groq.com/openai/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
    except requests.exceptions.RequestException:
        return False, "Couldn't reach Groq - check your internet connection.", []

    if resp.status_code == 200:
        whisper_models = []
        try:
            for m in resp.json().get("data", []):
                model_id = m.get("id", "")
                if "whisper" in model_id.lower():
                    whisper_models.append(model_id)
        except ValueError:
            pass
        if not whisper_models:
            whisper_models = ["whisper-large-v3-turbo", "whisper-large-v3"]
        return True, "", sorted(set(whisper_models), reverse=True)

    try:
        detail = resp.json().get("error", {}).get("message", "")
    except ValueError:
        detail = ""
    if resp.status_code in (400, 401, 403):
        return False, detail or "Groq rejected this key.", []
    return False, detail or f"Groq returned an error ({resp.status_code}).", []


def _patch_config_line(text: str, key: str, value_literal: str) -> str:
    """Replace a top-level `KEY = ...` in config.py text; keep trailing comments. Append if missing."""
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
