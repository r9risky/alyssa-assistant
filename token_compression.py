"""
Token compression stub — engine removed (ponytail audit #4).
All public functions preserved as pass-throughs so callers don't break.
"""
# ponytail: 301-line compression engine solving a problem that doesn't exist
# at desktop-assistant scale. Re-add from git history if context-window
# pressure becomes measurable.


def compress_text(text, allow_semantic=True, is_tool=False):
    return text


def compress_messages(messages):
    return messages


def get_stats():
    return {"chars_before": 0, "chars_after": 0, "messages_touched": 0}


def reset_stats():
    pass


def spoken_stats_summary():
    return "Token compression is not active."
