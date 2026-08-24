from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QMovie, QPainter, QPixmap, QRegion

from ..rendering import (
    RESIZE_GRIP, _resolve_character_path, _scale_centered, render_character,
)
from ..theming import _theme


class RenderingMixin:
    def _update_pixmap(self):
        mouth_open = self._talking and self._mouth_open and self.settings.get("talk_mouth_flap_enabled", True)
        path = _resolve_character_path(self.settings, mouth_open)
        if path.lower().endswith(".gif"):
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

    def _update_mask(self):
        if self._pixmap is None or self._pixmap.isNull():
            return
        region = QRegion(self._pixmap.mask()).translated(0, int(self._bounce_offset))
        # Keep the resize grip clickable even if that corner of the image is transparent.
        gx = self.width() - RESIZE_GRIP
        gy = self.height() - RESIZE_GRIP
        region += QRegion(gx, gy, RESIZE_GRIP, RESIZE_GRIP, QRegion.Ellipse)
        self.setMask(region)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if self._pixmap is not None:
            painter.drawPixmap(0, int(self._bounce_offset), self._pixmap)

        gx = self.width() - RESIZE_GRIP
        gy = self.height() - RESIZE_GRIP
        t_grip = _theme(self.settings)
        accent_hex = t_grip["accent"]
        r = int(accent_hex[1:3], 16)
        g = int(accent_hex[3:5], 16)
        b = int(accent_hex[5:7], 16)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(r, g, b, 80))
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
