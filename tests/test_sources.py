from unittest.mock import patch

import sources


def test_record_keeps_one_http_link_per_site_and_take_clears():
    sources.clear()
    sources.record("https://www.bbc.com/news/a", "A")
    sources.record("https://bbc.com/news/b", "B")  # same site, skipped
    sources.record("javascript:alert(1)", "bad")
    sources.record("file:///C:/secret.txt", "bad")
    sources.record("http://example.org/x")
    found = sources.take()
    assert [s["site"] for s in found] == ["bbc.com", "example.org"]
    assert found[0]["url"] == "https://www.bbc.com/news/a"
    assert sources.take() == []


def test_take_caps_at_max():
    sources.clear()
    for i in range(6):
        sources.record(f"https://site{i}.com/")
    assert len(sources.take()) == sources.MAX_SOURCES


def test_bubble_links_sources_only_for_their_reply():
    from overlay.widgets import SpeechBubble

    bubble = SpeechBubble(None, {"bubble_seconds": 5})
    with patch("voice_dsp.play_typing_blip"):
        bubble.show_message("It is <sunny> today, according to BBC.")
        bubble.show_sources([{"url": 'https://bbc.com/w?a=1&b="x"', "title": "", "site": "bbc.com"}])
        text = bubble.label.text()
        assert "&lt;sunny&gt;" in text
        assert 'href="https://bbc.com/w?a=1&amp;b=&quot;x&quot;"' in text
        assert ">bbc.com</a>" in text

        bubble.show_message("A new reply with no lookup.")
        assert "Sources" not in bubble.label.text()


def test_search_web_records_result_urls():
    from plugins import web_search

    sources.clear()
    fake = [{"title": "T", "snippet": "S", "url": "https://example.com/page"}]
    with patch.object(web_search, "_BRAVE_API_KEY", ""), \
         patch.object(web_search, "_search_duckduckgo", return_value=fake):
        assert "example.com/page" in web_search.search_web("weather")
    assert [s["site"] for s in sources.take()] == ["example.com"]
