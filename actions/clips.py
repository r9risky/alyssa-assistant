"""Game replay clipping buffer and MP4 video generation.

Captures and holds rolling gameplay video frames in a circular memory buffer,
allowing instant 'clip that!' voice/chat commands to render the last 30 seconds
into Videos/Alyssa/<game>_<timestamp>.mp4 using ffmpeg.
"""

from __future__ import annotations

import collections
import datetime
import io
import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple, Union

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

logger = logging.getLogger("actions.clips")

DEFAULT_CLIP_SECONDS = 30
MAX_BUFFER_SECONDS = 120
TARGET_FPS = 24


def get_clips_directory() -> Path:
    """Return directory for saved clips (Videos/Alyssa or ./clips)."""
    home = Path.home()
    videos_dir = home / "Videos" / "Alyssa"
    if not videos_dir.exists():
        try:
            videos_dir.mkdir(parents=True, exist_ok=True)
            return videos_dir
        except Exception:
            pass

    # Fallback to local clips folder
    local_dir = Path("clips")
    local_dir.mkdir(parents=True, exist_ok=True)
    return local_dir


class ClipFrame:
    """Single timestamped frame compressed as JPEG bytes to keep RAM light."""

    __slots__ = ("timestamp", "jpeg_bytes", "width", "height")

    def __init__(self, timestamp: float, jpeg_bytes: bytes, width: int, height: int):
        self.timestamp = timestamp
        self.jpeg_bytes = jpeg_bytes
        self.width = width
        self.height = height


class ReplayBuffer:
    """Thread-safe rolling buffer holding recent frames for clipping."""

    def __init__(self, max_seconds: int = MAX_BUFFER_SECONDS, target_fps: int = TARGET_FPS):
        self.max_seconds = max_seconds
        self.target_fps = target_fps
        self._lock = threading.Lock()
        self._frames: Deque[ClipFrame] = collections.deque()
        self._active_game: Optional[str] = None
        self._last_added_time: float = 0.0
        self._min_frame_interval = 1.0 / target_fps

    def set_active_game(self, game_name: Optional[str]) -> None:
        with self._lock:
            self._active_game = game_name

    def clear(self) -> None:
        with self._lock:
            self._frames.clear()

    def add_frame(
        self,
        frame: Union[Image.Image, np.ndarray, bytes],
        timestamp: Optional[float] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
    ) -> bool:
        """Add a frame to the rolling replay buffer."""
        now = time.time() if timestamp is None else float(timestamp)
        if timestamp is None and (now - self._last_added_time < (self._min_frame_interval * 0.85)):
            return False  # Throttle to target FPS

        jpeg_bytes: Optional[bytes] = None
        w, h = 0, 0

        try:
            if Image is not None and isinstance(frame, Image.Image):
                w, h = frame.size
                bio = io.BytesIO()
                frame.save(bio, format="JPEG", quality=75)
                jpeg_bytes = bio.getvalue()
            elif np is not None and isinstance(frame, np.ndarray):
                h, w = frame.shape[:2]
                if Image is not None:
                    img = Image.fromarray(frame[:, :, :3].astype(np.uint8))
                    bio = io.BytesIO()
                    img.save(bio, format="JPEG", quality=75)
                    jpeg_bytes = bio.getvalue()
            elif isinstance(frame, bytes) and width and height:
                w, h = width, height
                if Image is not None:
                    img = Image.frombytes("RGB", (w, h), frame)
                    bio = io.BytesIO()
                    img.save(bio, format="JPEG", quality=75)
                    jpeg_bytes = bio.getvalue()
                else:
                    jpeg_bytes = frame
        except Exception as e:
            logger.debug(f"Failed to encode frame for clip buffer: {e}")
            return False

        if not jpeg_bytes:
            return False

        clip_frame = ClipFrame(now, jpeg_bytes, w, h)
        cutoff = now - self.max_seconds

        with self._lock:
            self._frames.append(clip_frame)
            self._last_added_time = now
            # Prune old frames
            while self._frames and self._frames[0].timestamp < cutoff:
                self._frames.popleft()

        return True

    def get_frames_for_duration(self, seconds: int = DEFAULT_CLIP_SECONDS) -> List[ClipFrame]:
        """Extract frames covering the last `seconds`."""
        with self._lock:
            if not self._frames:
                return []
            latest_time = self._frames[-1].timestamp
            start_time = latest_time - max(1, seconds)
            return [f for f in self._frames if f.timestamp >= start_time]

    def frame_count(self) -> int:
        with self._lock:
            return len(self._frames)


# App-wide singleton replay buffer
REPLAY_BUFFER = ReplayBuffer()


def save_replay_clip(
    seconds: int = DEFAULT_CLIP_SECONDS,
    game_name: Optional[str] = None,
    buffer: Optional[ReplayBuffer] = None,
) -> Dict[str, Any]:
    """Render the rolling replay buffer to an MP4 video clip.

    Returns dict with keys:
    - success (bool)
    - path (str)
    - seconds (float)
    - frames (int)
    - game (str)
    - message (str)
    """
    buf = buffer or REPLAY_BUFFER
    game = game_name or buf._active_game or "Gameplay"
    safe_game = re_clean_name(game)
    frames = buf.get_frames_for_duration(seconds)

    if not frames:
        # If no frames in buffer, return a clear descriptive error
        return {
            "success": False,
            "path": "",
            "seconds": 0.0,
            "frames": 0,
            "game": game,
            "message": "No gameplay frames available in replay buffer to clip.",
        }

    actual_duration = frames[-1].timestamp - frames[0].timestamp if len(frames) > 1 else float(seconds)
    actual_duration = max(1.0, actual_duration)

    clips_dir = get_clips_directory()
    now_str = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    output_path = clips_dir / f"{safe_game}_{now_str}.mp4"

    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin:
        success = _encode_with_ffmpeg(frames, output_path, ffmpeg_bin, TARGET_FPS)
        if success and output_path.exists() and output_path.stat().st_size > 0:
            return {
                "success": True,
                "path": str(output_path),
                "seconds": round(actual_duration, 1),
                "frames": len(frames),
                "game": game,
                "message": f"Saved {round(actual_duration, 1)}s clip of {game} to {output_path.name}",
            }

    # Fallback if ffmpeg is missing or failed: save animated GIF
    gif_path = output_path.with_suffix(".gif")
    if Image is not None:
        try:
            pil_frames = []
            for f in frames[:: max(1, len(frames) // 40)]:  # Sample up to 40 frames for GIF
                pil_frames.append(Image.open(io.BytesIO(f.jpeg_bytes)))
            if pil_frames:
                pil_frames[0].save(
                    gif_path,
                    save_all=True,
                    append_images=pil_frames[1:],
                    duration=int(1000 / 12),
                    loop=0,
                )
                return {
                    "success": True,
                    "path": str(gif_path),
                    "seconds": round(actual_duration, 1),
                    "frames": len(pil_frames),
                    "game": game,
                    "message": f"Saved animated clip of {game} to {gif_path.name}",
                }
        except Exception as e:
            logger.error(f"Fallback GIF saving failed: {e}")

    return {
        "success": False,
        "path": "",
        "seconds": round(actual_duration, 1),
        "frames": len(frames),
        "game": game,
        "message": "Failed to encode video clip (ffmpeg not responding).",
    }


def _encode_with_ffmpeg(
    frames: List[ClipFrame], output_file: Path, ffmpeg_bin: str, fps: int = TARGET_FPS
) -> bool:
    """Pipe JPEG frames into ffmpeg stdin to produce standard H.264 MP4."""
    cmd = [
        ffmpeg_bin,
        "-y",
        "-f",
        "image2pipe",
        "-vcodec",
        "mjpeg",
        "-r",
        str(fps),
        "-i",
        "-",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        str(output_file),
    ]

    proc = None
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            for f in frames:
                proc.stdin.write(f.jpeg_bytes)
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        proc.wait(timeout=15)
        return proc.returncode == 0
    except subprocess.TimeoutExpired:
        if proc:
            try:
                proc.kill()
                proc.wait(timeout=2)
            except Exception:
                pass
        return False
    except Exception as e:
        logger.error(f"FFmpeg encoding error: {e}")
        if proc:
            try:
                proc.kill()
            except Exception:
                pass
        return False


def re_clean_name(name: str) -> str:
    """Clean filename component."""
    import re

    clean = re.sub(r"[^\w\s-]", "", name).strip()
    return re.sub(r"[-\s]+", "_", clean) or "clip"
