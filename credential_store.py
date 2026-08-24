"""OS keyring for Alyssa secrets. config.py names unchanged; only the value source.

Precedence: env var ALWAYS wins > keyring > "" (unset).

migrate_legacy_plaintext_keys() (main.py, before importing config): move leftover
plaintext literals from config.py into the keyring and blank them.
"""
import json
import os
import re

SERVICE_NAME = "Alyssa Assistant"

# Secret name -> override env var (None = no env override). Names match config.py.
SECRET_ENV_VARS = {
    "GEMINI_API_KEY": "GEMINI_API_KEY",
    "OPENAI_API_KEY": "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY": "ANTHROPIC_API_KEY",
    "CUSTOM_API_KEY": "CUSTOM_LLM_API_KEY",
    "ELEVENLABS_API_KEY": None,
    "SPOTIFY_CLIENT_ID": "SPOTIFY_CLIENT_ID",
    "SPOTIFY_CLIENT_SECRET": "SPOTIFY_CLIENT_SECRET",
    "YOUTUBE_API_KEY": "YOUTUBE_API_KEY",
}

try:
    import keyring
    import keyring.errors
    _KEYRING_IMPORT_ERROR = None
except ImportError as e:  # pragma: no cover - exercised via _keyring_available()
    keyring = None
    _KEYRING_IMPORT_ERROR = e


def _keyring_available() -> bool:
    """True if `keyring` is installed with a real OS backend (not the "fail" backend)."""
    if keyring is None:
        return False
    try:
        backend = keyring.get_keyring()
    except Exception:
        return False
    return type(backend).__module__ != "keyring.backends.fail"


def get_secret(name: str) -> str:
    """Return secret `name` (config.py attribute, e.g. GEMINI_API_KEY).
    Precedence: matching env var ALWAYS wins, then OS keyring. Always a str; "" if unset."""
    env_var = SECRET_ENV_VARS.get(name)
    if env_var:
        env_value = os.environ.get(env_var)
        if env_value:
            return env_value

    if not _keyring_available():
        return ""
    try:
        return keyring.get_password(SERVICE_NAME, name) or ""
    except keyring.errors.KeyringError:
        return ""


def set_secret(name: str, value: str) -> bool:
    """Write secret `name` to the OS keyring, or delete it if `value` is empty.
    Returns True on success. Does not touch config.py."""
    if not _keyring_available():
        return False
    try:
        if value:
            keyring.set_password(SERVICE_NAME, name, value)
        else:
            try:
                keyring.delete_password(SERVICE_NAME, name)
            except keyring.errors.PasswordDeleteError:
                pass  # already absent — success
        return True
    except keyring.errors.KeyringError:
        return False


def get_json_secret(name: str):
    """JSON from secure storage, or None if absent/invalid."""
    raw = get_secret(name)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def set_json_secret(name: str, value) -> bool:
    """Store a JSON-serializable value. False if serialization fails."""
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return False
    return set_secret(name, encoded)


def storage_backend_name() -> str:
    """Active keyring backend name for Settings UI, or why storage is unavailable."""
    if keyring is None:
        return "unavailable (the 'keyring' package isn't installed)"
    if not _keyring_available():
        return "unavailable (no OS credential store found - see README)"
    return type(keyring.get_keyring()).__name__


_LITERAL_RE_TEMPLATE = r"^{name}\s*=\s*(['\"])(.*)\1\s*(?:#.*)?$"


def migrate_legacy_plaintext_keys(config_path: str) -> "list[str]":
    """One-shot upgrade of leftover plaintext secrets in config.py.
    - Matches only `NAME = 'value'` / `NAME = "value"` (not os.environ.get)
    - On hit: write to keyring, blank the literal
    - Returns migrated attribute names ([] if keyring unavailable or read fails)
    - Rewrites config.py only if at least one name migrated
    - If rewrite fails: keyring already has the values; retries next launch
    """
    if not _keyring_available():
        return []
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return []

    migrated = []
    for name in SECRET_ENV_VARS:
        pattern = re.compile(_LITERAL_RE_TEMPLATE.format(name=re.escape(name)), re.MULTILINE)
        match = pattern.search(text)
        if not match:
            continue
        value = match.group(2)
        if not value:
            continue  # already blank
        if set_secret(name, value):
            quote = match.group(1)
            text = pattern.sub(f"{name} = {quote}{quote}", text, count=1)
            migrated.append(name)

    if migrated:
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(text)
        except OSError:
            pass  # keyring already has the values; config.py cleanup retries next launch

    return migrated
