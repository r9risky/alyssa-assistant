import math
import os
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QMovie, QPainter, QPixmap, QRegion

from .. import poses
from ..rendering import (
    ASSETS_DIR, RESIZE_GRIP, _resolve_character_path, _scale_centered, render_character,
)

FRAMES_DIR = os.path.join(ASSETS_DIR, "frames")
from ..theming import _theme


class RenderingMixin:
    def _update_pixmap(self):
        mouth_open = self._talking and self._mouth_open and self.settings.get("talk_mouth_flap_enabled", True)
        path = _resolve_character_path(self.settings, mouth_open)
        frames = poses.frame_paths(FRAMES_DIR, getattr(self, "_pose_state", "idle"))
        if frames:
            self._stop_active_movie()
            frame = QPixmap(frames[getattr(self, "_frame_index", 0) % len(frames)])
            self._pixmap = _scale_centered(frame, self.size())
        elif path.lower().endswith(".gif"):
            self._pixmap = self._frame_from_movie(path, mouth_open)
        else:
            self._stop_active_movie()
            self._pixmap = render_character(self.settings, self.size(), mouth_open=mouth_open)
        self._update_mask()
        self.update()

    def _frame_from_movie(self, path: str, mouth_open: bool) -> QPixmap:
        """Current GIF frame, scaled/centered. Stops the previous movie so only one GIF decodes at a time."""
        movie = self._movies.get(path)
        if movie is None:
            movie = QMovie(path)
            movie.setCacheMode(QMovie.CacheAll)
            movie.finished.connect(movie.start)  # loop forever regardless of authored loop count
            movie.frameChanged.connect(self._on_movie_frame)
            self._movies[path] = movie

        if self._active_movie_path != path:
            self._stop_active_movie()
            self._active_movie_path = path
            movie.start()

        frame = movie.currentPixmap()
        if frame.isNull():
            # Frame 0 not decoded yet: static render this paint; frameChanged replaces it.
            return render_character(self.settings, self.size(), mouth_open=mouth_open)
        return _scale_centered(frame, self.size())

    def _stop_active_movie(self):
        if self._active_movie_path is not None:
            movie = self._movies.get(self._active_movie_path)
            if movie is not None:
                movie.stop()
            self._active_movie_path = None

    def _on_movie_frame(self, _frame_number: int):
        # GIF tick: ignore queued frames from a movie we already switched away from.
        if self.sender() is self._movies.get(self._active_movie_path):
            self._update_pixmap()

    def _update_pose(self, dt: float) -> bool:
        """Advance the pose clock; True if she needs repainting."""
        land_age = 0.0
        landing = False
        if self._land_started is not None:
            land_age = time.monotonic() - self._land_started
            landing = land_age < 0.6
            if not landing:
                self._land_started = None
        drag_dx = 0.0
        if self._dragging:
            if self._last_drag_x is not None:
                drag_dx = self.x() - self._last_drag_x
            self._last_drag_x = self.x()
        else:
            self._last_drag_x = None

        state = poses.pick_state(
            dragging=self._dragging, landing=landing, sleeping=self._sleeping,
            talking=self._talking, mood=getattr(self, "_current_mood", ""),
        )
        changed = False
        if state != self._pose_state:
            self._pose_state = state
            self._frame_index = 0
            changed = True
        self._pose_time += dt
        if poses.frame_paths(FRAMES_DIR, state):
            index = int(self._pose_time * poses.FRAME_FPS)
            if index != self._frame_index or changed:
                self._frame_index = index
                self._update_pixmap()
        elif changed:
            self._update_pixmap()  # leaving a drawn-frames state: back to the base art

        if not self.settings.get("poses_enabled", True):
            tilt, squash = 0.0, 1.0
        else:
            tilt, squash = poses.pose_for(state, self._pose_time, drag_dx, land_age)
        if abs(tilt - self._pose_tilt) < 0.05 and abs(squash - self._pose_squash) < 0.001:
            return changed
        remask = abs(tilt - getattr(self, "_mask_tilt", 0.0)) > 1.5 or (tilt == 0.0 and squash == 1.0)
        self._pose_tilt, self._pose_squash = tilt, squash
        if remask:
            self._update_mask()
        return True

    def _posed_pixmap(self) -> QPixmap:
        """The character leaned (sheared above the bottom edge) and squashed, on a same-size canvas."""
        tilt = getattr(self, "_pose_tilt", 0.0)
        squash = getattr(self, "_pose_squash", 1.0)
        if abs(tilt) < 0.05 and abs(squash - 1.0) < 0.001:
            return self._pixmap
        out = QPixmap(self._pixmap.size())
        out.fill(Qt.transparent)
        p = QPainter(out)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        w, h = self._pixmap.width(), self._pixmap.height()
        p.translate(w / 2, h)
        # Shear, not rotate: her art is a bust cut off at the bottom edge, so the
        # bottom row must stay put while her head and shoulders lean.
        p.shear(-math.tan(math.radians(tilt)), 0.0)
        p.scale(2.0 - squash, squash)  # keep volume: wider as she squashes
        p.drawPixmap(int(-w / 2), -h, self._pixmap)
        p.end()
        return out

    def _update_mask(self):
        if self._pixmap is None or self._pixmap.isNull():
            return
        self._mask_tilt = getattr(self, "_pose_tilt", 0.0)
        total_y = int(
            self._bounce_offset
            + getattr(self, "_breathing_offset", 0.0)
            + getattr(self, "_gaze_y_offset", 0.0)
        )
        total_x = int(getattr(self, "_gaze_x_offset", 0.0))
        region = QRegion(self._posed_pixmap().mask()).translated(total_x, total_y)
        # Keep the resize grip clickable even if that corner of the image is transparent.
        gx = self.width() - RESIZE_GRIP
        gy = self.height() - RESIZE_GRIP
        region += QRegion(gx, gy, RESIZE_GRIP, RESIZE_GRIP, QRegion.Ellipse)

        # Ensure emote badge area is unclipped in the window mask
        emote = getattr(self, "_current_emote", None)
        if emote and self.settings.get("emotes_enabled", True):
            badge_x = max(8, min(self.width() - 32, self.width() // 2 + 12))
            float_y = total_y + 18 + int(math.sin(getattr(self, "_emote_float_phase", 0.0)) * 2.5)
            region += QRegion(badge_x - 6, float_y - 22, 34, 30, QRegion.Rectangle)

        self.setMask(region)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        draw_x = int(getattr(self, "_gaze_x_offset", 0.0))
        draw_y = int(
            self._bounce_offset
            + getattr(self, "_breathing_offset", 0.0)
            + getattr(self, "_gaze_y_offset", 0.0)
        )

        if self._pixmap is not None:
            posed = self._posed_pixmap()
            if getattr(self, "_blink_active", False) and self.settings.get("blinking_enabled", True):
                # Subtle eye-squash micro-blink
                squash_h = max(1, int(posed.height() * 0.93))
                squashed = posed.scaled(
                    posed.width(), squash_h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation
                )
                squash_y = draw_y + (posed.height() - squash_h)
                painter.drawPixmap(draw_x, squash_y, squashed)
            else:
                painter.drawPixmap(draw_x, draw_y, posed)

        # Dynamic floating mood emote badge
        emote = getattr(self, "_current_emote", None)
        if emote and self.settings.get("emotes_enabled", True):
            badge_font = painter.font()
            badge_font.setPointSize(14)
            painter.setFont(badge_font)
            float_y = draw_y + 18 + int(math.sin(getattr(self, "_emote_float_phase", 0.0)) * 2.5)
            badge_x = max(8, min(self.width() - 32, self.width() // 2 + 12))
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(18, 18, 26, 210))
            painter.drawRoundedRect(badge_x - 3, float_y - 18, 28, 24, 8, 8)
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(badge_x, float_y, emote)

        gx = self.width() - RESIZE_GRIP
        gy = self.height() - RESIZE_GRIP
        t_grip = _theme(self.settings)
        accent_color = QColor(t_grip.get("accent", "#6366F1"))
        accent_color.setAlpha(80)
        painter.setPen(Qt.NoPen)
        painter.setBrush(accent_color)
        painter.drawEllipse(gx, gy, RESIZE_GRIP, RESIZE_GRIP)
        painter.setPen(QColor(255, 255, 255, 180))
        for i in range(3):
            off = 5 + i * 4
            painter.drawLine(
                gx + RESIZE_GRIP - off, gy + RESIZE_GRIP - 3,
                gx + RESIZE_GRIP - 3, gy + RESIZE_GRIP - off,
            )

    def _update_effective_opacity(self):
        """Base opacity × dim-when-idle (idle only). Talking uses full base opacity."""
        base = float(self.settings.get("opacity", 1.0))
        if self.settings.get("dim_when_idle_enabled", False) and not self._talking:
            dim_pct = float(self.settings.get("dim_when_idle_opacity", 55)) / 100.0
            effective = base * dim_pct
        else:
            effective = base
        self.setWindowOpacity(max(0.05, min(1.0, effective)))
