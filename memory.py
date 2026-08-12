"""
Persistent JSON memory storage and overlap retrieval for Alyssa.
"""

import json
import os
import re
import sys
import threading
from functools import lru_cache

import config

if getattr(sys, "frozen", False):
    _BASE_DIR = os.path.dirname(sys.executable)
else:
    _BASE_DIR = os.path.dirname(__file__)

MEMORY_FILE = os.path.join(_BASE_DIR, "memory.json")

_lock = threading.RLock()
_MEMORIES_CACHE = None
_MEMORIES_CACHE_MTIME = None
_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "am", "be", "been",
    "my", "me", "i", "you", "your", "it", "its", "this", "that", "to",
    "of", "in", "on", "at", "for", "and", "or", "with", "as", "do",
    "does", "did", "what", "whats", "which", "who", "how", "so",
}


@lru_cache(maxsize=4096)
def _tokenize(text: str) -> list:
    """Tokenizes and stems input text, ignoring stopwords."""
    words = re.findall(r"[a-z0-9']+", text.lower())
    stemmed = []
    for w in words:
        if w in _STOPWORDS or len(w) < 2:
            continue
        for suffix in ("ing", "ed", "es", "s"):
            if w.endswith(suffix) and len(w) > len(suffix) + 2:
                w = w[: -len(suffix)]
                break
        stemmed.append(w)
    return stemmed


def _score(query_tokens: list, fact_tokens: list) -> int:
    """Calculates shared distinct token count between query and fact."""
    if not query_tokens or not fact_tokens:
        return 0
    return len(set(query_tokens) & set(fact_tokens))


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


def relevant_memories(query: str, limit: int = 20) -> list:
    """Returns top relevant memories matching the query using keyword overlap."""
    if limit <= 0:
        return []
    memories = load_memories()
    if not memories:
        return []

    query_tokens = _tokenize(query or "")
    scored = [
        (i, m, _score(query_tokens, _tokenize(m)))
        for i, m in enumerate(memories)
    ]

    selected_indices = []
    if query_tokens:
        ranked = sorted(
            [s for s in scored if s[2] > 0],
            key=lambda s: (s[2], s[0]),
            reverse=True,
        )
        selected_indices = [i for i, _m, _score_val in ranked[:limit]]

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
