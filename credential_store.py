"""
Secure storage for Alyssa's API keys and other secrets.

Historically every provider key (Gemini, OpenAI, Anthropic, ElevenLabs,
Spotify, YouTube, the custom OpenAI-compatible provider) was either a
plaintext literal in config.py or, when the Settings window edited it,
got patched into config.py on disk as plaintext - a file that isn't
gitignored and that anti-virus/backup/sync tools, screen-share sessions,
and "zip up my project to share" moments can all expose.

This module moves those values into the operating system's native
credential store instead, via the `keyring` package:
  - Windows: Credential Manager
  - macOS: Keychain
  - Linux: Secret Service (GNOME Keyring / KWallet) via SecretStorage

config.py still exposes the same module-level names (GEMINI_API_KEY,
OPENAI_API_KEY, ...) that the rest of the codebase already reads - only
*where the value comes from* changes. Precedence, matching the previous
behavior:
  1. Environment variable (unchanged - still wins, so CI/server/Docker
     deployments that export secrets keep working untouched)
  2. OS keyring entry
  3. "" (unset)

migrate_legacy_plaintext_keys() is a one-time helper main.py calls at
startup: if it finds a real (non-empty) key still sitting in config.py's
own source as a plaintext literal - i.e. this is an existing install
upgrading from before this module existed - it moves that value into
the keyring and blanks the literal in config.py, instead of silently
leaving the old plaintext copy on disk forever.
"""
import os
import re

SERVICE_NAME = "Alyssa Assistant"

# Every secret Alyssa stores, and the environment variable (if any) that
# still overrides it. Order/names match config.py's existing *_API_KEY /
# *_CLIENT_* attributes so callers can loop over this instead of
# hardcoding the list in more than one place.
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
    """True if the `keyring` package is installed AND it found a real OS
    backend (not keyring's own "fail" backend, which raises on every call
    - happens on minimal Linux installs with no Secret Service running)."""
    if keyring is None:
        return False
    try:
        backend = keyring.get_keyring()
    except Exception:
        return False
    return type(backend).__module__ != "keyring.backends.fail"


def get_secret(name: str) -> str:
    """Reads a secret by its config.py attribute name (e.g.
    "GEMINI_API_KEY"). Checks the matching environment variable first,
    then the OS keyring. Always returns a string ("" if unset) so callers
    can keep doing `if not config.GEMINI_API_KEY:` unchanged."""
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
    """Writes (or, if value is empty, deletes) a secret in the OS
    keyring. Returns True on success. Never touches config.py - the
    caller is responsible for not also writing the raw value there."""
    if not _keyring_available():
        return False
    try:
        if value:
            keyring.set_password(SERVICE_NAME, name, value)
        else:
            try:
                keyring.delete_password(SERVICE_NAME, name)
            except keyring.errors.PasswordDeleteError:
                pass  # already absent - fine
        return True
    except keyring.errors.KeyringError:
        return False


def storage_backend_name() -> str:
    """Human-readable name of the active keyring backend, for the
    Settings window to show ("Stored in: Windows Credential Manager")
    or to explain why secure storage isn't available."""
    if keyring is None:
        return "unavailable (the 'keyring' package isn't installed)"
    if not _keyring_available():
        return "unavailable (no OS credential store found - see README)"
    return type(keyring.get_keyring()).__name__


_LITERAL_RE_TEMPLATE = r"^{name}\s*=\s*(['\"])(.*)\1\s*$"


def migrate_legacy_plaintext_keys(config_path: str) -> "list[str]":
    """One-time upgrade path: scans config.py for any of the secret
    attributes above that are still hardcoded as a non-empty plaintext
    string literal (the old storage method), moves that value into the
    OS keyring, and blanks the literal in config.py so the plaintext
    copy doesn't keep living on disk (and, for anyone who later commits
    config.py, in git history from that point forward).

    Deliberately only touches simple `NAME = 'value'` / `NAME = "value"`
    literals - never `NAME = os.environ.get(...)` lines, which were
    already safe. Returns the list of attribute names migrated, so
    main.py can log what happened."""
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
            continue  # already blank - nothing to migrate
        if set_secret(name, value):
            quote = match.group(1)
            text = pattern.sub(f"{name} = {quote}{quote}", text, count=1)
            migrated.append(name)

    if migrated:
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(text)
        except OSError:
            pass  # keyring already has the values; config.py cleanup can retry next launch

    return migrated
