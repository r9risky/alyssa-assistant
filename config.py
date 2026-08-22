"""
Central Configuration Settings for Alyssa Assistant.
"""
from typing import Any

import credential_store as _cred

# --- Identity ---
ASSISTANT_NAME = 'Alyssa'
CREATOR_NAME = "Riya"
ASSISTANT_NAME_ALIASES = [
    "alyssa", "alissa", "alisa", "alysa", "aleesa", "aleessa",
    "alicia", "elisa", "elissa", "melissa", "larissa",
]


# --- LLM Provider ("ollama", "gemini", "openai", "anthropic", "custom_openai") ---
LLM_PROVIDER = 'gemini'
LLM_STRONG_PROVIDER = 'anthropic'
LLM_REASONING_TIER = 'auto'  # "auto", "fast", or "strong"

# --- Ollama Settings ---
OLLAMA_MODEL = 'qwen2.5:3b'
OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_KEEP_ALIVE = "10m"

# --- Gemini Settings ---
GEMINI_API_KEY = _cred.get_secret('GEMINI_API_KEY')  # env var GEMINI_API_KEY still overrides
GEMINI_MODEL = 'gemini-3.5-flash-lite'

# --- OpenAI Settings ---
OPENAI_API_KEY = _cred.get_secret('OPENAI_API_KEY')  # env var OPENAI_API_KEY still overrides
OPENAI_MODEL = 'gpt-5-mini'
OPENAI_BASE_URL = "https://api.openai.com/v1"

# --- Integrations & APIs ---
SPOTIFY_CLIENT_ID = _cred.get_secret('SPOTIFY_CLIENT_ID')  # env var SPOTIFY_CLIENT_ID still overrides
SPOTIFY_CLIENT_SECRET = _cred.get_secret('SPOTIFY_CLIENT_SECRET')  # env var SPOTIFY_CLIENT_SECRET still overrides
YOUTUBE_API_KEY = _cred.get_secret('YOUTUBE_API_KEY')  # env var YOUTUBE_API_KEY still overrides

# --- Anthropic Settings ---
ANTHROPIC_API_KEY = _cred.get_secret('ANTHROPIC_API_KEY')  # env var ANTHROPIC_API_KEY still overrides
ANTHROPIC_MODEL = 'claude-sonnet-5'

# --- Custom OpenAI-Compatible Provider ---
CUSTOM_API_KEY = _cred.get_secret('CUSTOM_API_KEY')  # env var CUSTOM_LLM_API_KEY still overrides
CUSTOM_BASE_URL = 'https://openrouter.ai/api/v1'
CUSTOM_MODEL = 'meta-llama/llama-3.3-70b-instruct'
LLM_MAX_OUTPUT_TOKENS = 256



# --- Screen Vision ---
OLLAMA_VISION_MODEL = "llava"
SCREEN_VISION_MAX_DIMENSION = 768

# --- Speech-to-Text (Whisper) ---
# "auto" uses ElevenLabs' persistent realtime WebSocket when a key is
# configured, otherwise it preserves the local Faster Whisper fallback.
STT_PROVIDER = "auto"  # "auto", "elevenlabs_realtime", or "local"
STT_REALTIME_MODEL = "scribe_v2_realtime"
STT_LANGUAGE = "en"
STT_FINAL_TIMEOUT_SECONDS = 2.0
WHISPER_MODEL_SIZE = 'base.en'
WHISPER_DEVICE = 'auto'
WHISPER_COMPUTE_TYPE = 'auto'
WHISPER_CPU_THREADS = 0
WHISPER_NUM_WORKERS = 1
WHISPER_LOG_PROB_THRESHOLD = -1.0
WHISPER_INITIAL_PROMPT = (
    "Alyssa, open Chrome, open Notepad, open Spotify, open Windows Explorer, "
    "search for files, delete file, run command, remember that, type this, "
    "Arknights Endfield."
)
VOCABULARY_CORRECTIONS = [
    ("Arknights Endfield", ["arknights and field", "arc knights endfield", "arc nights and field"]),
]

# --- Audio Recording ---
MICROPHONE_DEVICE = 'default'
AUDIO_OUTPUT_DEVICE = 'default'  # "default" or a (partial) speaker/headset name
SAMPLE_RATE = 16000
SILENCE_SECONDS = 0.30
MAX_RECORD_SECONDS = 15
VAD_AGGRESSIVENESS = 2
MIN_SPEECH_MS = 120

# --- Adaptive Silence Timeout ---
ADAPTIVE_SILENCE_ENABLED = True
ADAPTIVE_SILENCE_MIN_SECONDS = 0.24
ADAPTIVE_SILENCE_MAX_SECONDS = 0.42
ADAPTIVE_SILENCE_FAST_WPS = 3.3
ADAPTIVE_SILENCE_SLOW_WPS = 1.3
ADAPTIVE_SILENCE_EMA_ALPHA = 0.3

FAST_TOOL_RESPONSES = True

# --- Text-to-Speech (TTS) ---
SPEAK_RESPONSES = True
TTS_PROVIDER = 'edge'  # "edge" or "elevenlabs"

# Edge TTS Options
EDGE_TTS_VOICE = 'ja-JP-NanamiNeural'
EDGE_TTS_RATE = "+0%"
EDGE_TTS_VOLUME = '+0%'
EDGE_TTS_PITCH = "+0Hz"

# ElevenLabs Options
ELEVENLABS_API_KEY = _cred.get_secret('ELEVENLABS_API_KEY')  # env var not supported for this one; use the Settings window
ELEVENLABS_VOICE_ID = ''
ELEVENLABS_MODEL = 'eleven_flash_v2_5'


TTS_PIPELINE_SENTENCES = True
TTS_STREAMING_ENABLED = True
TTS_AUDIO_BUFFER_MS = 100
TTS_CLAUSE_MIN_CHARACTERS = 28

# --- Interruption / Barge-in ---
ALLOW_INTERRUPTIONS = True
BARGE_IN_REQUIRE_NAME = True
BARGE_IN_MIN_SPEECH_MS = 150
BARGE_IN_VAD_AGGRESSIVENESS = 3

# --- Memory & Context ---
CONVERSATION_MEMORY_TURNS = 11
CONVERSATION_MEMORY_CHARACTERS = 4000
MAX_SAVED_MEMORIES = 75
MAX_MEMORY_FACT_CHARACTERS = 400
MAX_MEMORIES_IN_PROMPT = 8
RECENT_ACTION_CONTEXT_LIMIT = 6
POWER_CONFIRMATION_TIMEOUT_SECONDS = 60
FOLLOWUP_GRACE_SECONDS = 20
CONVERSATION_TIMEOUT_SECONDS = 300

# --- Plugins & Watchers ---
PLUGINS_ENABLED = True
ENABLE_BACKGROUND_WATCHER = True
SYSTEM_WATCH_ENABLED = False
SYSTEM_WATCH_INTERVAL_SECONDS = 300
WEATHER_DEFAULT_LOCATION = ''
WEATHER_UNITS = 'metric'
AUTO_DETECT_LOCATION = True
LOCATION_CACHE_MINUTES = 60
REMINDER_UPCOMING_WINDOW_HOURS = 24

# --- Desktop Companion GUI ---
ENABLE_COMPANION_GUI = True
HIDE_CONSOLE_WINDOW = False  # Keep startup diagnostics visible by default

# --- Caveman Mode (runtime-toggled via plugins/caveman_mode.py) ---
# None = off. Otherwise "lite" / "full" / "ultra" - shrinks Alyssa's own
# reply length by instructing the LLM to speak tersely; never touches what
# she knows or does, only how many words she uses to say it.
CAVEMAN_MODE = None

# --- App Launching & Safety ---
LAUNCH_APPS_IN_BACKGROUND = True
APP_LAUNCH_SETTLE_SECONDS = 1.5
DEBUG_PRINT_TRANSCRIPTS = True
CONFIRM_BEFORE_ACTIONS = False


class _SettingsView:
    """Typed live view over the legacy module-level settings."""

    def __init__(self, *names: str):
        object.__setattr__(self, "_names", frozenset(names))

    def __getattr__(self, name: str) -> Any:
        if name not in self._names:
            raise AttributeError(name)
        try:
            return globals()[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name: str, value: Any) -> None:
        if name not in self._names:
            raise AttributeError(name)
        globals()[name] = value

    def __delattr__(self, name: str) -> None:
        if name not in self._names:
            raise AttributeError(name)
        del globals()[name]

    def __dir__(self):
        return sorted(self._names)


class ProviderSettings(_SettingsView):
    LLM_PROVIDER: str
    LLM_STRONG_PROVIDER: str
    LLM_REASONING_TIER: str
    OLLAMA_MODEL: str
    OLLAMA_URL: str
    OLLAMA_KEEP_ALIVE: str
    GEMINI_API_KEY: str
    GEMINI_MODEL: str
    OPENAI_API_KEY: str
    OPENAI_MODEL: str
    OPENAI_BASE_URL: str
    ANTHROPIC_API_KEY: str
    ANTHROPIC_MODEL: str
    CUSTOM_API_KEY: str
    CUSTOM_BASE_URL: str
    CUSTOM_MODEL: str
    LLM_MAX_OUTPUT_TOKENS: int
    OLLAMA_VISION_MODEL: str
    SCREEN_VISION_MAX_DIMENSION: int


class AudioSettings(_SettingsView):
    ASSISTANT_NAME: str
    STT_PROVIDER: str
    STT_REALTIME_MODEL: str
    STT_LANGUAGE: str
    STT_FINAL_TIMEOUT_SECONDS: float
    WHISPER_MODEL_SIZE: str
    WHISPER_DEVICE: str
    WHISPER_COMPUTE_TYPE: str
    WHISPER_CPU_THREADS: int
    WHISPER_NUM_WORKERS: int
    WHISPER_LOG_PROB_THRESHOLD: float
    WHISPER_INITIAL_PROMPT: str
    VOCABULARY_CORRECTIONS: list
    MICROPHONE_DEVICE: str
    AUDIO_OUTPUT_DEVICE: str
    SAMPLE_RATE: int
    SILENCE_SECONDS: float
    MAX_RECORD_SECONDS: int
    VAD_AGGRESSIVENESS: int
    MIN_SPEECH_MS: int
    ADAPTIVE_SILENCE_ENABLED: bool
    ADAPTIVE_SILENCE_MIN_SECONDS: float
    ADAPTIVE_SILENCE_MAX_SECONDS: float
    ADAPTIVE_SILENCE_FAST_WPS: float
    ADAPTIVE_SILENCE_SLOW_WPS: float
    ADAPTIVE_SILENCE_EMA_ALPHA: float
    SPEAK_RESPONSES: bool
    TTS_PROVIDER: str
    EDGE_TTS_VOICE: str
    EDGE_TTS_RATE: str
    EDGE_TTS_VOLUME: str
    EDGE_TTS_PITCH: str
    ELEVENLABS_API_KEY: str
    ELEVENLABS_VOICE_ID: str
    ELEVENLABS_MODEL: str
    TTS_PIPELINE_SENTENCES: bool
    TTS_STREAMING_ENABLED: bool
    TTS_AUDIO_BUFFER_MS: int
    TTS_CLAUSE_MIN_CHARACTERS: int
    ALLOW_INTERRUPTIONS: bool
    BARGE_IN_REQUIRE_NAME: bool
    BARGE_IN_MIN_SPEECH_MS: int
    BARGE_IN_VAD_AGGRESSIVENESS: int
    DEBUG_PRINT_TRANSCRIPTS: bool


class MemorySettings(_SettingsView):
    CONVERSATION_MEMORY_TURNS: int
    CONVERSATION_MEMORY_CHARACTERS: int
    MAX_SAVED_MEMORIES: int
    MAX_MEMORY_FACT_CHARACTERS: int
    MAX_MEMORIES_IN_PROMPT: int
    RECENT_ACTION_CONTEXT_LIMIT: int
    POWER_CONFIRMATION_TIMEOUT_SECONDS: int
    FOLLOWUP_GRACE_SECONDS: int
    CONVERSATION_TIMEOUT_SECONDS: int


class PluginSettings(_SettingsView):
    PLUGINS_ENABLED: bool
    ENABLE_BACKGROUND_WATCHER: bool
    SYSTEM_WATCH_ENABLED: bool
    SYSTEM_WATCH_INTERVAL_SECONDS: int
    WEATHER_DEFAULT_LOCATION: str
    WEATHER_UNITS: str
    AUTO_DETECT_LOCATION: bool
    LOCATION_CACHE_MINUTES: int
    REMINDER_UPCOMING_WINDOW_HOURS: int


class UISettings(_SettingsView):
    ASSISTANT_NAME: str
    CREATOR_NAME: str
    ENABLE_COMPANION_GUI: bool
    HIDE_CONSOLE_WINDOW: bool
    CAVEMAN_MODE: str | None
    LAUNCH_APPS_IN_BACKGROUND: bool
    APP_LAUNCH_SETTLE_SECONDS: float
    CONFIRM_BEFORE_ACTIONS: bool
    FAST_TOOL_RESPONSES: bool


PROVIDER_SETTINGS = ProviderSettings(*ProviderSettings.__annotations__)
AUDIO_SETTINGS = AudioSettings(*AudioSettings.__annotations__)
MEMORY_SETTINGS = MemorySettings(*MemorySettings.__annotations__)
PLUGIN_SETTINGS = PluginSettings(*PluginSettings.__annotations__)
UI_SETTINGS = UISettings(*UISettings.__annotations__)
DIALOGUE_SETTINGS = _SettingsView(
    *ProviderSettings.__annotations__,
    *MemorySettings.__annotations__,
    *UISettings.__annotations__,
)
