"""
News digest via web_search.py (Brave/DDG, no extra key).

- get_news_digest(topics): on-demand; blank uses Settings' briefing news topics
- The scheduled morning briefing lives in daily_briefing.py
"""
import config

# plugins/ is on sys.path (plugin_loader); sibling import of web_search is OK.
import web_search

UNTRUSTED_OUTPUTS = {"get_news_digest"}


def _summarize(topic: str, num_results: int = 3) -> str:
    raw = web_search.search_web(topic, num_results=num_results)
    if raw.startswith("I couldn't") or raw.startswith("I didn't") or raw.startswith("Something went wrong"):
        return f"{topic}: {raw}"
    # Drop search_web header + URLs; speak titles/snippets only.
    lines = raw.split("\n")[1:]
    cleaned = []
    for line in lines:
        line = line.split(" (http")[0].strip()
        if line:
            cleaned.append(line)
    return f"{topic}: " + " ".join(cleaned[:num_results])


def get_news_digest(topics: str = "") -> str:
    """topics: comma-separated, or blank to use DIGEST_TOPICS."""
    topics = topics or str(getattr(config, "BRIEFING_NEWS_TOPICS", "") or "")
    topic_list = [t.strip() for t in topics.split(",") if t.strip()]
    if not topic_list:
        return "I don't have any news topics set - ask me about a specific topic, or add some under Settings > Assistant > Daily briefing."
    summaries = [_summarize(t) for t in topic_list]
    return "Here's your digest. " + " ".join(summaries)


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_news_digest",
            "description": (
                "Gives a short spoken news/research digest on one or more "
                "topics - e.g. 'catch me up on the news', 'what's new in "
                "AI research', 'give me a digest on electric cars and "
                "world news'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "topics": {
                        "type": "string",
                        "description": "Comma-separated topics. Leave blank to use the user's configured default topics.",
                    },
                },
                "required": [],
            },
        },
    },
]

FUNCTIONS = {
    "get_news_digest": get_news_digest,
}
