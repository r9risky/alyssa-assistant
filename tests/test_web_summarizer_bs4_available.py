"""beautifulsoup4 must import so web_summarizer does not silently fall back to regex."""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


class TestBeautifulSoupAvailable(unittest.TestCase):
    def test_bs4_importable(self):
        try:
            import bs4  # noqa: F401
        except ImportError as e:
            self.fail(
                "beautifulsoup4 is not installed - check it's listed in "
                f"requirements.txt and `pip install -r requirements.txt` "
                f"was run. Original error: {e}"
            )

    def test_web_summarizer_reports_bs4_available(self):
        import importlib
        import plugins.web_summarizer as web_summarizer
        importlib.reload(web_summarizer)  # pick up bs4 if it was just installed mid-session
        self.assertTrue(
            web_summarizer._BS4_AVAILABLE,
            "plugins/web_summarizer.py fell back to the regex HTML "
            "stripper - beautifulsoup4 is installed but the plugin's "
            "own import still failed, or the plugin's fallback logic "
            "changed. Check plugins/web_summarizer.py:9-14.",
        )


if __name__ == "__main__":
    unittest.main()
