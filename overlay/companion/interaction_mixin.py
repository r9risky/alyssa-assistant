import random
import time

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication, QMenu

import config
from ..ledges import snap_to_nearest_ledge
from ..rendering import BASE_H, BASE_W, MIN_W, RESIZE_GRIP
from . import save_overlay_settings
from ..theming import _build_menu_style

_HEAD_PAT_LINES = [
    "Hehe, that tickles.",
    "Careful, you'll mess up my hair.",
    "Okay, okay, I'm purring—happy now?",
    "Best desktop spot in the house.",
    "Head pats accepted. Carry on.",
    "You know how to keep an assistant happy.",
    "Alright, alright, back to work soon.",
    "Five-star head pat service right there.",
    "Hmm, not bad. You can keep doing that.",
    "Wait—did I earn a bonus pat?",
    "Hey, watch the bangs!",
    "Cozy. Very cozy.",
    "Recharging battery via head pats...",
    "Okay, I'm officially spoiled now.",
]


class InteractionMixin:
    def _in_resize_grip(self, pos: QPoint) -> bool:
        return pos.x() >= self.width() - RESIZE_GRIP and pos.y() >= self.height() - RESIZE_GRIP

    def _record_user_activity(self):
        self._last_interaction_time = time.monotonic()
        if getattr(self, "_sleeping", False):
            self.wake_up()

    def mousePressEvent(self, event):
        self._record_user_activity()
        if event.button() == Qt.LeftButton:
            if getattr(self, "_riding_cursor", False):
                self.stop_cursor_ride()
                return
            pos = event.position().toPoint()
            if self._in_resize_grip(pos):
                self._resizing = True
                self._resize_start_pos = event.globalPosition().toPoint()
                self._resize_start_size = self.size()
            else:
                self._dragging = True
                self._drag_offset = event.globalPosition().toPoint() - self.pos()
                if getattr(self, "hover_pill", None) is not None:
                    self.hover_pill.hide()  # it'd trail behind her while she's carried
        elif event.button() == Qt.RightButton:
            self._show_context_menu(event.globalPosition().toPoint())

    def mouseMoveEvent(self, event):
        gp = event.globalPosition().toPoint()
        if self._resizing:
            delta = gp.x() - self._resize_start_pos.x()
            new_w = max(MIN_W, self._resize_start_size.width() + delta)
            new_h = int(new_w * BASE_H / BASE_W)
            self.resize(new_w, new_h)
            self._update_pixmap()
            self._reposition_attachments()
        elif self._dragging:
            self.move(gp - self._drag_offset)
            self._reposition_attachments()
        else:
            self._detect_head_pat(event.position().toPoint())

    def _detect_head_pat(self, local_pos: QPoint):
        """Detect back-and-forth horizontal cursor rubbing over her head area."""
        if not self.settings.get("head_pats_enabled", True):
            return
        if local_pos.y() > int(self.height() * 0.55):
            return
        now = time.monotonic()
        last_pat = getattr(self, "_last_pat_triggered_at", 0.0)
        if now - last_pat < 20.0:
            return
        last_x = getattr(self, "_pat_last_x", None)
        last_dir = getattr(self, "_pat_last_dir", 0)
        reversals = getattr(self, "_pat_reversals", 0)
        window_start = getattr(self, "_pat_window_start", now)
        if now - window_start > 2.5:
            reversals = 0
            window_start = now
        if last_x is not None:
            dx = local_pos.x() - last_x
            if abs(dx) >= 8:
                direction = 1 if dx > 0 else -1
                if last_dir != 0 and direction != last_dir:
                    reversals += 1
                    if reversals == 1:
                        window_start = now
                self._pat_last_dir = direction
                self._pat_last_x = local_pos.x()
        else:
            self._pat_last_x = local_pos.x()
        self._pat_reversals = reversals
        self._pat_window_start = window_start
        if reversals >= 4:
            self._pat_reversals = 0
            self.trigger_head_pat()

    def trigger_head_pat(self) -> str:
        """Play a non-repeating head-pat reaction line and happy bounce."""
        self._last_pat_triggered_at = time.monotonic()
        self._record_user_activity()
        pool = getattr(self, "_pat_line_pool", None)
        if not pool:
            pool = list(_HEAD_PAT_LINES)
            random.shuffle(pool)
            self._pat_line_pool = pool
        line = pool.pop()
        self._bounce_target = -10.0
        if not self._bounce_anim_timer.isActive():
            self._bounce_anim_timer.start(16)
        if hasattr(self, "set_mood"):
            self.set_mood("happy")
        if self.bubble:
            self.bubble.show_message(line)
        return line

    def mouseReleaseEvent(self, event):
        if self._dragging or self._resizing:
            was_dragging = self._dragging
            self._dragging = False
            if was_dragging:
                self._land_started = time.monotonic()  # squash-and-spring pose
            self._resizing = False
            if was_dragging and self.settings.get("perch_on_windows", True):
                snapped_x, snapped_y, _ledge = snap_to_nearest_ledge(
                    self.x(), self.y(), self.width(), self.height()
                )
                if (snapped_x, snapped_y) != (self.x(), self.y()):
                    self.move(snapped_x, snapped_y)
                    self._reposition_attachments()
            self.settings["scale"] = round(self.width() / BASE_W, 3)
            self.settings["pos_x"] = self.x()
            self.settings["pos_y"] = self.y()
            save_overlay_settings(self.settings)
            dialog = getattr(self, "_settings_dialog", None)
            if dialog is not None:
                try:
                    dialog.sync_companion_scale(self.settings["scale"])
                except RuntimeError:
                    self._settings_dialog = None

    def mouseDoubleClickEvent(self, event):
        self._record_user_activity()
        if self.settings.get("cursor_ride_enabled", True) and (
            event.modifiers() & Qt.ShiftModifier or getattr(self, "_riding_cursor", False)
        ):
            self.toggle_cursor_ride()
            return
        # A little life sign so it's obvious she's interactive.
        if self.bubble:
            self.bubble.show_message(random.choice([
                "Yes? I'm listening whenever you say my name.",
                "Right-click me any time you'd like to change my settings.",
                "At your service.",
            ]))

    # -- context menu / settings ----------------------------------------
    def _show_context_menu(self, global_pos):
        menu = QMenu()
        menu.setAttribute(Qt.WA_TranslucentBackground)
        menu.setStyleSheet(_build_menu_style(self.settings))
        settings_action = menu.addAction("Settings…")
        menu.addSeparator()

        quiet_action = menu.addAction("Quiet Mode (Pause Proactive Speech)")
        quiet_action.setCheckable(True)
        quiet_action.setChecked(bool(getattr(config, "QUIET_MODE", False)))

        ride_label = "Hop Off Cursor" if getattr(self, "_riding_cursor", False) else "Ride Cursor"
        ride_action = menu.addAction(ride_label)

        player_action = menu.addAction("Mini Music Player")
        player_action.setCheckable(True)
        player_action.setChecked(bool(self.settings.get("mini_player_enabled", False)))

        games_menu = menu.addMenu("Play Mini-Game")
        ttt_action = games_menu.addAction("Tic-Tac-Toe")
        c4_action = games_menu.addAction("Connect Four")
        rps_action = games_menu.addAction("Rock Paper Scissors")
        chess_action = games_menu.addAction("Chess")

        clear_ink_action = menu.addAction("Clear Screen Ink")
        menu.addSeparator()

        top_action = menu.addAction("Always on Top")
        top_action.setCheckable(True)
        top_action.setChecked(bool(self.settings.get("always_on_top", True)))
        reset_action = menu.addAction("Reset Position && Size")
        menu.addSeparator()
        hide_action = menu.addAction("Hide (right-click the tray icon to bring her back)")
        quit_action = menu.addAction("Quit Alyssa")

        chosen = menu.exec(global_pos)
        if chosen is None:
            return
        if chosen == settings_action:
            self.open_settings()
        elif chosen == quiet_action:
            config.QUIET_MODE = quiet_action.isChecked()
            if hasattr(self, "hover_pill") and self.hover_pill is not None:
                self.hover_pill.refresh_state()
        elif chosen == ride_action:
            self.toggle_cursor_ride()
        elif chosen == player_action:
            self.settings["mini_player_enabled"] = player_action.isChecked()
            if hasattr(self, "mini_player") and self.mini_player is not None:
                self.mini_player.apply_enabled()
            save_overlay_settings(self.settings)
        elif chosen in (ttt_action, c4_action, rps_action, chess_action):
            game_map = {
                ttt_action: "tic_tac_toe",
                c4_action: "connect_four",
                rps_action: "rps",
                chess_action: "chess",
            }
            try:
                from plugins import mini_games_and_ink
                mini_games_and_ink.open_mini_game_window(game_map[chosen], parent=self)
            except Exception as e:
                print(f"[mini-games] Failed to open game window: {e}")
        elif chosen == clear_ink_action:
            try:
                from plugins import mini_games_and_ink
                mini_games_and_ink.draw_on_screen(action="clear")
            except Exception:
                pass
        elif chosen == top_action:
            self._set_always_on_top(top_action.isChecked())
        elif chosen == reset_action:
            self._reset_position_only()
            self.settings["scale"] = 1.0
            self.resize(BASE_W, BASE_H)
            self._update_pixmap()
            dialog = getattr(self, "_settings_dialog", None)
            if dialog is not None:
                dialog.sync_companion_scale(1.0)
            save_overlay_settings(self.settings)
        elif chosen == hide_action:
            self.hide()
        elif chosen == quit_action:
            QApplication.instance().quit()

