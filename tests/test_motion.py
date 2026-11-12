"""Overlay windows share one fade helper; the hover pill shows while the mouse is on her."""
from unittest.mock import patch

import pytest


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_fade_out_then_in_reverses_cleanly(app):
    from PySide6.QtWidgets import QWidget
    from overlay import motion

    w = QWidget()
    motion.fade_in(w)
    assert w.isVisible()
    motion.fade_out(w)
    assert w._motion_hides
    motion.fade_in(w)  # back in mid-fade: must not hide when the old fade would have ended
    assert not w._motion_hides
    w._motion_fade.setCurrentTime(w._motion_fade.duration())
    assert w.isVisible()


def test_hover_pill_appears_on_hover_and_leaves_after(app):
    import overlay
    from overlay import widgets

    with patch.object(widgets, "save_overlay_settings"):
        companion = overlay.CompanionWindow(dict(overlay.DEFAULT_OVERLAY_SETTINGS), overlay.Bridge())
        companion.hover_pill = widgets.HoverPill(companion, companion.settings)
        companion.keep_hover_pill()
        assert companion.hover_pill.isVisible()
        companion.release_hover_pill()
        assert companion._pill_hide_timer.isActive()
        companion.keep_hover_pill()  # mouse moved onto the pill itself
        assert not companion._pill_hide_timer.isActive()
        companion.hover_pill.close()
        companion.close()
