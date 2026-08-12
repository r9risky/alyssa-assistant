"""
Voice ID stub — feature removed (ponytail audit #5).
All public functions preserved as no-ops so callers don't break.
"""
# ponytail: hand-rolled voiceprint was behind VOICE_ID_ENABLED=False and unused.
# Re-add from git history if speaker verification becomes a real requirement.


def extract_descriptor(audio, sample_rate=None):
    return None


def is_enrolled():
    return False


def enroll_from_samples(samples):
    return "Voice ID has been removed — it can be re-added if needed."


def verify(audio):
    return True, ""
