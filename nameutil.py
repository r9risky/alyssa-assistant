"""Assistant name detection and matching utilities."""

import functools
import re

import config


# Other people's names that older configs listed as aliases. Saying "Lisa" or
# "Melissa" in normal conversation made her answer, so they never count as her
# name. Removed in code because the updater keeps a user's old alias list.
_NOT_HER_NAME = {"lisa", "melissa", "larissa"}

# Words that can come before her name when you address her ("hey Alyssa").
_MAX_WORDS_BEFORE_NAME = 2


def name_variants():
    """Returns deduplicated configured assistant name and aliases."""
    aliases = [
        a for a in getattr(config, "ASSISTANT_NAME_ALIASES", [])
        if str(a).strip().lower() not in _NOT_HER_NAME
    ]
    return list(dict.fromkeys([config.ASSISTANT_NAME] + aliases))


@functools.lru_cache(maxsize=16)
def _compile_name_pattern(variants: tuple):
    return re.compile(
        r"\b(" + "|".join(re.escape(v) for v in variants) + r")\b",
        flags=re.IGNORECASE,
    )


def name_pattern():
    """Compiled regex keyed by the current configured name variants."""
    return _compile_name_pattern(tuple(name_variants()))


def find_name_span(text: str):
    """Finds the assistant name in text. Returns (start, end) span or None."""
    if not text:
        return None
    match = name_pattern().search(text)
    if match:
        return match.start(), match.end()
    return None


def find_address_span(text: str):
    """Like find_name_span, but only when the name is used to address her: at the
    start ("hey Alyssa, ...", at most two words before it) or as the last word
    ("open Chrome, Alyssa"). "I told Alyssa about it" is talk about her, not to
    her, so it returns None."""
    for match in name_pattern().finditer(text or ""):
        before = re.findall(r"[\w']+", text[: match.start()])
        after = re.findall(r"[\w']+", text[match.end():])
        if len(before) <= _MAX_WORDS_BEFORE_NAME or not after:
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


def is_stop_request(text: str) -> bool:
    """Returns True when text contains both the assistant's name and "stop"."""
    return contains_name(text) and bool(re.search(r"\bstop\b", text or "", re.IGNORECASE))
