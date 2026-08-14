import re

def _strip_fake_tool_call(text: str) -> str:
    """Removes any stray JSON-looking tool-call text the model wrote out as
    plain text instead of actually calling the tool. Small local models
    occasionally do this, and it should never reach speech.

    Uses brace-counting rather than a simple regex so it correctly handles
    nested JSON, e.g. {"name": "x", "parameters": {"command": "y"}}.
    """
    result = []
    i = 0
    while i < len(text):
        if text[i] == "{":
            # Try to find the matching closing brace for this block
            depth = 0
            j = i
            while j < len(text):
                if text[j] == "{":
                    depth += 1
                elif text[j] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1

            candidate = text[i : j + 1]
            if depth == 0 and '"name"' in candidate:
                # This block looks like a fake tool call - skip over it entirely
                i = j + 1
                continue

        result.append(text[i])
        i += 1

    return "".join(result).strip()


def _is_degenerate_reply(text: str) -> bool:
    """True if text is empty, or contains nothing but brackets/braces/quotes/
    punctuation/whitespace - i.e. the model tried to write a tool call (or an
    empty one, like '[]') as plain text instead of actually calling a tool,
    and there's no real sentence left to say out loud."""
    stripped = text.strip()
    if not stripped:
        return True
    return re.fullmatch(r"[\[\]\{\}\(\)\"'`,:;.\s]*", stripped) is not None


_LAZY_ACK_RE = re.compile(
    r"^(?:sure|okay|ok|certainly|of course|alright|done|will do|"
    r"right away|on it|got it|no problem|no worries|absolutely|"
    r"you got it|consider it done|happy to)[.!,]*$",
    re.IGNORECASE,
)


def _looks_like_lazy_dodge(text: str) -> bool:
    """True if `text` is nothing but a stock acknowledgment word/phrase -
    see _LAZY_ACK_RE above."""
    return bool(_LAZY_ACK_RE.match((text or "").strip()))
