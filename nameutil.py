"""
Assistant name detection and matching utilities.
"""

import functools
import re

import config


@functools.cache
def name_variants():
    """Returns deduplicated configured assistant name and aliases."""
    names = [config.ASSISTANT_NAME] + list(getattr(config, "ASSISTANT_NAME_ALIASES", []))
    return list(dict.fromkeys(names))


@functools.cache
def name_pattern():
    """Compiled regex for matching assistant name variants."""
    variants = name_variants()
    return re.compile(
        r"\b(" + "|".join(re.escape(v) for v in variants) + r")\b",
        flags=re.IGNORECASE,
    )


def find_name_span(text: str):
    """Finds the assistant name in text. Returns (start, end) span or None."""
    if not text:
        return None
    match = name_pattern().search(text)
    if match:
        return match.start(), match.end()
    return None


def strip_name_at_span(text: str, span) -> str:
    """Strips the matched name from text, returning the remaining command string."""
    start, end = span
    after = text[end:].strip(" ,.:;-")
    if after:
        return after
    before = text[:start].strip(" ,.:;-")
    if before:
        return before
    return ""


def contains_name(text: str) -> bool:
    """Returns True if the assistant name or an alias is detected in text."""
    return find_name_span(text or "") is not None
