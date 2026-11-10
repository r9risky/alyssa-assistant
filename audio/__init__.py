"""Unified audio and speech processing subsystem for Alyssa Assistant.

This package unifies recording, VAD, transcription, speech synthesis,
audio playback, DSP effects, wake-word detection, and call audio isolation.
"""

from .recorder import (
    record_command,
    listen_for_barge_in,
    update_speaking_rate,
    VoiceActivityDetector,
    was_wake_word_triggered,
)
from .transcribe import (
    transcribe,
    preload,
    register_partial_transcript_hook,
    start_streaming,
)
from .playback import (
    speak,
    StreamingSpeaker,
    warm_up,
    reinit_mixer,
)
from .synthesis import (
    list_edge_voices,
    list_elevenlabs_voices,
    websocket_connect,
)
from .dsp import (
    apply_pitch_shift,
    apply_robot_effect,
    apply_glitch_effect,
    process_pcm16_mono,
)
from .wake_word import (
    WakeWordDetector,
    get_wake_word_detector,
)
from .call_ears import (
    CallSession,
    get_call_session,
    sanitize_call_transcript,
)

__all__ = [
    "record_command",
    "listen_for_barge_in",
    "update_speaking_rate",
    "VoiceActivityDetector",
    "was_wake_word_triggered",
    "transcribe",
    "preload",
    "register_partial_transcript_hook",
    "start_streaming",
    "speak",
    "StreamingSpeaker",
    "warm_up",
    "reinit_mixer",
    "list_edge_voices",
    "list_elevenlabs_voices",
    "websocket_connect",
    "apply_pitch_shift",
    "apply_robot_effect",
    "apply_glitch_effect",
    "process_pcm16_mono",
    "WakeWordDetector",
    "get_wake_word_detector",
    "CallSession",
    "get_call_session",
    "sanitize_call_transcript",
]
