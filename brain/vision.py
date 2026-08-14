import io
import re

import requests

import config

from .providers.anthropic import _describe_image_anthropic
from .providers.gemini import _describe_image_gemini
from .providers.ollama import _describe_image_ollama
from .providers.openai import _describe_image_openai_compatible

_SCREEN_VISION_BASE_PROMPT = (
    "Screenshot of the user's screen, just taken. In 1-2 spoken sentences, "
    "say what's on it - the app/site in focus and what's happening. Skip "
    "UI chrome (menus, scrollbars) unless asked. If you recognize a "
    "specific character, show, game, person, or brand, only name it if "
    "you're genuinely confident - a small/cropped/low-res image of a "
    "niche source is easy to misidentify. Otherwise describe it "
    "generically (e.g. 'an anime character with dark hair drinking from "
    "a mug') instead of guessing a specific name; a vague-but-correct "
    "description is more useful than a confident wrong one."
)


def describe_screen_with_vision(question: str = "") -> str:
    """Takes a screenshot (in memory only - nothing saved to disk) and asks
    the configured LLM provider's vision-capable model to describe it, or
    answer a specific question about it. Returns the plain-text answer, or
    a plain-language error string starting with "Couldn't"/"I can't" if
    something went wrong - handle_command()'s caller-facing tool-result
    convention, same as every function in actions.py."""
    try:
        from PIL import ImageGrab
    except ImportError:
        return "Couldn't look at the screen: Pillow isn't installed (pip install Pillow)."

    try:
        image = ImageGrab.grab()
    except Exception as e:
        return f"Couldn't capture the screen: {e}"

    max_dim = getattr(config, "SCREEN_VISION_MAX_DIMENSION", 1568)
    if image.width > max_dim or image.height > max_dim:
        image.thumbnail((max_dim, max_dim))

    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=85)
    image_bytes = buffer.getvalue()

    prompt = _SCREEN_VISION_BASE_PROMPT
    question = (question or "").strip()
    if question:
        prompt += f' They asked: "{question}" - answer that directly.'

    provider = config.LLM_PROVIDER
    if provider == "gemini":
        return _describe_image_gemini(image_bytes, "image/jpeg", prompt)
    if provider == "openai":
        return _describe_image_openai_compatible(
            image_bytes, "image/jpeg", prompt,
            getattr(config, "OPENAI_BASE_URL", "https://api.openai.com/v1"),
            config.OPENAI_API_KEY, config.OPENAI_MODEL, "OpenAI",
        )
    if provider == "anthropic":
        return _describe_image_anthropic(image_bytes, "image/jpeg", prompt)
    if provider == "custom_openai":
        return _describe_image_openai_compatible(
            image_bytes, "image/jpeg", prompt,
            getattr(config, "CUSTOM_BASE_URL", ""),
            getattr(config, "CUSTOM_API_KEY", ""),
            getattr(config, "CUSTOM_MODEL", ""), "Your custom provider",
        )
    return _describe_image_ollama(image_bytes, prompt)


_LOCATE_ELEMENT_PROMPT_TEMPLATE = (
    "Screenshot of the user's screen, just taken. Find this UI element: "
    '"{description}". Respond with ONLY two numbers separated by a comma: '
    "the element's center point as a percentage of image width and height, "
    "0-100 each, e.g. \"42,87\" for a point 42% across and 87% down. No "
    "words, no explanation, no percent signs, no extra formatting - just "
    "the two numbers. If you can't find it, respond with exactly: NOT_FOUND"
)


def locate_screen_element_with_vision(description: str) -> tuple[float, float] | None:
    """Takes a screenshot and asks the vision model to point at a
    described UI element as (x_percent, y_percent) of the screen, 0-100
    each. Returns None if the model reported it couldn't find the element
    or its reply didn't parse as coordinates. Used by
    actions.click_screen_element() to let Alyssa act on what she sees,
    not just narrate it - the vision-to-click pipeline is inherently
    approximate (a language model estimating pixel coordinates from a
    downscaled screenshot), so this is best used for reasonably large,
    distinct on-screen targets (a labeled button, an icon, a visible
    text field) rather than tiny or ambiguous ones."""
    try:
        from PIL import ImageGrab
    except ImportError:
        return None

    try:
        image = ImageGrab.grab()
    except Exception:
        return None

    max_dim = getattr(config, "SCREEN_VISION_MAX_DIMENSION", 1568)
    if image.width > max_dim or image.height > max_dim:
        image.thumbnail((max_dim, max_dim))

    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=85)
    image_bytes = buffer.getvalue()

    prompt = _LOCATE_ELEMENT_PROMPT_TEMPLATE.format(description=description.strip())

    provider = config.LLM_PROVIDER
    if provider == "gemini":
        raw = _describe_image_gemini(image_bytes, "image/jpeg", prompt)
    elif provider == "openai":
        raw = _describe_image_openai_compatible(
            image_bytes, "image/jpeg", prompt,
            getattr(config, "OPENAI_BASE_URL", "https://api.openai.com/v1"),
            config.OPENAI_API_KEY, config.OPENAI_MODEL, "OpenAI",
        )
    elif provider == "anthropic":
        raw = _describe_image_anthropic(image_bytes, "image/jpeg", prompt)
    elif provider == "custom_openai":
        raw = _describe_image_openai_compatible(
            image_bytes, "image/jpeg", prompt,
            getattr(config, "CUSTOM_BASE_URL", ""),
            getattr(config, "CUSTOM_API_KEY", ""),
            getattr(config, "CUSTOM_MODEL", ""), "Your custom provider",
        )
    else:
        raw = _describe_image_ollama(image_bytes, prompt)

    match = re.search(r"(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)", raw or "")
    if not match:
        return None
    x_pct, y_pct = float(match.group(1)), float(match.group(2))
    if not (0 <= x_pct <= 100 and 0 <= y_pct <= 100):
        return None
    return x_pct, y_pct
