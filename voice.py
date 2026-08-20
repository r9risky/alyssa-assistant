"""Backward-compatible facade for voice synthesis and playback."""

import sys
import types

import voice_playback as _playback
import voice_synthesis as _synthesis
from voice_playback import (
    StreamingSpeaker, warm_up, reinit_mixer,
    _configured_output_device, _configured_output_device_name,
    _ensure_mixer, _init_mixer_locked, _smooth_speech_text,
    _split_sentences, _volume_str_to_gain, _play, _speak_one,
    _speak_pipelined, speak,
)
from voice_synthesis import (
    websocket_connect, _synthesize, _synthesize_edge, _synthesize_elevenlabs,
    _fetch_edge_voices_async, list_edge_voices, list_elevenlabs_voices,
    _get_loop, _run_on_loop, _ElevenRealtimeTTS, _eleven_realtime_tts,
    _synthesize_to_temp_file,
)

__all__ = [
    "asyncio", "base64", "json", "os", "queue", "re", "tempfile",
    "threading", "time", "urlencode", "edge_tts", "requests", "sd",
    "websocket_connect", "config", "telemetry", "pygame", "reinit_mixer",
    "list_edge_voices", "list_elevenlabs_voices", "StreamingSpeaker",
    "warm_up", "speak",
]

_MODULES = (_playback, _synthesis)


def __getattr__(name):
    for module in _MODULES:
        try:
            return getattr(module, name)
        except AttributeError:
            pass
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class _VoiceModule(types.ModuleType):
    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        for module in _MODULES:
            if name in vars(module):
                setattr(module, name, value)


sys.modules[__name__].__class__ = _VoiceModule
