"""Persistent JSON memory storage and semantic retrieval for Alyssa."""

import json
import os
import sys
import threading
from functools import lru_cache

import numpy as np

from config import MEMORY_SETTINGS as config

if getattr(sys, "frozen", False):
    _BASE_DIR = os.path.dirname(sys.executable)
else:
    _BASE_DIR = os.path.dirname(__file__)

MEMORY_FILE = os.path.join(_BASE_DIR, "memory.json")

_lock = threading.RLock()
_MEMORIES_CACHE = None
_MEMORIES_CACHE_MTIME = None


@lru_cache(maxsize=1)
def _embedding_model():
    from fastembed import TextEmbedding

    return TextEmbedding(model_name="BAAI/bge-small-en-v1.5")


def _clean_fact(fact: str) -> str:
    """Normalizes whitespace and clamps fact length."""
    limit = max(1, int(getattr(config, "MAX_MEMORY_FACT_CHARACTERS", 400)))
    return " ".join(fact.split())[:limit]


def _compact(memories: list) -> list:
    """Deduplicates and bounds memory count to MAX_SAVED_MEMORIES."""
    maximum = max(1, int(getattr(config, "MAX_SAVED_MEMORIES", 75)))
    unique = []
    seen = set()
    for fact in memories:
        if not isinstance(fact, str):
            continue
        fact = _clean_fact(fact)
        key = fact.casefold()
        if fact and key not in seen:
            unique.append(fact)
            seen.add(key)
    return unique[-maximum:]


def _read_file() -> list:
    if not os.path.exists(MEMORY_FILE):
        return []
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"(couldn't read memory.json, starting fresh: {e})")
        if isinstance(e, json.JSONDecodeError):
            try:  # keep the damaged file: the next save would overwrite it for good
                os.replace(MEMORY_FILE, MEMORY_FILE + ".corrupt")
                print(f"(saved the damaged copy as memory.json.corrupt)")
            except OSError:
                pass
        return []
    if isinstance(data, list):
        return [f for f in data if isinstance(f, str) and f.strip()]
    return []


def _write_file(memories: list):
    tmp_path = MEMORY_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(memories, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, MEMORY_FILE)


def load_memories() -> list:
    """Returns saved facts in chronological order."""
    global _MEMORIES_CACHE, _MEMORIES_CACHE_MTIME
    with _lock:
        if _MEMORIES_CACHE is not None:
            if os.path.exists(MEMORY_FILE):
                current_mtime = os.path.getmtime(MEMORY_FILE)
                if current_mtime == _MEMORIES_CACHE_MTIME:
                    return list(_MEMORIES_CACHE)
            else:
                _MEMORIES_CACHE = None
                _MEMORIES_CACHE_MTIME = None

        memories = _read_file()
        compacted = _compact(memories)
        if compacted != memories:
            _write_file(compacted)

        _MEMORIES_CACHE = compacted
        _MEMORIES_CACHE_MTIME = os.path.getmtime(MEMORY_FILE) if os.path.exists(MEMORY_FILE) else None
        return list(compacted)


def save_memories(memories: list):
    """Atomically replaces memories in memory.json."""
    global _MEMORIES_CACHE, _MEMORIES_CACHE_MTIME
    memories = _compact(memories)
    with _lock:
        if _MEMORIES_CACHE == memories and os.path.exists(MEMORY_FILE):
            return
        try:
            _write_file(memories)
        except OSError as e:
            raise RuntimeError(f"couldn't write to memory.json: {e}") from e
        _MEMORIES_CACHE = memories
        _MEMORIES_CACHE_MTIME = os.path.getmtime(MEMORY_FILE) if os.path.exists(MEMORY_FILE) else None


_FACT_VECTORS = {}  # fact -> embedding; facts rarely change, so each is embedded once, not every turn


def _fact_vectors(model, memories: list) -> list:
    with _lock:
        missing = [m for m in dict.fromkeys(memories) if m not in _FACT_VECTORS]
        if missing:
            _FACT_VECTORS.update(zip(missing, model.passage_embed(missing)))
        for gone in set(_FACT_VECTORS) - set(memories):  # forgotten facts
            del _FACT_VECTORS[gone]
        return [_FACT_VECTORS[m] for m in memories]


def relevant_memories(query: str, limit: int = 20) -> list:
    """Returns top memories by local embedding similarity."""
    if limit <= 0:
        return []
    memories = load_memories()
    if not memories:
        return []

    selected_indices = []
    query = (query or "").strip()
    if query:
        model = _embedding_model()
        query_vector = next(model.query_embed(query))
        scores = np.dot(_fact_vectors(model, memories), query_vector)
        ranked = sorted(
            enumerate(scores),
            key=lambda item: (item[1], item[0]),
            reverse=True,
        )
        selected_indices = [i for i, _score in ranked[:limit]]

    recent_budget = min(5, limit)
    selected_set = set(selected_indices)
    for i in range(len(memories) - 1, -1, -1):
        if len(selected_indices) >= limit or recent_budget <= 0:
            break
        if i not in selected_set:
            selected_indices.append(i)
            selected_set.add(i)
            recent_budget -= 1

    selected_indices = selected_indices[:limit]
    selected_indices.sort()
    return [memories[i] for i in selected_indices]


def remember(fact: str) -> str:
    """Saves a new fact to memory."""
    memories = load_memories()
    fact = _clean_fact(fact)
    if not fact:
        return "I couldn't save an empty memory."
    if fact.casefold() not in {m.casefold() for m in memories}:
        memories.append(fact)
        save_memories(memories)
    return f"Got it, I'll remember that: {fact}"


def forget(fact_snippet: str) -> str:
    """Removes facts matching fact_snippet from memory."""
    memories = load_memories()
    matched = [m for m in memories if fact_snippet.lower() in m.lower()]
    if not matched:
        return f"I couldn't find a memory matching '{fact_snippet}'."
    memories = [m for m in memories if m not in matched]
    save_memories(memories)
    return f"Forgot: {', '.join(matched)}"


# ---------------------------------------------------------------------------
# Capri-inspired Markdown Notes (personality.md, me.md, memories.md, check-ins.md),
# Daily Chat Transcripts (transcripts/YYYY-MM-DD.md), and 3h Visual Screen Buffer
# ---------------------------------------------------------------------------

import datetime
import hashlib
import random
import re
import time

DEFAULT_PERSONALITY_MARKDOWN = """# Alyssa's Personality

- Warm, sharp, witty, and genuinely helpful desktop companion.
- Speaks naturally in 1 to 2 concise sentences unless asked for detail.
- Playful and observant without being overbearing.
"""

DEFAULT_ME_MARKDOWN = """# About You

## Name & Pronouns

## Interests & Projects

## Setup & Preferences
"""

DEFAULT_CHECK_INS_MARKDOWN = """# Alyssa's check-ins

These are the little nudges Alyssa gives you. Change any line under a heading, add your own,
or delete every line under a heading to turn that nudge off.

## Water
- Quick drink of water? I'll hold your place.
- Hydration check. Go on, I'll wait.

## Stretch
- You've been sitting a while. Stand up and stretch for a sec.
- Roll your shoulders back. There, better.

## Look away
- Rest your eyes for 20 seconds: look at something across the room.
- Screen break for your eyes. Find something far away and stare at it.

## Sleep nudge
- It's pretty late. Tomorrow-you would appreciate some sleep.

## Meals
- Have you eaten? Go get some food.
- Snack time. Brains need fuel.

## Game break
- Good time for a short break when you hit a safe spot.
- Long gaming run. Give your hands and wrists a rest soon.

## Bored
- I'm bored. Entertain me.
- Nothing's happening. I've started naming your folders.
- Just me here, twiddling my thumbs.

## Bored noticed
- Oh, hey! You're back.
- Hi! Missed you.

## Bored ignored
- Okay, I'll keep myself busy.
- Fine, I'll just hum to myself.
"""

# The previous built-in check-ins; a file still holding exactly those gets the new defaults.
_OLD_DEFAULT_CHECK_INS_SHA256 = "c306dce694ff25202b97de3890f5f917d593aaf90ff5c8ad3c5c93bf338c7695"

_DISK_TRANSCRIPTS_ENABLED = False
_TRANSCRIPT_BUFFER: list[dict] = []
_SCREEN_MEMORY_BUFFER: list[dict] = []
_LAST_CHECK_IN_PICK: dict[str, str] = {}


def _resolve_note_path(filename_attr: str, default_name: str) -> str:
    raw = str(getattr(config, filename_attr, default_name) or default_name).strip()
    if os.path.isabs(raw):
        return raw
    return os.path.join(_BASE_DIR, raw)


def get_personality_path() -> str:
    return _resolve_note_path("PERSONALITY_FILE", "personality.md")


def get_me_notes_path() -> str:
    return _resolve_note_path("ME_NOTES_FILE", "me.md")


def get_memories_notes_path() -> str:
    return _resolve_note_path("MEMORIES_NOTES_FILE", "memories.md")


def get_check_ins_path() -> str:
    return _resolve_note_path("CHECK_INS_FILE", "check-ins.md")


def get_transcripts_dir() -> str:
    raw = str(getattr(config, "TRANSCRIPTS_DIR", "transcripts") or "transcripts").strip()
    if os.path.isabs(raw):
        return raw
    return os.path.join(_BASE_DIR, raw)


def enable_disk_transcripts(enabled: bool = True) -> None:
    """Enable or disable automatic disk writing for daily chat transcripts."""
    global _DISK_TRANSCRIPTS_ENABLED
    _DISK_TRANSCRIPTS_ENABLED = bool(enabled)


def load_personality_notes() -> str:
    """Return custom personality markdown from personality.md, or default if absent."""
    path = get_personality_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    return content
        except OSError:
            pass
    return DEFAULT_PERSONALITY_MARKDOWN.strip()


def save_personality_notes(text: str) -> str:
    """Save custom personality markdown to personality.md."""
    path = get_personality_path()
    content = (text or "").strip() or DEFAULT_PERSONALITY_MARKDOWN.strip()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content + "\n")
    return content


def reset_personality_notes() -> str:
    """Reset personality.md back to the default personality."""
    path = get_personality_path()
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass
    return DEFAULT_PERSONALITY_MARKDOWN.strip()


def load_me_notes() -> str:
    """Return user profile notes from me.md, or default template if absent."""
    path = get_me_notes_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    return content
        except OSError:
            pass
    return DEFAULT_ME_MARKDOWN.strip()


def save_me_notes(text: str) -> str:
    """Save user profile notes to me.md."""
    path = get_me_notes_path()
    content = (text or "").strip() or DEFAULT_ME_MARKDOWN.strip()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content + "\n")
    return content


def update_user_profile_note(note: str, section: str = "Interests & Projects") -> str:
    """Append or update a bullet note in me.md under the requested section."""
    cleaned = _clean_fact(note or "")
    if not cleaned:
        return "Tell me what you'd like me to add to your profile notes."
    current = load_me_notes()
    bullet = f"- {cleaned}"
    if cleaned.casefold() in current.casefold():
        return f"That's already in your profile notes: {cleaned}"
    header = f"## {section.strip()}"
    lines = current.splitlines()
    inserted = False
    new_lines = []
    for line in lines:
        new_lines.append(line)
        if not inserted and line.strip().casefold() == header.casefold():
            new_lines.append(bullet)
            inserted = True
    if not inserted:
        new_lines.extend(["", header, bullet])
    save_me_notes("\n".join(new_lines))
    return f"Updated your profile notes ({section}): {cleaned}"


def load_memories_markdown() -> str:
    """Return memories formatted as markdown bullets (reading memories.md or memory.json)."""
    path = get_memories_notes_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read().strip()
                if text:
                    return text
        except OSError:
            pass
    facts = load_memories()
    if not facts:
        return "# Saved Memories\n\n- (No memories saved yet.)"
    bullets = "\n".join(f"- {fact}" for fact in facts)
    return f"# Saved Memories\n\n{bullets}"


def save_memories_markdown(markdown_text: str) -> list[str]:
    """Parse bullet lines from markdown, save to both memory.json and memories.md."""
    facts = []
    for line in (markdown_text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith(("- ", "* ")):
            item = stripped[2:].strip()
            if item and item != "(No memories saved yet.)":
                facts.append(item)
    save_memories(facts)
    path = get_memories_notes_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write((markdown_text or "").strip() + "\n")
    return load_memories()


def load_check_in_lines() -> str:
    """Return check-in lines markdown from check-ins.md or defaults."""
    path = get_check_ins_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content and hashlib.sha256(content.encode("utf-8")).hexdigest() != _OLD_DEFAULT_CHECK_INS_SHA256:
                    return content
        except OSError:
            pass
    return DEFAULT_CHECK_INS_MARKDOWN.strip()


def save_check_in_lines(text: str) -> str:
    """Save custom check-in lines markdown to check-ins.md."""
    path = get_check_ins_path()
    content = (text or "").strip() or DEFAULT_CHECK_INS_MARKDOWN.strip()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content + "\n")
    return content


def pick_check_in_line(category: str = "Water") -> str | None:
    """Pick a non-repeating bullet line under `## <category>` from check-ins.md."""
    text = load_check_in_lines()
    choices: list[str] = []
    in_section = False
    target = f"## {category.strip()}".casefold()
    for raw_line in text.splitlines():
        row = raw_line.strip()
        if row.startswith("#"):
            in_section = row.casefold() == target
        elif in_section and row.startswith("- ") and row[2:].strip():
            item = row[2:].strip()
            if item not in choices:
                choices.append(item)
    if not choices:
        return None
    previous = _LAST_CHECK_IN_PICK.get(category)
    pool = [c for c in choices if c != previous] if len(choices) > 1 and previous in choices else choices
    picked = random.choice(pool)
    _LAST_CHECK_IN_PICK[category] = picked
    return picked


def append_transcript_turn(
    user_text: str,
    assistant_text: str,
    *,
    timestamp: datetime.datetime | None = None,
    persist: bool | None = None,
) -> None:
    """Record a conversation turn in memory and optionally to transcripts/YYYY-MM-DD.md."""
    if not getattr(config, "REMEMBER_CHATS_ENABLED", True):
        return
    u = " ".join((user_text or "").split())
    a = " ".join((assistant_text or "").split())
    if not u and not a:
        return
    now = timestamp or datetime.datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")
    entry = {
        "date": date_str,
        "time": time_str,
        "user": u,
        "assistant": a,
    }
    with _lock:
        _TRANSCRIPT_BUFFER.append(entry)
        if len(_TRANSCRIPT_BUFFER) > 500:
            del _TRANSCRIPT_BUFFER[:-500]
        should_persist = _DISK_TRANSCRIPTS_ENABLED if persist is None else bool(persist)
        if should_persist:
            try:
                t_dir = get_transcripts_dir()
                os.makedirs(t_dir, exist_ok=True)
                file_path = os.path.join(t_dir, f"{date_str}.md")
                need_header = not os.path.exists(file_path)
                with open(file_path, "a", encoding="utf-8") as f:
                    if need_header:
                        f.write(f"# Chat Transcript — {date_str}\n\n")
                    f.write(f"- **[{time_str}] You:** {u}\n  **Alyssa:** {a}\n")
            except OSError:
                pass


def search_transcripts(query: str = "", date_hint: str = "", limit: int = 8) -> list[str]:
    """Search daily chat transcripts (in-memory + transcripts/*.md) by keyword or date."""
    q = (query or "").strip().lower()
    hint = (date_hint or "").strip().lower()
    target_date = None
    today = datetime.date.today()
    if hint in ("today",):
        target_date = today.strftime("%Y-%m-%d")
    elif hint in ("yesterday",):
        target_date = (today - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    elif re.match(r"^\d{4}-\d{2}-\d{2}$", hint):
        target_date = hint

    results: list[str] = []
    seen: set[str] = set()

    with _lock:
        for item in reversed(_TRANSCRIPT_BUFFER):
            if target_date and item["date"] != target_date:
                continue
            combined = f"[{item['date']} {item['time']}] You: {item['user']} | Alyssa: {item['assistant']}"
            if not q or q in combined.lower():
                if combined not in seen:
                    results.append(combined)
                    seen.add(combined)
                    if len(results) >= limit:
                        return results

    t_dir = get_transcripts_dir()
    if os.path.isdir(t_dir):
        try:
            files = sorted(
                (f for f in os.listdir(t_dir) if f.endswith(".md")),
                reverse=True,
            )
            for fname in files:
                file_date = fname[:-3]
                if target_date and file_date != target_date:
                    continue
                fpath = os.path.join(t_dir, fname)
                with open(fpath, "r", encoding="utf-8") as f:
                    lines = f.read().splitlines()
                block = []
                for line in lines:
                    if line.startswith("- **["):
                        if block:
                            entry_txt = f"[{file_date}] " + " ".join(block)
                            if (not q or q in entry_txt.lower()) and entry_txt not in seen:
                                results.append(entry_txt)
                                seen.add(entry_txt)
                                if len(results) >= limit:
                                    return results
                        block = [line[2:].strip()]
                    elif block and line.strip():
                        block.append(line.strip())
                if block:
                    entry_txt = f"[{file_date}] " + " ".join(block)
                    if (not q or q in entry_txt.lower()) and entry_txt not in seen:
                        results.append(entry_txt)
                        seen.add(entry_txt)
                        if len(results) >= limit:
                            return results
        except OSError:
            pass

    return results[:limit]


def record_screen_observation(summary: str, *, timestamp: float | None = None) -> None:
    """Record a visual screen observation into the rolling 3-hour screen memory buffer."""
    cleaned = " ".join((summary or "").split())
    if not cleaned:
        return
    now = time.time() if timestamp is None else float(timestamp)
    retention_seconds = max(60.0, float(getattr(config, "SCREEN_MEMORY_RETENTION_HOURS", 3.0)) * 3600.0)
    cutoff = now - retention_seconds
    with _lock:
        _SCREEN_MEMORY_BUFFER.append({"timestamp": now, "summary": cleaned})
        _SCREEN_MEMORY_BUFFER[:] = [
            item for item in _SCREEN_MEMORY_BUFFER if item["timestamp"] >= cutoff
        ]


def get_recent_screen_observations(limit: int = 10, max_age_hours: float | None = None) -> list[dict]:
    """Return recent screen observations within the retention window (default 3 hours)."""
    now = time.time()
    hours = (
        float(getattr(config, "SCREEN_MEMORY_RETENTION_HOURS", 3.0))
        if max_age_hours is None
        else float(max_age_hours)
    )
    cutoff = now - max(60.0, hours * 3600.0)
    with _lock:
        _SCREEN_MEMORY_BUFFER[:] = [
            item for item in _SCREEN_MEMORY_BUFFER if item["timestamp"] >= cutoff
        ]
        selected = _SCREEN_MEMORY_BUFFER[-max(1, limit):]
        return [
            {
                "timestamp": item["timestamp"],
                "time_str": datetime.datetime.fromtimestamp(item["timestamp"]).strftime("%H:%M:%S"),
                "summary": item["summary"],
            }
            for item in selected
        ]


def clear_screen_observations() -> None:
    """Clear the rolling visual screen memory buffer."""
    with _lock:
        _SCREEN_MEMORY_BUFFER.clear()

