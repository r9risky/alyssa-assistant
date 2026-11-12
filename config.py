"""Default settings for Alyssa Assistant.

Anything changed in Settings or the setup wizard is saved to settings.json and
overrides the value here (see the bottom of this file). If an edit here seems to
do nothing, that setting is probably in settings.json: change it there or in Settings.
"""
from typing import Any

import credential_store as _cred

ASSISTANT_NAME = 'Alyssa'
CREATOR_NAME = "Riya"
ASSISTANT_NAME_ALIASES = [
    "alyssa", "alissa", "alisa", "alysa", "aleesa", "aleessa",
    "alicia", "elisa", "elissa",
    "eliza", "elysia", "elyssa", "allissa", "allisa", "alissia",
    "alisha", "alysha", "ahlyssa", "uhlyssa",
]


LLM_PROVIDER = 'gemini'
LLM_STRONG_PROVIDER = 'anthropic'
LLM_REASONING_TIER = 'auto'  # "auto", "fast", or "strong"

OLLAMA_MODEL = 'qwen2.5:3b'
OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_KEEP_ALIVE = "10m"

GEMINI_API_KEY = _cred.get_secret('GEMINI_API_KEY')  # env always wins
GEMINI_MODEL = 'gemini-3.5-flash-lite'

OPENAI_API_KEY = _cred.get_secret('OPENAI_API_KEY')  # env always wins
OPENAI_MODEL = 'gpt-5-mini'
OPENAI_BASE_URL = "https://api.openai.com/v1"

SPOTIFY_CLIENT_ID = _cred.get_secret('SPOTIFY_CLIENT_ID')  # env always wins
SPOTIFY_CLIENT_SECRET = _cred.get_secret('SPOTIFY_CLIENT_SECRET')  # env always wins
YOUTUBE_API_KEY = _cred.get_secret('YOUTUBE_API_KEY')  # env always wins

ANTHROPIC_API_KEY = _cred.get_secret('ANTHROPIC_API_KEY')  # env always wins
ANTHROPIC_MODEL = 'claude-sonnet-5'

CUSTOM_API_KEY = _cred.get_secret('CUSTOM_API_KEY')  # env var is CUSTOM_LLM_API_KEY; always wins
CUSTOM_BASE_URL = 'https://openrouter.ai/api/v1'
CUSTOM_MODEL = 'meta-llama/llama-3.3-70b-instruct'
LLM_MAX_OUTPUT_TOKENS = 256



OLLAMA_VISION_MODEL = "llava"
SCREEN_VISION_MAX_DIMENSION = 768

# "auto": Groq Whisper LPU cloud when key is set (ultra-fast ~90ms), else local Faster Whisper.
STT_PROVIDER = "auto"  # "auto", "groq", "elevenlabs_realtime", or "local"
GROQ_API_KEY = _cred.get_secret('GROQ_API_KEY')
STT_GROQ_MODEL = "whisper-large-v3-turbo"
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
    ("Alyssa", ["eliza", "elyssa", "elysia", "allissa", "allisa", "alissia", "alisha", "alysha", "ahlyssa", "uhlyssa"]),
    ("Arknights Endfield", ["arknights and field", "arc knights endfield", "arc nights and field"]),
]

MICROPHONE_DEVICE = 'default'
AUDIO_OUTPUT_DEVICE = 'default'  # "default" or a (partial) speaker/headset name

# Capri-inspired Call & Meeting Mode settings
CALL_MODE_ENABLED = False  # True when actively in a call/meeting session
CALL_APP = 'Auto'  # "Auto", "Discord", "Zoom", "Teams", "WhatsApp", "Steam", "Browser"
CALL_VIRTUAL_MIC_DEVICE = 'CABLE Input'  # Output device sending Alyssa's voice into Discord/Zoom
CALL_VIRTUAL_MIC_VOLUME = 1.0  # 0.0 to 1.0 loudness of Alyssa in the call
CALL_FRIENDS_CAN_ASK = True  # Friends can request games/songs, requiring your confirmation
CALL_BARGE_IN_TOLERANCE_SECONDS = 1.0  # Yields only if someone speaks over her for >1.0s
CALL_AUTO_JOIN_ENABLED = True  # Automatically click Join Voice / Join Meeting
# Discord bot: she joins voice channels as her own bot account (token lives in the OS keyring)
DISCORD_BOT_ENABLED = False
DISCORD_OWNER_ID = ''  # your Discord user ID, so she can find the voice channel you're in
DISCORD_LISTEN = True  # hear friends (transcribed on this PC; AI only when they say her name)

SAMPLE_RATE = 16000
SILENCE_SECONDS = 0.95
MAX_RECORD_SECONDS = 15
VAD_AGGRESSIVENESS = 2
MIN_SPEECH_MS = 120
VAD_ENGINE = 'auto'  # "auto", "silero", or "webrtc"
SILERO_VAD_THRESHOLD = 0.50  # 0.0 to 1.0 speech probability
PRE_ROLL_BUFFER_MS = 400  # ms of rolling audio prepended to speech to prevent clipped starts
WAKE_WORD_MODE = 'always_listening'  # "always_listening", "wake_word", or "push_to_talk"
WAKE_WORD_NAME = 'Alyssa'
WAKE_WORD_THRESHOLD = 0.50  # 0.0 to 1.0 confidence
WAKE_CHIME_ENABLED = True  # Play pleasant confirmation chime on wake-up
WAKE_CHIME_VOLUME = 0.5  # 0.0 to 1.0
WAKE_WORD_MODEL_PATH = ''  # Optional path to custom ONNX wake word model

ADAPTIVE_SILENCE_ENABLED = True
ADAPTIVE_SILENCE_MIN_SECONDS = 0.75
ADAPTIVE_SILENCE_MAX_SECONDS = 1.50
ADAPTIVE_SILENCE_FAST_WPS = 3.3
ADAPTIVE_SILENCE_SLOW_WPS = 1.3
ADAPTIVE_SILENCE_EMA_ALPHA = 0.3

FAST_TOOL_RESPONSES = True
SPECULATIVE_EXECUTION_ENABLED = True
SPECULATIVE_TIMEOUT_SECONDS = 3.0

SPEAK_RESPONSES = True
TTS_PROVIDER = 'edge'  # "edge", "elevenlabs" or "kokoro" (free, offline after a one-time ~120 MB download)

EDGE_TTS_VOICE = 'ja-JP-NanamiNeural'
KOKORO_VOICE = 'af_heart'  # Kokoro voice id, e.g. af_heart, af_bella, af_sky, bf_emma, jf_alpha
EDGE_TTS_RATE = "+0%"
EDGE_TTS_VOLUME = '+0%'
EDGE_TTS_PITCH = "+0Hz"

ELEVENLABS_API_KEY = _cred.get_secret('ELEVENLABS_API_KEY')  # no env override; use Settings
ELEVENLABS_VOICE_ID = ''
ELEVENLABS_MODEL = 'eleven_flash_v2_5'


TTS_PIPELINE_SENTENCES = True
TTS_STREAMING_ENABLED = True
TTS_AUDIO_BUFFER_MS = 100
TTS_CLAUSE_MIN_CHARACTERS = 28

ALLOW_INTERRUPTIONS = True
BARGE_IN_REQUIRE_NAME = True
BARGE_IN_MIN_SPEECH_MS = 150
BARGE_IN_VAD_AGGRESSIVENESS = 3

CONVERSATION_MEMORY_TURNS = 11
CONVERSATION_MEMORY_CHARACTERS = 4000
MAX_SAVED_MEMORIES = 75
MAX_MEMORY_FACT_CHARACTERS = 400
MAX_MEMORIES_IN_PROMPT = 8
RECENT_ACTION_CONTEXT_LIMIT = 6
POWER_CONFIRMATION_TIMEOUT_SECONDS = 60
FOLLOWUP_GRACE_SECONDS = 20
CONVERSATION_TIMEOUT_SECONDS = 300

PLUGINS_ENABLED = True
ENABLE_BACKGROUND_WATCHER = True
SYSTEM_WATCH_ENABLED = False
SYSTEM_WATCH_INTERVAL_SECONDS = 300
BRIEFING_ENABLED = False  # spoken daily briefing (weather, calendar, reminders, news)
BRIEFING_TIME = '08:00'  # 24h local time
BRIEFING_NEWS_TOPICS = ''  # comma-separated, e.g. 'world news, AI'
WEATHER_DEFAULT_LOCATION = ''
WEATHER_UNITS = 'metric'
AUTO_DETECT_LOCATION = True
LOCATION_CACHE_MINUTES = 60
REMINDER_UPCOMING_WINDOW_HOURS = 24

ENABLE_COMPANION_GUI = True
HIDE_CONSOLE_WINDOW = False  # True: hide the Windows console at startup

# None = off. "lite" / "full" / "ultra" — terser LLM replies only; does not change what she knows or does.
CAVEMAN_MODE = None

LAUNCH_APPS_IN_BACKGROUND = True
APP_LAUNCH_SETTLE_SECONDS = 1.5
DEBUG_PRINT_TRANSCRIPTS = True
CONFIRM_BEFORE_ACTIONS = False

# Security presets: "ask_always", "careful", "balanced", "autopilot"
SECURITY_PRESET = 'balanced'
SECURITY_PERMISSIONS = {
    "open_apps_urls": True,
    "window_management": True,
    "media_volume": True,
    "clipboard_screen": True,
    "typing_keys": True,
    "screen_clicking": False,
    "file_deletion": False,
    "shell_commands": False,
    "power_actions": False,
    "plugins": True,
}

# Capri-inspired Richer Memory, Transcripts & Custom Personality
USER_NICKNAME = ''
REMEMBER_CHATS_ENABLED = True
PERSONALITY_FILE = 'personality.md'
ME_NOTES_FILE = 'me.md'
MEMORIES_NOTES_FILE = 'memories.md'
CHECK_INS_FILE = 'check-ins.md'
TRANSCRIPTS_DIR = 'transcripts'
SCREEN_MEMORY_RETENTION_HOURS = 3.0

# Capri-inspired Proactive Screen Watching, Visual Lookout & Quiet Mode
QUIET_MODE = False
PHONE_REMOTE_ENABLED = False  # chat with her from your phone on the same Wi-Fi
PHONE_REMOTE_PORT = 47900
SCREEN_WATCH_ENABLED = False
SCREEN_WATCH_INTERVAL_SECONDS = 120
SCREEN_WATCH_KEEP_CAUGHT_UP = True
CHECK_IN_ENABLED = False
CHECK_IN_INTERVAL_MINUTES = 45

# Capri-inspired Voice DSP Effects & Typing Sound
VOICE_DSP_ENABLED = False
VOICE_PITCH_SEMITONES = 0.0
VOICE_SPEED_FACTOR = 1.0
VOICE_ROBOT_AMOUNT = 0
VOICE_GLITCH_AMOUNT = 0
VOICE_LAYERS_ENABLED = False
VOICE_LAYER_1_PITCH = -3.0
VOICE_LAYER_2_PITCH = 4.0
TYPING_SOUND_ENABLED = False

# Capri-inspired Global Hotkeys
HOTKEYS_ENABLED = True
HOTKEY_TALK = 'ctrl+alt+v'
HOTKEY_MUTE = 'ctrl+alt+n'
HOTKEY_TYPE = 'ctrl+alt+t'
HOTKEY_HIDE = 'ctrl+alt+h'
HOTKEY_QUIET = 'ctrl+alt+p'
HOTKEY_HOP_OFF = 'ctrl+alt+c'

# Coding buddy mode (live status for coding agents)
HUSK_MODE_ENABLED = False
HUSK_PORT = 47831
HUSK_WATCH_CLAUDE_CODE = True

# Capri-inspired Modular Extensions & Toggles
EXTENSIONS_ENABLED = True
DESKTOP_CHAOS_ENABLED = True  # Set False for a prank-free desktop (disables fake BSOD & clones)
WATCH_ALONG_ENABLED = True   # Screen stream reaction mode
REPLAY_BUFFER_ENABLED = True # 30s rolling replay buffer ("clip that!")
GAME_COPILOT_ENABLED = True  # Visual game detection & controls assistant
MINI_GAMES_ENABLED = True    # Desktop ink overlay & mini-games


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
    USER_NICKNAME: str
    STT_PROVIDER: str
    GROQ_API_KEY: str
    STT_GROQ_MODEL: str
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
    VAD_ENGINE: str
    SILERO_VAD_THRESHOLD: float
    PRE_ROLL_BUFFER_MS: int
    WAKE_WORD_MODE: str
    WAKE_WORD_NAME: str
    WAKE_WORD_THRESHOLD: float
    WAKE_CHIME_ENABLED: bool
    WAKE_CHIME_VOLUME: float
    WAKE_WORD_MODEL_PATH: str
    ADAPTIVE_SILENCE_ENABLED: bool
    ADAPTIVE_SILENCE_MIN_SECONDS: float
    ADAPTIVE_SILENCE_MAX_SECONDS: float
    ADAPTIVE_SILENCE_FAST_WPS: float
    ADAPTIVE_SILENCE_SLOW_WPS: float
    ADAPTIVE_SILENCE_EMA_ALPHA: float
    SPEAK_RESPONSES: bool
    TTS_PROVIDER: str
    EDGE_TTS_VOICE: str
    KOKORO_VOICE: str
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
    VOICE_DSP_ENABLED: bool
    VOICE_PITCH_SEMITONES: float
    VOICE_SPEED_FACTOR: float
    VOICE_ROBOT_AMOUNT: int
    VOICE_GLITCH_AMOUNT: int
    VOICE_LAYERS_ENABLED: bool
    VOICE_LAYER_1_PITCH: float
    VOICE_LAYER_2_PITCH: float
    TYPING_SOUND_ENABLED: bool
    CALL_MODE_ENABLED: bool
    CALL_APP: str
    CALL_VIRTUAL_MIC_DEVICE: str
    CALL_VIRTUAL_MIC_VOLUME: float
    CALL_FRIENDS_CAN_ASK: bool
    CALL_BARGE_IN_TOLERANCE_SECONDS: float
    CALL_AUTO_JOIN_ENABLED: bool
    DISCORD_BOT_ENABLED: bool
    DISCORD_OWNER_ID: str
    DISCORD_LISTEN: bool


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
    USER_NICKNAME: str
    REMEMBER_CHATS_ENABLED: bool
    PERSONALITY_FILE: str
    ME_NOTES_FILE: str
    MEMORIES_NOTES_FILE: str
    CHECK_INS_FILE: str
    TRANSCRIPTS_DIR: str
    SCREEN_MEMORY_RETENTION_HOURS: float


class PluginSettings(_SettingsView):
    PLUGINS_ENABLED: bool
    ENABLE_BACKGROUND_WATCHER: bool
    SYSTEM_WATCH_ENABLED: bool
    SYSTEM_WATCH_INTERVAL_SECONDS: int
    BRIEFING_ENABLED: bool
    BRIEFING_TIME: str
    BRIEFING_NEWS_TOPICS: str
    SCREEN_WATCH_ENABLED: bool
    SCREEN_WATCH_INTERVAL_SECONDS: int
    SCREEN_WATCH_KEEP_CAUGHT_UP: bool
    CHECK_IN_ENABLED: bool
    CHECK_IN_INTERVAL_MINUTES: int
    HUSK_MODE_ENABLED: bool
    HUSK_PORT: int
    HUSK_WATCH_CLAUDE_CODE: bool
    WEATHER_DEFAULT_LOCATION: str
    WEATHER_UNITS: str
    AUTO_DETECT_LOCATION: bool
    LOCATION_CACHE_MINUTES: int
    REMINDER_UPCOMING_WINDOW_HOURS: int
    EXTENSIONS_ENABLED: bool
    DESKTOP_CHAOS_ENABLED: bool
    WATCH_ALONG_ENABLED: bool
    REPLAY_BUFFER_ENABLED: bool
    GAME_COPILOT_ENABLED: bool
    MINI_GAMES_ENABLED: bool


class UISettings(_SettingsView):
    ASSISTANT_NAME: str
    CREATOR_NAME: str
    USER_NICKNAME: str
    ENABLE_COMPANION_GUI: bool
    HIDE_CONSOLE_WINDOW: bool
    CAVEMAN_MODE: str | None
    LAUNCH_APPS_IN_BACKGROUND: bool
    APP_LAUNCH_SETTLE_SECONDS: float
    CONFIRM_BEFORE_ACTIONS: bool
    SECURITY_PRESET: str
    SECURITY_PERMISSIONS: dict
    QUIET_MODE: bool
    PHONE_REMOTE_ENABLED: bool
    PHONE_REMOTE_PORT: int
    HOTKEYS_ENABLED: bool
    HOTKEY_TALK: str
    HOTKEY_MUTE: str
    HOTKEY_TYPE: str
    HOTKEY_HIDE: str
    HOTKEY_QUIET: str
    HOTKEY_HOP_OFF: str
    FAST_TOOL_RESPONSES: bool
    SPECULATIVE_EXECUTION_ENABLED: bool
    SPECULATIVE_TIMEOUT_SECONDS: float


PROVIDER_SETTINGS = ProviderSettings(*ProviderSettings.__annotations__)
AUDIO_SETTINGS = AudioSettings(*AudioSettings.__annotations__)
MEMORY_SETTINGS = MemorySettings(*MemorySettings.__annotations__)
PLUGIN_SETTINGS = PluginSettings(*PluginSettings.__annotations__)
UI_SETTINGS = UISettings(*UISettings.__annotations__)
DIALOGUE_SETTINGS = _SettingsView(
    *ProviderSettings.__annotations__,
    *MemorySettings.__annotations__,
    *PluginSettings.__annotations__,
    *UISettings.__annotations__,
)



# What the user changed in Settings or the setup wizard lives in settings.json
# (see user_settings.py); this file only holds the defaults.
try:
    import user_settings as _user_settings
except ImportError:  # an older Alyssa.exe running this newer config.py
    _user_settings = None
if _user_settings is not None:
    _user_settings.apply(globals(), skip=_cred.SECRET_ENV_VARS)  # keys live in the keyring
