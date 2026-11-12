"""One set of animation timings for every overlay window, so they all move alike."""
from PySide6.QtCore import QEasingCurve, QPropertyAnimation

FAST_MS = 140  # small things: the hover pill, a dim/undim
NORMAL_MS = 220  # windows appearing
SLOW_MS = 320  # windows leaving: a touch slower reads as calm, not abrupt


def fade(widget, to: float, ms: int = NORMAL_MS, hide_after: bool = False, show: bool = True):
    """Animate `widget`'s window opacity to `to`.

    show: show a hidden widget first (from transparent). hide_after: hide it once faded.
    Calling again mid-fade continues from wherever the opacity is now.
    """
    anim = getattr(widget, "_motion_fade", None)
    if anim is None:
        anim = QPropertyAnimation(widget, b"windowOpacity", widget)
        widget._motion_fade = anim
    anim.stop()
    if getattr(widget, "_motion_hides", False):
        anim.finished.disconnect(widget.hide)
        widget._motion_hides = False
    if not widget.isVisible():
        if not show:
            widget.setWindowOpacity(to)
            return
        widget.setWindowOpacity(0.0)
        widget.show()
    start = widget.windowOpacity()
    anim.setDuration(ms)
    anim.setStartValue(start)
    anim.setEndValue(to)
    anim.setEasingCurve(QEasingCurve.OutCubic if to >= start else QEasingCurve.InCubic)
    if hide_after:
        anim.finished.connect(widget.hide)
        widget._motion_hides = True
    anim.start()


def fade_in(widget, ms: int = NORMAL_MS):
    fade(widget, 1.0, ms)


def fade_out(widget, ms: int = SLOW_MS):
    """Fade out and hide; a widget that's already hidden stays hidden."""
    if widget.isVisible():
        fade(widget, 0.0, ms, hide_after=True)
