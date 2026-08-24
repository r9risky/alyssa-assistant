import random

from PySide6.QtWidgets import QMessageBox

import config


class TalkStateMixin:
    # -- thread-safe entry points for Bridge signals ----------------------
    # Real bound methods (not a lambda) so PySide6 detects they belong to
    # a GUI-thread QObject and auto-queues the call rather than running it
    # directly on the background thread - see run_with_assistant() below.
    def start_talking(self):
        self.set_talking(True)

    def stop_talking(self):
        self.set_talking(False)

    def show_error(self, message: str):
        QMessageBox.critical(self, config.ASSISTANT_NAME, message)

    # -- PNGTuber talk animation (mouth flap + bounce) --------------------
    def set_talking(self, talking: bool):
        """Called from the assistant loop (via Bridge signals) right
        before/after she speaks. Drives the mouth-open image swap and the
        bounce animation; both are individually toggleable in Settings."""
        if talking == self._talking:
            return
        self._talking = talking
        if talking:
            self._talk_tick = 0
            # Snap the mouth open immediately (not on the first 50ms timer
            # tick) so bounce, mouth, and playback all start on the same
            # frame; the random flap cadence below takes over right after.
            self._mouth_open = True
            self._next_flap_tick = random.randint(2, 4)
            self._talk_anim_timer.start(50)
            self._update_pixmap()  # repaint now - don't wait for the first timer tick
        else:
            self._talk_anim_timer.stop()
            self._mouth_open = False
            self._update_pixmap()

        # Ease toward "up" the instant talking starts, and toward "down"
        # the instant it stops - the ease timer keeps running until she
        # actually settles, even after the mouth-flap timer has stopped.
        height = max(0, float(self.settings.get("talk_bounce_height", 0)))
        height *= self.settings.get("scale", 1.0)
        self._bounce_target = -height if (talking and self.settings.get("talk_bounce_enabled", True)) else 0.0
        if not self._bounce_anim_timer.isActive():
            self._bounce_anim_timer.start(20)

        self._update_effective_opacity()

    def _on_bounce_anim_tick(self):
        # Simple ease toward the current target - quick but not instant, so
        # both the "pop up" and the "settle back down" read as motion
        # rather than a snap.
        diff = self._bounce_target - self._bounce_offset
        if abs(diff) < 0.4:
            self._bounce_offset = self._bounce_target
            self._bounce_anim_timer.stop()
        else:
            self._bounce_offset += diff * 0.35
        self._update_pixmap()

    def _on_talk_tick(self):
        self._talk_tick += 1

        # Flap the mouth open/closed a few times a second, like a
        # PNGTuber. A slightly randomized interval reads closer to real
        # speech cadence than a rigid fixed one.
        if self.settings.get("talk_mouth_flap_enabled", True):
            if self._talk_tick >= self._next_flap_tick:
                self._mouth_open = not self._mouth_open
                # Open->closed snaps back a bit quicker than closed->open,
                # which is what a mouth actually does when talking.
                gap = random.randint(2, 4) if self._mouth_open else random.randint(1, 3)
                self._next_flap_tick = self._talk_tick + gap
        else:
            self._mouth_open = False

        self._update_pixmap()
