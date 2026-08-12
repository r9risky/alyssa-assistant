"""
Web & Article Summarizer plugin for Alyssa.

Gives Alyssa ability to fetch and summarize web pages or articles by URL ('summarize_webpage').
"""
import re
import requests

try:
    from bs4 import BeautifulSoup
    _BS4_AVAILABLE = True
except ImportError:
    BeautifulSoup = None
    _BS4_AVAILABLE = False

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}


def summarize_webpage(url: str) -> str:
    """Fetches a webpage and extracts clean text for summarization."""
    if not url or not url.strip():
        return "Please provide a web URL to summarize."

    clean_url = url.strip()
    if not clean_url.startswith(("http://", "https://")):
        clean_url = "https://" + clean_url

    try:
        response = requests.get(clean_url, headers=_HEADERS, timeout=12)
        response.raise_for_status()
        html = response.text
    except Exception as e:
        return f"Couldn't fetch web page at '{url}': {e}"

    title = ""
    text_content = ""

    if _BS4_AVAILABLE:
        soup = BeautifulSoup(html, "html.parser")
        # Remove script, style, nav, footer tags
        for element in soup(["script", "style", "nav", "footer", "header", "aside"]):
            element.extract()

        title = soup.title.string.strip() if soup.title and soup.title.string else ""

        # Extract text from paragraphs and headings
        paragraphs = [p.get_text().strip() for p in soup.find_all(["p", "h1", "h2", "h3"]) if p.get_text().strip()]
        text_content = " ".join(paragraphs)
    else:
        # Simple regex fallback
        title_match = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        if title_match:
            title = re.sub(r"<[^>]+>", "", title_match.group(1)).strip()

        body_match = re.search(r"<body.*?>(.*?)</body>", html, re.IGNORECASE | re.DOTALL)
        body_html = body_match.group(1) if body_match else html

        # Strip scripts and styles
        clean_html = re.sub(r"<(script|style).*?>.*?</\1>", "", body_html, flags=re.IGNORECASE | re.DOTALL)
        clean_text = re.sub(r"<[^>]+>", " ", clean_html)
        text_content = " ".join(clean_text.split())

    if not text_content:
        return f"Fetched webpage '{title or clean_url}', but couldn't find readable text content."

    # Truncate to reasonable length for LLM context / summary
    max_chars = 2000
    snippet = text_content[:max_chars] if len(text_content) > max_chars else text_content

    res_header = f"Page Title: {title}\nURL: {clean_url}\n\nKey Content:\n" if title else f"URL: {clean_url}\n\nKey Content:\n"
    return res_header + snippet


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "summarize_webpage",
            "description": "Fetches and extracts text content from a website or article URL for summarization or reading, e.g. 'summarize this website https://...', 'what is this article about'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The URL of the webpage or article to fetch (e.g. 'https://en.wikipedia.org/wiki/Artificial_intelligence')."}
                },
                "required": ["url"],
            },
        },
    },
]

FUNCTIONS = {
    "summarize_webpage": summarize_webpage,
}
