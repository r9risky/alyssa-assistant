import re

def _strip_fake_tool_call(text: str) -> str:
    """Strip fake JSON tool-call text so it never reaches speech.

    Brace-counts nested JSON; a regex would truncate inner objects.
    """
    result = []
    i = 0
    while i < len(text):
        if text[i] == "{":
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
                i = j + 1
                continue

        result.append(text[i])
        i += 1

    return "".join(result).strip()


def _is_degenerate_reply(text: str) -> bool:
    """True if empty or only brackets/braces/quotes/punctuation/whitespace."""
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
