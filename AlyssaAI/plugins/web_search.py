"""
Web search plugin - gives Alyssa a "search_web" ability, e.g. "Alyssa,
search the web for the release date of the new iPhone". Returns a short
list of results (title / snippet / URL) so the model can cite sources
instead of guessing from what it already knows.

Two providers, picked automatically:

  - Brave Search API (better results, structured JSON) - used if you set
    a BRAVE_SEARCH_API_KEY environment variable. Free tier available at
    https://brave.com/search/api/:
        setx BRAVE_SEARCH_API_KEY "your-key-here"      (Windows, permanent)
        $env:BRAVE_SEARCH_API_KEY = "your-key-here"    (this session only)
    then restart Alyssa.

  - DuckDuckGo's lite HTML endpoint - used if no Brave key is set. No
    key/account/cost, but a lighter-weight HTML scrape rather than an
    official API, so a bit more fragile and less precise than Brave.

search_web() returns a string that gets both spoken out loud and fed back
to the model as its tool result, as source material for its reply.
"""
import os
import re
import html as _html
import urllib.parse

import requests

UNTRUSTED_OUTPUTS = {"search_web"}

_BRAVE_API_KEY = os.environ.get("BRAVE_SEARCH_API_KEY", "")
_BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
_DDG_ENDPOINT = "https://lite.duckduckgo.com/lite/"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def _clean(text: str) -> str:
    """Strips HTML tags/entities and collapses whitespace from a snippet."""
    text = re.sub(r"<[^>]+>", "", text)
    text = _html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _search_brave(query: str, num_results: int) -> list[dict]:
    response = requests.get(
        _BRAVE_ENDPOINT,
        params={"q": query, "count": num_results},
        headers={
            "Accept": "application/json",
            "X-Subscription-Token": _BRAVE_API_KEY,
        },
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()
    results = []
    for item in data.get("web", {}).get("results", [])[:num_results]:
        results.append(
            {
                "title": _clean(item.get("title", "")),
                "snippet": _clean(item.get("description", "")),
                "url": item.get("url", ""),
            }
        )
    return results


def _search_duckduckgo(query: str, num_results: int) -> list[dict]:
    response = requests.post(
        _DDG_ENDPOINT,
        data={"q": query},
        headers={"User-Agent": _USER_AGENT},
        timeout=10,
    )
    response.raise_for_status()
    page = response.text

    # lite.duckduckgo.com's markup is plain HTML tables: each result is a
    # "result-link" anchor followed by a "result-snippet" cell. A scrape
    # of their public lite page, not an official API - update these two
    # patterns if DuckDuckGo changes the markup.
    link_pattern = re.compile(
        r'class="result-link"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL
    )
    snippet_pattern = re.compile(
        r'class="result-snippet"[^>]*>(.*?)</td>', re.DOTALL
    )

    links = link_pattern.findall(page)
    snippets = snippet_pattern.findall(page)

    results = []
    for i, (url, title) in enumerate(links[:num_results]):
        # DuckDuckGo's lite results wrap the destination in a redirect URL
        # like //duckduckgo.com/l/?uddg=<encoded target>&... - unwrap it so
        # the real source URL is shown/spoken.
        match = re.search(r"uddg=([^&]+)", url)
        if match:
            url = urllib.parse.unquote(match.group(1))
        snippet = _clean(snippets[i]) if i < len(snippets) else ""
        results.append({"title": _clean(title), "snippet": snippet, "url": url})
    return results


def search_web(query: str, num_results: int = 5) -> str:
    query = (query or "").strip()
    if not query:
        return "I need something to search for."
    num_results = max(1, min(int(num_results), 8))

    try:
        if _BRAVE_API_KEY:
            results = _search_brave(query, num_results)
        else:
            results = _search_duckduckgo(query, num_results)
    except requests.exceptions.RequestException as e:
        return f"I couldn't search the web just now - {e}"
    except Exception as e:
        return f"Something went wrong parsing the search results - {e}"

    if not results:
        return f"I didn't find any results for '{query}'."

    lines = [f"Web search results for '{query}':"]
    for i, r in enumerate(results, start=1):
        title = r["title"] or r["url"]
        line = f"{i}. {title}"
        if r["snippet"]:
            line += f" - {r['snippet']}"
        if r["url"]:
            line += f" ({r['url']})"
        lines.append(line)
    return "\n".join(lines)


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": (
                "Searches the web for current information, facts, or sources "
                "on a topic - use for anything that needs up-to-date or "
                "factual info beyond what you already know (news, prices, "
                "who/what/when questions, 'look up ...', 'search for ...'). "
                "Returns a short list of titles, snippets, and source URLs "
                "so you can answer and cite where the info came from."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query.",
                    },
                    "num_results": {
                        "type": "integer",
                        "description": "How many results to return. Defaults to 5, max 8.",
                    },
                },
                "required": ["query"],
            },
        },
    },
]

FUNCTIONS = {
    "search_web": search_web,
}
