"""Real-time game visual event detection, game awareness, and controls memory.

Analyzes game screen frames for visual cues (flashes, red hurt borders, health bar drops,
scene cuts, blackout) and manages persistent game notes and control cheat-sheets.
"""

from __future__ import annotations

import logging
import math
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None

logger = logging.getLogger("actions.game_sense")

# Game genres and strategic wisdom
GAME_GENRES: List[Tuple[str, str, str]] = [
    (
        r"counter.?strike|\bcs ?2\b|\bcs ?go\b|valorant|rainbow six|\bsiege\b|tarkov|ready or not|insurgency|\bsquad\b|hunt.?showdown|gray zone",
        "Tactical shooter",
        "Position beats speed. Keep crosshair at head height where enemies appear. Stop moving before shooting because moving ruins spread. Clear one angle at a time from cover. Walk near enemies—running is loud.",
    ),
    (
        r"overwatch|marvel rivals|\bapex\b|fortnite|call of duty|warzone|battlefield|\bpubg\b|\bhalo\b|destiny|team fortress|titanfall|\bdoom\b|quake|the finals|splitgate|left 4 dead|borderlands|ultrakill|battlebit",
        "Fast shooter",
        "Keep moving, fight from cover or high ground, aim upper torso/head, and reload when safe. Play your character's role and back off when hurt to heal before re-engaging.",
    ),
    (
        r"minecraft|terraria|valheim|\brust\b|\bark\b|subnautica|the forest|sons of the forest|palworld|don.?t starve|7 days to die|no man.?s sky|satisfactory|factorio|grounded|\braft\b|project zomboid|core keeper|enshrouded|v rising",
        "Survival & Crafting",
        "The loop is gather -> craft better tools -> secure shelter -> explore further. Secure food and shelter before nightfall. Don't mine or fight on low health. Mark landmarks so you can navigate back.",
    ),
    (
        r"cyberpunk|skyrim|oblivion|fallout|witcher|grand theft|\bgta\b|red dead|assassin.?s creed|starfield|baldur|mass effect|dragon age|kingdom come|far cry|watch dogs|horizon|ghost of|\bmafia\b|outer worlds|avowed|hogwarts|arkham",
        "Open-World RPG",
        "Follow quest markers on the minimap, read objectives, and scout encounters from cover before diving in. Check healing consumables and ammo. Save frequently before risky missions.",
    ),
    (
        r"elden ring|dark souls|demon.?s souls|sekiro|bloodborne|hollow knight|monster hunter|god of war|devil may cry|lies of p|\bnioh\b|black myth|wukong|\bhades\b|cuphead|silksong|nier",
        "Action & Soulslike",
        "Every enemy attack has a windup tell. Watch before committing. Hit once or twice in recovery windows, then roll out. Conserve stamina for dodges and heal only when safe.",
    ),
    (
        r"league of legends|\blol\b|dota|smite|heroes of the storm|age of empires|starcraft|company of heroes|civilization|\bciv\b|total war|stellaris|crusader kings|hearts of iron|\btft\b",
        "Strategy & MOBA",
        "Decisions and map awareness matter more than fast clicks. Keep economy growing, maintain vision, track enemy cooldowns, and stay with waves/minions.",
    ),
    (
        r"bloons|tower defen[cs]e|kingdom rush|plants vs",
        "Tower Defense",
        "Place towers where their range covers bends and multiple track sections. Prioritize early eco/money generators, and upgrade existing towers before spamming weak starters.",
    ),
    (
        r"forza|need for speed|gran turismo|assetto|\bf1\b|mario kart|rocket league|trackmania|beamng|\bfifa\b|ea (sports )?fc|nba 2k|madden",
        "Racing & Sports",
        "Look ahead down the track, not at your bumper. Brake in a straight line before entering a corner, then accelerate cleanly out. Steer in smooth taps.",
    ),
    (
        r"super mario|celeste|ori and|rayman|\bsonic\b|\bcrash\b|fall guys|only up|getting over it|geometry dash",
        "Platformer",
        "Jumps are about rhythm and edge precision. Stop and line up trajectory. Small controlled inputs beat erratic holding near fatal drops.",
    ),
    (
        r"phasmophobia|lethal company|dead by daylight|outlast|amnesia|resident evil|silent hill|dead space|alien.?isolation|content warning|among us",
        "Horror & Co-op Survival",
        "Audio cues reveal danger before sight. Keep an escape route clear, conserve light sources/batteries, and retreat rather than confronting unknown entities.",
    ),
    (
        r"balatro|slay the spire|hearthstone|stardew|the sims|cities.? skylines|planet coaster|animal crossing|portal|the witness|tetris|chess|papers.? please|disco elysium",
        "Turn-based & Puzzle",
        "Nothing is rushing you. Read card/item tooltips thoroughly, calculate synergies 2-3 turns ahead, and focus on consistency over high-variance gambles.",
    ),
    (
        r"roblox",
        "Roblox",
        "Every Roblox experience has unique rules. Check on-screen UI prompts (usually E to interact, number keys for inventory, / for chat). Esc then R then Enter resets character if stuck.",
    ),
]

# Builtin control defaults for popular games
BUILTIN_CONTROLS: Dict[str, Dict[str, str]] = {
    "cyberpunk 2077": {
        "Move": "W/A/S/D",
        "Jump": "Space",
        "Crouch": "C",
        "Dodge / Dash": "Double-tap direction or Left Ctrl",
        "Sprint": "Left Shift",
        "Interact": "F",
        "Scanner": "Hold Tab",
        "Draw / Holster": "Alt (double-tap to holster)",
        "Use Heal / Inhaler": "X",
        "Phone": "T",
        "Call Vehicle": "V",
        "Auto Drive": "Hold V for 0.7s in car",
        "Handbrake": "Space",
        "Map": "M",
        "Inventory": "I",
        "Journal": "J",
        "Quick Save": "F5",
    },
    "minecraft": {
        "Move": "W/A/S/D",
        "Jump / Swim Up": "Space",
        "Sneak": "Left Shift",
        "Sprint": "Left Ctrl (or double-tap W)",
        "Attack / Destroy": "Left Mouse",
        "Use / Place": "Right Mouse",
        "Pick Block": "Middle Mouse",
        "Drop Item": "Q",
        "Inventory": "E",
        "Swap Hand": "F",
        "Chat": "T",
        "Command": "/",
        "Coordinates / Debug": "F3",
        "Fullscreen": "F11",
    },
    "counter-strike 2": {
        "Move": "W/A/S/D",
        "Walk (Silent)": "Left Shift",
        "Crouch": "Left Ctrl",
        "Jump": "Space or MouseWheel",
        "Fire": "Left Mouse",
        "Secondary Fire": "Right Mouse",
        "Reload": "R",
        "Drop Weapon": "G",
        "Buy Menu": "B",
        "Bomb / Defuse": "E",
        "Scoreboard": "Tab",
        "Voice Chat": "K (or custom push-to-talk)",
        "Ping Location": "Middle Mouse",
    },
    "valorant": {
        "Move": "W/A/S/D",
        "Walk": "Left Shift",
        "Crouch": "Left Ctrl",
        "Jump": "Space",
        "Ability 1 / 2 / 3": "C / Q / E",
        "Ultimate": "X",
        "Plant / Defuse Spike": "4 (hold)",
        "Drop": "G",
        "Buy Menu": "B",
        "Inspect Weapon": "Y",
    },
}


def _get_games_data_dir() -> Path:
    """Return persistent directory for game controls and notes."""
    app_data = os.environ.get("APPDATA")
    if app_data and os.path.isdir(app_data):
        target = Path(app_data) / "Alyssa" / "games"
    else:
        target = Path.home() / ".alyssa" / "games"
    target.mkdir(parents=True, exist_ok=True)
    return target


def slugify_game_name(name: str) -> str:
    """Sanitize game title into a clean filename slug."""
    clean = re.sub(r"[^\w\s-]", "", (name or "").lower()).strip()
    return re.sub(r"[-\s]+", "-", clean) or "game"


def detect_game_from_title_or_process(text: str) -> Optional[Tuple[str, str, str]]:
    """Match a window title or process name to a known game genre and tips."""
    if not text:
        return None
    for pattern, genre, tips in GAME_GENRES:
        if re.search(pattern, text, re.IGNORECASE):
            return text.strip(), genre, tips
    return None


def detect_active_game() -> Optional[Dict[str, Any]]:
    """Scan running processes and find any known game."""
    if psutil is None:
        return None
    try:
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                name = proc.info.get("name") or ""
                detected = detect_game_from_title_or_process(name)
                if detected:
                    matched_title, genre, tips = detected
                    return {
                        "name": matched_title,
                        "genre": genre,
                        "tips": tips,
                        "pid": proc.info.get("pid"),
                    }
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception as e:
        logger.debug(f"Error scanning processes for games: {e}")
    return None


def get_game_controls(game_name: str) -> Dict[str, str]:
    """Retrieve saved or builtin controls for a game."""
    slug = slugify_game_name(game_name)
    file_path = _get_games_data_dir() / f"{slug}.controls.md"

    # Check for custom saved controls
    if file_path.is_file():
        try:
            content = file_path.read_text(encoding="utf-8")
            controls: Dict[str, str] = {}
            for line in content.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    k = k.lstrip("- *#").strip()
                    controls[k] = v.strip()
            if controls:
                return controls
        except Exception as e:
            logger.warning(f"Failed to read custom controls from {file_path}: {e}")

    # Fall back to builtin controls
    for known_game, bindings in BUILTIN_CONTROLS.items():
        if known_game in game_name.lower():
            return dict(bindings)

    return {}


def save_game_controls(game_name: str, controls_text: str) -> str:
    """Save user-taught or custom controls to markdown."""
    slug = slugify_game_name(game_name)
    file_path = _get_games_data_dir() / f"{slug}.controls.md"
    header = f"# Controls for {game_name}\n\n"
    content = header + controls_text.strip() + "\n"
    file_path.write_text(content, encoding="utf-8")
    return str(file_path)


def get_game_notes(game_name: str) -> str:
    """Retrieve persistent learning notes for a game."""
    slug = slugify_game_name(game_name)
    file_path = _get_games_data_dir() / f"{slug}.md"
    if file_path.is_file():
        try:
            return file_path.read_text(encoding="utf-8").strip()
        except Exception:
            return ""
    return ""


def append_game_note(game_name: str, note_line: str) -> str:
    """Append a learned insight to the game's persistent notes."""
    slug = slugify_game_name(game_name)
    file_path = _get_games_data_dir() / f"{slug}.md"
    if not file_path.exists():
        file_path.write_text(f"# Notes: {game_name}\n\n", encoding="utf-8")
    with file_path.open("a", encoding="utf-8") as f:
        f.write(f"- {note_line.strip()}\n")
    return str(file_path)


class GameSense:
    """Real-time visual game sense analyzer.

    Detects:
    - 'flash': Flashbang / sudden brightness spike (>78% bright, delta > 55).
    - 'hurt': Red vignette/border around screen edges from taking damage.
    - 'bar_drop': Sharp drop in tracked status/health bars.
    - 'cut': Scene cut or cinematic transition.
    - 'dark': Screen fade to black / deep darkness.
    """

    KINDS = ("flash", "hurt", "bar_drop", "cut", "dark")

    def __init__(self):
        self._lock = threading.Lock()
        self._subscribers: List[Callable[[str, float, float], None]] = []
        self._last_event_time = {k: -999.0 for k in self.KINDS}
        self._baseline_red = [0.0] * 5  # [left, right, top, bottom, center]
        self._has_baseline = False
        self._prev_frame: Optional[np.ndarray] = None
        self._prev_mean: float = 0.0
        self._flash_active = False
        self._flash_start = 0.0
        self._flash_mean = 0.0
        self._cooldown_seconds = 2.5

    def subscribe(self, callback: Callable[[str, float, float], None]) -> None:
        """Register a callback: callback(kind: str, strength: float, timestamp: float)."""
        with self._lock:
            if callback not in self._subscribers:
                self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[str, float, float], None]) -> None:
        with self._lock:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

    def _emit(self, kind: str, strength: float, timestamp: float) -> None:
        if timestamp - self._last_event_time[kind] < self._cooldown_seconds:
            return
        self._last_event_time[kind] = timestamp
        clamped_strength = max(0.0, min(1.0, float(strength)))
        with self._lock:
            subs = list(self._subscribers)
        for sub in subs:
            try:
                sub(kind, clamped_strength, timestamp)
            except Exception as e:
                logger.debug(f"GameSense subscriber error: {e}")

    def reset(self) -> None:
        """Reset internal frame tracker and baselines."""
        with self._lock:
            self._prev_frame = None
            self._prev_mean = 0.0
            self._flash_active = False
            self._has_baseline = False
            for k in self.KINDS:
                self._last_event_time[k] = -999.0

    def feed_frame(
        self,
        frame: Union[np.ndarray, Image.Image, bytes],
        width: Optional[int] = None,
        height: Optional[int] = None,
        timestamp: Optional[float] = None,
    ) -> List[Tuple[str, float]]:
        """Process one game frame and fire events.

        Returns list of (kind, strength) detected in this frame.
        """
        now = time.time() if timestamp is None else float(timestamp)
        detected_events: List[Tuple[str, float]] = []

        # Convert to numpy array of shape (H, W, 3) RGB/BGR
        arr: Optional[np.ndarray] = None
        if isinstance(frame, np.ndarray):
            arr = frame
        elif Image is not None and isinstance(frame, Image.Image):
            arr = np.array(frame.convert("RGB"))
        elif isinstance(frame, bytes) and width and height:
            if np is not None:
                # Expecting RGB or RGBA/BGRA bytes
                bpp = len(frame) // (width * height)
                arr = np.frombuffer(frame, dtype=np.uint8).reshape((height, width, bpp))
                if bpp == 4:
                    arr = arr[:, :, :3]

        if arr is None or arr.size == 0 or len(arr.shape) < 3:
            return detected_events

        h, w, c = arr.shape
        if c < 3:
            return detected_events

        # Downsample to lightweight 64x36 grid for ultra-fast calculation
        step_y = max(1, h // 36)
        step_x = max(1, w // 64)
        sampled = arr[::step_y, ::step_x, :3].astype(np.float32)
        sh, sw, _ = sampled.shape

        # Color channels (assuming RGB or BGR - excess red works similarly)
        r = sampled[:, :, 0]
        g = sampled[:, :, 1]
        b = sampled[:, :, 2]
        gray = 0.299 * r + 0.587 * g + 0.114 * b
        mean_gray = float(np.mean(gray))
        black_ratio = float(np.mean(gray < 18.0))
        bright_ratio = float(np.mean(gray > 220.0))

        # Edge excess red (R - max(G, B))
        excess_red = np.maximum(0.0, r - np.maximum(g, b))
        edge_w = max(1, sw // 8)
        edge_h = max(1, sh // 8)

        left_red = float(np.mean(excess_red[:, :edge_w])) if sw > 0 else 0.0
        right_red = float(np.mean(excess_red[:, -edge_w:])) if sw > 0 else 0.0
        top_red = float(np.mean(excess_red[:edge_h, :])) if sh > 0 else 0.0
        bottom_red = float(np.mean(excess_red[-edge_h:, :])) if sh > 0 else 0.0
        center_slice = excess_red[edge_h:-edge_h, edge_w:-edge_w] if (sh > 2 * edge_h and sw > 2 * edge_w) else excess_red
        center_red = float(np.mean(center_slice)) if center_slice.size > 0 else 0.0
        current_reds = [left_red, right_red, top_red, bottom_red, center_red]

        if not self._has_baseline:
            self._baseline_red = list(current_reds)
            self._has_baseline = True
            self._prev_frame = sampled
            self._prev_mean = mean_gray
            return detected_events

        # 1. Darkness detection (nearly black screen: black > 90%, mean < 15)
        if black_ratio > 0.90 and mean_gray < 15.0:
            dark_strength = 1.0 - (mean_gray / 15.0)
            self._emit("dark", dark_strength, now)
            detected_events.append(("dark", dark_strength))

        # 2. Flashbang detection (sudden spike: delta > 45, bright ratio > 0.60)
        mean_delta = mean_gray - self._prev_mean
        if bright_ratio > 0.60 and mean_delta > 45.0 and not self._flash_active:
            self._flash_active = True
            self._flash_start = now
            self._flash_mean = self._prev_mean
            flash_strength = min(1.0, mean_delta / 120.0)
            self._emit("flash", flash_strength, now)
            detected_events.append(("flash", flash_strength))
        elif self._flash_active and (now - self._flash_start > 1.5 or abs(mean_gray - self._flash_mean) < 25.0):
            self._flash_active = False

        # 3. Hurt / Damage Border (elevated red on at least 3 outer edges compared to center & baseline)
        raised_edges = 0
        redness_sum = 0.0
        for i in range(4):
            elevated = current_reds[i] - self._baseline_red[i]
            if elevated > 18.0 and current_reds[i] > (center_red + 12.0):
                raised_edges += 1
                redness_sum += elevated

        if raised_edges >= 3:
            hurt_strength = min(1.0, redness_sum / 200.0)
            self._emit("hurt", hurt_strength, now)
            detected_events.append(("hurt", hurt_strength))
        else:
            # Slowly adjust baseline red when not hurt
            for i in range(5):
                self._baseline_red[i] += (current_reds[i] - self._baseline_red[i]) * 0.1

        # 4. Scene Cut (large spatial difference across grid)
        if self._prev_frame is not None and self._prev_frame.shape == sampled.shape:
            diff = np.abs(sampled - self._prev_frame)
            mean_diff = float(np.mean(diff))
            if mean_diff > 48.0 and not self._flash_active and black_ratio < 0.80:
                cut_strength = min(1.0, mean_diff / 100.0)
                self._emit("cut", cut_strength, now)
                detected_events.append(("cut", cut_strength))

        self._prev_frame = sampled
        self._prev_mean = mean_gray
        return detected_events


# Global singleton instance for app-wide game sensing
GAME_SENSE = GameSense()
