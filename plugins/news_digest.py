"""
News digest via web_search.py (Brave/DDG, no extra key).

- get_news_digest(topics): on-demand
- check_watch(): once per day at DIGEST_HOUR (local 24h) on DIGEST_TOPICS
- Empty DIGEST_TOPICS disables the proactive briefing only
"""
import datetime

# plugins/ is on sys.path (plugin_loader); sibling import of web_search is OK.
import web_search

# --- Daily briefing: DIGEST_TOPICS empty = on-demand only ---------------------
DIGEST_TOPICS = []  # e.g. ["AI research", "world news", "your favorite team"]
DIGEST_HOUR = 8      # 24h local time the daily briefing fires (once per day)

WATCH_INTERVAL_SECONDS = 900  # only needs to notice DIGEST_HOUR a few times/hour
UNTRUSTED_OUTPUTS = {"get_news_digest"}

_last_digest_date = None


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
    topic_list = [t.strip() for t in topics.split(",") if t.strip()] or list(DIGEST_TOPICS)
    if not topic_list:
        return "I don't have any digest topics configured - ask me about a specific topic, or set DIGEST_TOPICS in plugins/news_digest.py."
    summaries = [_summarize(t) for t in topic_list]
    return "Here's your digest. " + " ".join(summaries)


def check_watch():
    global _last_digest_date
    if not DIGEST_TOPICS:
        return None
    now = datetime.datetime.now()
    today = now.date()
    if _last_digest_date == today:
        return None
    if now.hour < DIGEST_HOUR:
        return None
    _last_digest_date = today
    return "Here's your morning briefing. " + get_news_digest()


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
