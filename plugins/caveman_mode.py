"""
Sets config.CAVEMAN_MODE so brain.py's system prompt makes the LLM generate
terse replies. Not a text filter — facts, code, and tools stay unchanged.
"""
import config

_LEVELS = ("lite", "full", "ultra")


def set_caveman_mode(level: str = "full") -> str:
    """Turns caveman mode on at the given level, or off with level='off'."""
    level = (level or "full").strip().lower()
    if level in ("off", "normal", "none", "stop"):
        config.CAVEMAN_MODE = None
        return "Caveman mode off."
    if level not in _LEVELS:
        level = "full"
    config.CAVEMAN_MODE = level
    return f"Caveman mode on ({level})."


def caveman_status() -> str:
    """Reports whether caveman mode is currently on, and at what level."""
    level = getattr(config, "CAVEMAN_MODE", None)
    return f"Caveman mode: {level or 'off'}."


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "set_caveman_mode",
            "description": (
                "Turns Alyssa's caveman speaking mode on/off or changes its "
                "level. Use when the user says 'talk like a caveman', "
                "'caveman mode', 'be more terse', or 'normal mode'/'stop "
                "caveman' to turn it off."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "level": {
                        "type": "string",
                        "enum": ["lite", "full", "ultra", "off"],
                        "description": "Compression level, or 'off' to disable (default: full).",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "caveman_status",
            "description": "Reports whether caveman mode is on and at what level.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

FUNCTIONS = {
    "set_caveman_mode": set_caveman_mode,
    "caveman_status": caveman_status,
}
