import random

from PySide6.QtWidgets import QMessageBox

import config


class TalkStateMixin:
    # Bound methods (not lambdas) so PySide auto-queues onto the GUI thread.
    def start_talking(self):
        self.set_talking(True)

    def stop_talking(self):
        self.set_talking(False)

    def show_error(self, message: str):
        QMessageBox.critical(self, config.ASSISTANT_NAME, message)

    # -- PNGTuber talk animation (mouth flap + bounce) --------------------
    def set_talking(self, talking: bool):
        """Drive mouth-open swap + bounce (each toggleable in Settings). Called via Bridge around speech."""
        if talking == self._talking:
            return
        self._talking = talking
        if talking:
            self._talk_tick = 0
            # Mouth open on this frame (don't wait 50ms); random flap cadence takes over after.
            self._mouth_open = True
            self._next_flap_tick = random.randint(2, 4)
            self._talk_anim_timer.start(50)
            self._update_pixmap()  # repaint now - don't wait for the first timer tick
        else:
            self._talk_anim_timer.stop()
            self._mouth_open = False
            self._update_pixmap()

        # Ease toward up/down immediately; bounce timer keeps running until settled (after flap timer stops).
        height = max(0, float(self.settings.get("talk_bounce_height", 0)))
        height *= self.settings.get("scale", 1.0)
        self._bounce_target = -height if (talking and self.settings.get("talk_bounce_enabled", True)) else 0.0
        if not self._bounce_anim_timer.isActive():
            self._bounce_anim_timer.start(20)

        self._update_effective_opacity()

    def _on_bounce_anim_tick(self):
        # Ease toward target (not a snap).
        diff = self._bounce_target - self._bounce_offset
        if abs(diff) < 0.4:
            self._bounce_offset = self._bounce_target
            self._bounce_anim_timer.stop()
        else:
            self._bounce_offset += diff * 0.35
        self._update_pixmap()

    def _on_talk_tick(self):
        self._talk_tick += 1

        if self.settings.get("talk_mouth_flap_enabled", True):
            if self._talk_tick >= self._next_flap_tick:
                self._mouth_open = not self._mouth_open
                # Open→closed is quicker than closed→open.
                gap = random.randint(2, 4) if self._mouth_open else random.randint(1, 3)
                self._next_flap_tick = self._talk_tick + gap
        else:
            self._mouth_open = False

        self._update_pixmap()
