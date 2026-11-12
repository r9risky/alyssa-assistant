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

# Genre -> games we recognise by window title or process name, plus a short coaching note.
# Short names that are also ordinary words get whole-word matching (see _title_pattern).
_GENRE_GAMES: List[Tuple[str, Tuple[str, ...], str]] = [
    (
        "Tactical shooter",
        ("arma", "counter-strike", "cs2", "csgo", "escape from tarkov", "hunt showdown",
         "hunt: showdown", "insurgency", "rainbow six", "ready or not", "siege", "squad",
         "tarkov", "valorant"),
        "Rounds here are won by patience. Pre-aim the spots people peek from, stand still for "
        "the shot, and tap rather than spray. Take fights one doorway at a time and use "
        "footstep noise against them.",
    ),
    (
        "Arena & battle royale shooter",
        ("apex", "apex legends", "battlefield", "borderlands", "call of duty", "deadlock",
         "destiny", "doom", "fortnite", "halo", "left 4 dead", "marvel rivals", "overwatch",
         "pubg", "quake", "splitgate", "team fortress", "the finals", "titanfall", "ultrakill",
         "warzone"),
        "Movement is your armour: strafe, use height, and never stand in the open. "
        "Retreat to recover before you're one shot from dying, and play to your hero's job.",
    ),
    (
        "Survival & Crafting",
        ("7 days to die", "ark", "core keeper", "don't starve", "enshrouded", "factorio",
         "grounded", "minecraft", "no man's sky", "palworld", "project zomboid", "raft", "rust",
         "satisfactory", "sons of the forest", "subnautica", "terraria", "the forest",
         "v rising", "valheim"),
        "Early on, everything serves the next upgrade: basic resources, then better tools, then "
        "a safe base. Sort food and a bed before dark, avoid risky digging while hurt, and leave "
        "markers so you can find home.",
    ),
    (
        "Open-World RPG",
        ("arkham", "assassin's creed", "avowed", "baldur's gate", "cyberpunk", "dragon age",
         "fallout", "far cry", "ghost of tsushima", "grand theft auto", "gta",
         "hogwarts legacy", "horizon", "kingdom come", "mafia", "mass effect", "oblivion",
         "outer worlds", "red dead", "skyrim", "starfield", "watch dogs", "witcher"),
        "Lean on the quest tracker and minimap, and scope out a fight from a distance before "
        "committing. Keep healing items stocked and save before anything that looks dangerous.",
    ),
    (
        "Action & Soulslike",
        ("black myth", "bloodborne", "cuphead", "dark souls", "demon's souls", "devil may cry",
         "elden ring", "god of war", "hades", "hollow knight", "lies of p", "monster hunter",
         "nier", "nioh", "sekiro", "silksong", "wukong"),
        "Learn the boss before trying to beat it: each attack has a wind-up you can read. "
        "Punish in the gaps with a hit or two, keep stamina for dodging, and heal at a distance.",
    ),
    (
        "Strategy & MOBA",
        ("age of empires", "civ", "civilization", "company of heroes", "crusader kings", "dota",
         "hearts of iron", "heroes of the storm", "league of legends", "lol", "smite",
         "starcraft", "stellaris", "teamfight tactics", "tft", "total war"),
        "Good calls beat fast hands. Keep your economy climbing, know where the enemy is, "
        "track their big cooldowns, and don't wander away from your lane or army.",
    ),
    (
        "Tower Defense",
        ("bloons", "kingdom rush", "plants vs", "tower defence", "tower defense"),
        "Build at corners and overlaps where one tower covers the path twice. Get income going "
        "early and upgrade what you have before adding more cheap towers.",
    ),
    (
        "Racing & Sports",
        ("assetto corsa", "beamng", "ea sports fc", "f1", "fifa", "forza", "gran turismo",
         "madden", "mario kart", "nba 2k", "need for speed", "rocket league", "trackmania"),
        "Eyes on the next corner, not the car in front. Do your braking before the turn while "
        "going straight, then power out, and keep steering inputs small.",
    ),
    (
        "Platformer",
        ("celeste", "crash bandicoot", "fall guys", "geometry dash", "getting over it",
         "only up", "ori and", "rayman", "sonic", "super mario"),
        "Take a breath before tricky jumps and find the rhythm. Short, deliberate taps land more "
        "ledges than holding a direction near a drop.",
    ),
    (
        "Horror & Co-op Survival",
        ("alien isolation", "alien: isolation", "amnesia", "among us", "content warning",
         "dead by daylight", "dead space", "lethal company", "outlast", "phasmophobia", "repo",
         "resident evil", "silent hill"),
        "Listen first: sounds give threats away before you see them. Know your way out, ration "
        "batteries and light, and don't go poking at things you can't fight.",
    ),
    (
        "Turn-based & Puzzle",
        ("animal crossing", "balatro", "chess", "cities skylines", "cities: skylines",
         "disco elysium", "hearthstone", "papers please", "papers, please", "planet coaster",
         "portal", "slay the spire", "stardew", "tetris", "the sims", "the witness"),
        "There's no clock, so use it. Read every tooltip, plan a couple of turns out, and prefer "
        "steady combos over risky long shots.",
    ),
    (
        "Roblox",
        ("roblox",),
        "Each Roblox game has its own rules, so read the on-screen hints. E usually interacts, "
        "number keys pick items and / opens chat. If you're stuck, Esc, R, Enter respawns you.",
    ),
]


def _title_pattern(names: Tuple[str, ...]) -> str:
    """Alternation of names; punctuation inside a name may be a space or nothing.

    Names of 4 letters or fewer ("ark", "rust", "lol") must stand alone so they don't fire
    inside other words; longer ones match anywhere, e.g. "FortniteClient-Win64-Shipping.exe".
    """
    parts = []
    for name in sorted(names, key=len, reverse=True):
        tokens = [t for t in re.split(r"[^a-z0-9]+", name.lower()) if t]
        body = r"[\W_]*".join(re.escape(t) for t in tokens)
        parts.append(rf"(?<![a-z]){body}(?![a-z])" if len("".join(tokens)) <= 4 else body)
    return "|".join(parts)


GAME_GENRES: List[Tuple[str, str, str]] = [
    (_title_pattern(names), genre, tips) for genre, names, tips in _GENRE_GAMES
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
