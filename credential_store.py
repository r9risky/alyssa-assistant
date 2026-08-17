"""Secure local storage for LLM provider credentials.

LLM API keys are intentionally kept out of ``config.py`` and the project tree.
On Windows they are encrypted at rest with DPAPI for the current user and
stored under ``%LOCALAPPDATA%\\AlyssaAi``. On other platforms the credentials
file is stored in the user's config directory with owner-only permissions.

Environment variables always take precedence, which keeps CI/deployment
workflows simple and avoids writing secrets to disk when that is preferred.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import tempfile
from typing import Mapping


LLM_CREDENTIAL_ENV = {
    "GEMINI_API_KEY": "GEMINI_API_KEY",
    "OPENAI_API_KEY": "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY": "ANTHROPIC_API_KEY",
    "CUSTOM_API_KEY": "CUSTOM_LLM_API_KEY",
}

_DPAPI_PREFIX = b"ALYSSA_DPAPI_V1\n"
_PLAIN_PREFIX = b"ALYSSA_JSON_V1\n"


def _data_dir() -> Path:
    override = os.environ.get("ALYSSA_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if root:
            return Path(root) / "AlyssaAi"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg).expanduser() / "AlyssaAi"
    return Path.home() / ".config" / "AlyssaAi"


def credentials_path() -> Path:
    """Return the per-user credential file path (outside the project tree)."""
    return _data_dir() / "llm_credentials.dat"


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes):
    buffer = ctypes.create_string_buffer(data)
    return _DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def _dpapi_protect(data: bytes) -> bytes:
    in_blob, in_buffer = _blob(data)
    out_blob = _DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    # CRYPTPROTECT_UI_FORBIDDEN: do not display OS prompts while Settings saves.
    if not crypt32.CryptProtectData(
        ctypes.byref(in_blob), "Alyssa LLM credentials", None, None, None,
        0x1, ctypes.byref(out_blob),
    ):
        raise OSError(ctypes.get_last_error(), "Windows DPAPI encryption failed")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)
        del in_buffer


def _dpapi_unprotect(data: bytes) -> bytes:
    in_blob, in_buffer = _blob(data)
    out_blob = _DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    if not crypt32.CryptUnprotectData(
        ctypes.byref(in_blob), None, None, None, None, 0x1, ctypes.byref(out_blob)
    ):
        raise OSError(ctypes.get_last_error(), "Windows DPAPI decryption failed")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)
        del in_buffer


def _encode(credentials: Mapping[str, str]) -> bytes:
    payload = json.dumps(credentials, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if os.name == "nt":
        return _DPAPI_PREFIX + _dpapi_protect(payload)
    return _PLAIN_PREFIX + payload


def _decode(raw: bytes) -> dict[str, str]:
    if raw.startswith(_DPAPI_PREFIX):
        if os.name != "nt":
            return {}
        payload = _dpapi_unprotect(raw[len(_DPAPI_PREFIX):])
    elif raw.startswith(_PLAIN_PREFIX):
        payload = raw[len(_PLAIN_PREFIX):]
    else:
        # Do not interpret arbitrary files as credentials.
        return {}
    parsed = json.loads(payload.decode("utf-8"))
    if not isinstance(parsed, dict):
        return {}
    return {
        key: str(value)
        for key, value in parsed.items()
        if key in LLM_CREDENTIAL_ENV and isinstance(value, str)
    }


def load_llm_credentials() -> dict[str, str]:
    path = credentials_path()
    try:
        return _decode(path.read_bytes())
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}


def get_llm_credential(name: str) -> str:
    """Return an LLM key, preferring its environment variable over disk."""
    env_name = LLM_CREDENTIAL_ENV[name]
    if env_name in os.environ:
        return os.environ.get(env_name, "")
    return load_llm_credentials().get(name, "")


def save_llm_credentials(values: Mapping[str, str]) -> None:
    """Merge and persist LLM credentials without placing them in the repo."""
    current = load_llm_credentials()
    for key, value in values.items():
        if key not in LLM_CREDENTIAL_ENV:
            continue
        clean = str(value).strip()
        if clean:
            current[key] = clean
        else:
            current.pop(key, None)

    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass

    encoded = _encode(current)
    fd, temp_name = tempfile.mkstemp(prefix=".llm_credentials-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(temp_name, 0o600)
        except OSError:
            pass
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
