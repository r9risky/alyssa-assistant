"""
Web & Article Summarizer plugin for Alyssa.

Gives Alyssa ability to fetch and summarize web pages or articles by URL ('summarize_webpage').
"""
import ipaddress
import re
import socket
import time
import urllib.parse

import requests
from requests.adapters import HTTPAdapter

UNTRUSTED_OUTPUTS = {"summarize_webpage"}

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
_MAX_RESPONSE_BYTES = 1_000_000
_MAX_FETCH_SECONDS = 30
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class _PinnedHTTPSAdapter(HTTPAdapter):
    """Connect to a resolved IP while validating TLS for the original host."""

    def __init__(self, hostname: str):
        self.hostname = hostname
        super().__init__()

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        pool_kwargs["assert_hostname"] = self.hostname
        pool_kwargs["server_hostname"] = self.hostname
        return super().init_poolmanager(connections, maxsize, block, **pool_kwargs)


def _resolve_public_target(url: str):
    try:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("URL must use HTTP or HTTPS")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("URL credentials are not allowed")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        addresses = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    except OSError as e:
        raise ValueError("URL could not be resolved") from e
    if not addresses:
        raise ValueError("URL could not be resolved")
    public_ips = []
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address[4][0])
            if getattr(ip, "ipv4_mapped", None):
                ip = ip.ipv4_mapped
            if not ip.is_global:
                raise ValueError("URL must resolve to a public internet address")
            public_ips.append(ip)
        except ValueError:
            raise ValueError("URL must resolve to a public internet address")
    public_ips.sort(key=lambda ip: ip.version)  # prefer IPv4 where both are available
    return parsed, public_ips[0]


def _request_public_url(url: str, timeout: float):
    parsed, ip = _resolve_public_target(url)
    hostname = parsed.hostname.encode("idna").decode("ascii")
    ip_host = f"[{ip}]" if ip.version == 6 else str(ip)
    port = parsed.port
    pinned_netloc = ip_host + (f":{port}" if port is not None else "")
    pinned_url = urllib.parse.urlunsplit(
        (parsed.scheme, pinned_netloc, parsed.path or "/", parsed.query, "")
    )
    host_header = f"[{hostname}]" if ":" in hostname else hostname
    if port is not None:
        host_header += f":{port}"

    session = requests.Session()
    if parsed.scheme == "https":
        session.mount("https://", _PinnedHTTPSAdapter(hostname))
    try:
        response = session.get(
            pinned_url,
            headers={**_HEADERS, "Host": host_header},
            timeout=timeout,
            allow_redirects=False,
            stream=True,
        )
    except Exception:
        session.close()
        raise
    return response, session


def _fetch_public_page(url: str) -> tuple[str, str]:
    current = url
    deadline = time.monotonic() + _MAX_FETCH_SECONDS
    for _ in range(6):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("webpage fetch timed out")
        response, session = _request_public_url(current, min(12, remaining))
        try:
            if response.status_code in _REDIRECT_STATUSES:
                location = response.headers.get("Location")
                if not location:
                    raise ValueError("redirect response had no destination")
                current = urllib.parse.urljoin(current, location)
                continue
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "").lower()
            if content_type and not (
                content_type.startswith("text/")
                or content_type.startswith("application/xhtml+xml")
            ):
                raise ValueError("URL did not return a text webpage")
            try:
                declared_size = int(response.headers.get("Content-Length", 0))
            except (TypeError, ValueError):
                declared_size = 0
            if declared_size > _MAX_RESPONSE_BYTES:
                raise ValueError("webpage is too large to summarize")
            body = bytearray()
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if time.monotonic() >= deadline:
                    raise TimeoutError("webpage fetch timed out")
                body.extend(chunk)
                if len(body) > _MAX_RESPONSE_BYTES:
                    raise ValueError("webpage is too large to summarize")
            return current, bytes(body).decode(response.encoding or "utf-8", errors="replace")
        finally:
            response.close()
            session.close()
    raise ValueError("too many webpage redirects")


def summarize_webpage(url: str) -> str:
    """Fetches a webpage and extracts clean text for summarization."""
    if not url or not url.strip():
        return "Please provide a web URL to summarize."

    clean_url = url.strip()
    if not clean_url.startswith(("http://", "https://")):
        clean_url = "https://" + clean_url

    try:
        clean_url, html = _fetch_public_page(clean_url)
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
