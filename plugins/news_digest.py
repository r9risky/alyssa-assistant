"""
News/research digest plugin - built on top of plugins/web_search.py (same
Brave/DuckDuckGo lookup, no extra API key needed beyond what web_search.py
already uses). Gives Alyssa:

  - get_news_digest(topics): on-demand summary, e.g. "Alyssa, catch me up
    on the news" / "what's new in AI research".
  - check_watch(): once a day, unprompted, she gives you a short briefing
    on the topics you configured below - the "morning briefing" pattern,
    without you having to ask first.

Configure DIGEST_TOPICS and DIGEST_HOUR below (24h, local time). Leave
DIGEST_TOPICS empty to disable the proactive daily briefing while keeping
the on-demand get_news_digest ability.
"""
import datetime

# plugin_loader.py adds plugins/ to sys.path, so this sibling import works
# the same way plugins/_shared_location.py does for plugins/weather.py.
import web_search

# --- Configure your daily briefing here -------------------------------------
DIGEST_TOPICS = []  # e.g. ["AI research", "world news", "your favorite team"]
DIGEST_HOUR = 8      # 24h local time the daily briefing fires (once per day)

WATCH_INTERVAL_SECONDS = 900  # only needs to check "has DIGEST_HOUR passed yet" a few times an hour

_last_digest_date = None


def _summarize(topic: str, num_results: int = 3) -> str:
    raw = web_search.search_web(topic, num_results=num_results)
    if raw.startswith("I couldn't") or raw.startswith("I didn't") or raw.startswith("Something went wrong"):
        return f"{topic}: {raw}"
    # search_web()'s raw output is "Web search results for 'x':\n1. title - snippet (url)\n...".
    # Drop the header line and URLs for a briefing - titles/snippets read
    # out loud, without a spoken URL.
    lines = raw.split("\n")[1:]
    cleaned = []
    for line in lines:
        line = line.split(" (http")[0].strip()
        if line:
            cleaned.append(line)
    return f"{topic}: " + " ".join(cleaned[:num_results])


def get_news_digest(topics: str = "") -> str:
    """topics: comma-separated list, or blank to use the configured
    DIGEST_TOPICS from this file."""
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
