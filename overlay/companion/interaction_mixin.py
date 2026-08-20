import random

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication, QMenu

from ..rendering import BASE_H, BASE_W, MIN_W, RESIZE_GRIP
from . import save_overlay_settings
from ..theming import _build_menu_style


class InteractionMixin:
    def _in_resize_grip(self, pos: QPoint) -> bool:
        return pos.x() >= self.width() - RESIZE_GRIP and pos.y() >= self.height() - RESIZE_GRIP

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            pos = event.position().toPoint()
            if self._in_resize_grip(pos):
                self._resizing = True
                self._resize_start_pos = event.globalPosition().toPoint()
                self._resize_start_size = self.size()
            else:
                self._dragging = True
                self._drag_offset = event.globalPosition().toPoint() - self.pos()
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
            if self.bubble:
                self.bubble.reposition()
            if self.chatbar:
                self.chatbar.reposition()
        elif self._dragging:
            self.move(gp - self._drag_offset)
            if self.bubble:
                self.bubble.reposition()
            if self.chatbar:
                self.chatbar.reposition()

    def mouseReleaseEvent(self, event):
        if self._dragging or self._resizing:
            self._dragging = False
            self._resizing = False
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
