from unittest.mock import patch

import pytest

import phone_remote


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_log_keeps_sources_with_her_reply():
    phone_remote.log("alyssa", "It's sunny.", sources=[{"url": "https://bbc.com/w", "site": "bbc.com", "title": ""}])
    assert phone_remote.messages_after(0)[-1]["sources"][0]["site"] == "bbc.com"


def test_history_window_shows_both_sides_escaped(app):
    import overlay
    from overlay import chat_history, widgets

    phone_remote.log("you", "is it <sunny>?")
    phone_remote.log("alyssa", "Yep!", sources=[{"url": 'https://x.y/?a="1"', "site": "x.y", "title": ""}])
    with patch.object(widgets, "save_overlay_settings"):
        companion = overlay.CompanionWindow(dict(overlay.DEFAULT_OVERLAY_SETTINGS), overlay.Bridge())
        dialog = chat_history.open_for(companion)
        text = dialog.view.toPlainText()
        assert "is it <sunny>?" in text  # escaped, so shown literally instead of eaten as a tag
        assert "Yep!" in text and "Sources: x.y" in text
        assert chat_history.open_for(companion) is dialog  # one window, raised again
        dialog.close()
        companion.close()


def test_empty_history_says_so():
    from overlay.chat_history import render_html
    from overlay.theming import _theme

    assert "Nothing yet" in render_html([], _theme(True))
