"""Code-driven poses for the companion, plus optional hand-drawn frames.

Each animation state maps to a lean (degrees; drawn as a shear above her bottom edge) and a vertical
squash (1.0 = normal). Drawn frames, when present, replace the base picture:
put PNGs in assets/frames/<state>/ (see the README there) and they play in
name order at FRAME_FPS, with the code motion still layered on top.
"""
import math
import os

STATES = ("idle", "talk", "sleep", "drag", "music", "land")
FRAME_FPS = 8


def pick_state(*, dragging: bool, landing: bool, sleeping: bool, talking: bool, mood: str) -> str:
    if dragging:
        return "drag"
    if landing:
        return "land"
    if sleeping:
        return "sleep"
    if talking:
        return "talk"
    if mood == "music":
        return "music"
    return "idle"


def pose_for(state: str, t: float, drag_dx: float = 0.0, land_age: float = 0.0) -> tuple[float, float]:
    """(tilt_degrees, squash) for `state` at time `t` seconds."""
    if state == "drag":
        # Flail: wobble, leaning against the direction she's being pulled.
        lean = max(-14.0, min(14.0, -drag_dx * 0.6))
        return lean + math.sin(t * 18.0) * 6.0, 1.0 + math.sin(t * 9.0) * 0.03
    if state == "land":
        # Squash on impact, then a damped springy recovery over ~0.6 s.
        return 0.0, 1.0 - 0.14 * math.exp(-land_age * 7.0) * math.cos(land_age * 22.0)
    if state == "sleep":
        return 7.0 + math.sin(t * 0.9) * 1.5, 0.985
    if state == "music":
        beat = t * math.pi * 2.0  # ~120 BPM sway
        return math.sin(beat / 2.0) * 6.0, 1.0 - abs(math.sin(beat)) * 0.025
    if state == "talk":
        return math.sin(t * 5.0) * 1.5, 1.0
    return 0.0, 1.0


_frame_cache: dict[str, list[str]] = {}


def frame_paths(frames_dir: str, state: str) -> list[str]:
    """Sorted PNG paths in frames_dir/state, cached. Empty if none were drawn."""
    key = os.path.join(frames_dir, state)
    if key not in _frame_cache:
        try:
            names = sorted(n for n in os.listdir(key) if n.lower().endswith(".png"))
        except OSError:
            names = []
        _frame_cache[key] = [os.path.join(key, n) for n in names]
    return _frame_cache[key]
