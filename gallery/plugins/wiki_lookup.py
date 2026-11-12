"""Short Wikipedia summaries - 'who was Ada Lovelace?'."""
import urllib.parse

import requests

PERMISSIONS = ["network"]
UNTRUSTED_OUTPUTS = {"wiki_summary"}


def wiki_summary(topic: str) -> str:
    topic = (topic or "").strip()
    if not topic:
        return "What should I look up?"
    url = "https://en.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(topic.replace(" ", "_"))
    try:
        response = requests.get(url, headers={"User-Agent": "AlyssaAssistant/1.0"}, timeout=10)
        if response.status_code == 404:
            return f"Wikipedia doesn't have a page called '{topic}'."
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as e:
        return f"I couldn't reach Wikipedia just now - {e}"
    extract = (data.get("extract") or "").strip()
    if not extract:
        return f"Wikipedia's page on '{topic}' has no summary."
    page = (data.get("content_urls", {}).get("desktop", {}) or {}).get("page", "")
    try:
        import sources  # Alyssa's source links under the reply, when available
        sources.record(page, data.get("title", topic))
    except ImportError:
        pass
    return f"{extract} (Source: {page})" if page else extract


FUNCTIONS = {"wiki_summary": wiki_summary}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "wiki_summary",
            "description": "Gets a short Wikipedia summary of a person, place or thing - 'who was ...', 'tell me about ... from Wikipedia'.",
            "parameters": {
                "type": "object",
                "properties": {"topic": {"type": "string", "description": "The page title or topic."}},
                "required": ["topic"],
            },
        },
    },
]
