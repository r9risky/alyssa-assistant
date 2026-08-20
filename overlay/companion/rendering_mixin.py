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
        """Returns the current frame of the GIF at path, scaled/centered
        to the window's current size - and makes sure that GIF's QMovie
        is the one actually playing (stopping whichever one was playing
        before, if this is a switch between her idle/talking GIFs, so
        only one decodes frames at a time)."""
        movie = self._movies.get(path)
        if movie is None:
            movie = QMovie(path)
            movie.setCacheMode(QMovie.CacheAll)
            # Some GIFs have a finite (or zero) authored loop count - as a
            # background character she should loop forever regardless.
            movie.finished.connect(movie.start)
            movie.frameChanged.connect(self._on_movie_frame)
            self._movies[path] = movie

        if self._active_movie_path != path:
            self._stop_active_movie()
            self._active_movie_path = path
            movie.start()

        frame = movie.currentPixmap()
        if frame.isNull():
            # First call, before the movie has decoded frame 0 yet - fall
            # back to a static render just for this one paint so there's
            # never a blank flash; the next frameChanged tick replaces it.
            return render_character(self.settings, self.size(), mouth_open=mouth_open)
        return _scale_centered(frame, self.size())

    def _stop_active_movie(self):
        if self._active_movie_path is not None:
            movie = self._movies.get(self._active_movie_path)
            if movie is not None:
                movie.stop()
            self._active_movie_path = None

    def _on_movie_frame(self, _frame_number: int):
        # QMovie advances frames on its own internal timer, so this is the
        # actual animation tick for a GIF - only repaint if the signal came
        # from whichever movie is currently active/shown (a previous movie
        # can still be mid-decode of a queued frame right as we switch away).
        if self.sender() is self._movies.get(self._active_movie_path):
            self._update_pixmap()

    def _update_mask(self):
        if self._pixmap is None or self._pixmap.isNull():
            return
        region = QRegion(self._pixmap.mask()).translated(0, int(self._bounce_offset))
        # Always keep the resize grip clickable even if that corner of the
        # image happens to be transparent.
        gx = self.width() - RESIZE_GRIP
        gy = self.height() - RESIZE_GRIP
        region += QRegion(gx, gy, RESIZE_GRIP, RESIZE_GRIP, QRegion.Ellipse)
        self.setMask(region)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if self._pixmap is not None:
            painter.drawPixmap(0, int(self._bounce_offset), self._pixmap)

        # Resize grip glyph, always visible so it's discoverable even on a
        # transparent corner of the artwork. Color pulls from the current
        # theme accent so it matches the UI palette instead of being arbitrary.
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
        """Combines the base "Opacity" setting with the optional
        "dim while idle" setting -- she fades out a bit whenever she isn't
        talking, and comes back to full (base) opacity while she is."""
        base = float(self.settings.get("opacity", 1.0))
        if self.settings.get("dim_when_idle_enabled", False) and not self._talking:
            dim_pct = float(self.settings.get("dim_when_idle_opacity", 55)) / 100.0
            effective = base * dim_pct
        else:
            effective = base
        self.setWindowOpacity(max(0.05, min(1.0, effective)))
