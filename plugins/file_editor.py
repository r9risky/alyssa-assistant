"""
Read and edit plain-text files in the user's home folder.

- read_text_file: no approval needed.
- edit_text_file / append_to_text_file / create_text_file: always ask first
  (spoken or clicked approval, whatever the security preset), back up the old
  version to file_backups/, then write atomically.
- undo_last_file_edit: puts the most recent backup back.

Only text-type extensions under the home folder, never Alyssa's own folder
(her config and code), so a confused or injected request can't rewrite her.
"""
import datetime
import json
import os
import shutil
import sys

from actions.apps_and_files import _friendly_file_name, _resolve_placeholder_user_path
from actions.confirmation import _confirm

if getattr(sys, "frozen", False):
    _BASE_DIR = os.path.dirname(sys.executable)
else:
    _BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUP_DIR = os.path.join(_BASE_DIR, "file_backups")
MAX_BACKUPS = 30
MAX_FILE_BYTES = 1_000_000
MAX_READ_CHARS = 6000

TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".yaml", ".yml", ".toml",
    ".ini", ".cfg", ".conf", ".log", ".xml", ".html", ".htm", ".css", ".rst", ".tex",
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".c", ".h", ".cpp", ".hpp", ".cs",
    ".go", ".rs", ".rb", ".php", ".lua", ".sql", ".srt",
}  # no .bat/.ps1/.vbs/.reg: writing those is running code later


def _home() -> str:
    return os.path.realpath(os.path.expanduser("~"))


def _inside(path: str, folder: str) -> bool:
    try:
        return os.path.commonpath([os.path.normcase(path), os.path.normcase(folder)]) == os.path.normcase(folder)
    except ValueError:  # different drives
        return False


def _check_path(path: str, must_exist: bool = True):
    """(resolved_path, None) when allowed, else (None, reason to speak)."""
    if not (path or "").strip():
        return None, "Which file? Give me its name or path."
    resolved = os.path.realpath(_resolve_placeholder_user_path(path.strip()))
    name = _friendly_file_name(resolved)
    if os.path.splitext(resolved)[1].lower() not in TEXT_EXTENSIONS:
        return None, f"I only edit plain text files, and {name} isn't one I can safely change."
    if not _inside(resolved, _home()):
        return None, f"I can only work with files inside your user folder, not {resolved}."
    if _inside(resolved, os.path.realpath(_BASE_DIR)):
        return None, "I can't edit my own files - change my settings in the Settings window instead."
    if must_exist and not os.path.isfile(resolved):
        return None, f"I couldn't find {resolved}."
    if must_exist and os.path.getsize(resolved) > MAX_FILE_BYTES:
        return None, f"{name} is too big for me to edit safely."
    return resolved, None


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8", newline="") as f:  # newline="" keeps CRLF as-is
        return f.read()


def _write(path: str, text: str) -> None:
    tmp = path + ".alyssa-tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    os.replace(tmp, path)


def _backup(path: str) -> None:
    """Copy the current file (or a 'did not exist' marker) so undo can restore it."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    copy = os.path.join(BACKUP_DIR, f"{stamp}.bak")
    if os.path.exists(path):
        shutil.copy2(path, copy)
    with open(copy + ".json", "w", encoding="utf-8") as f:
        json.dump({"path": path, "existed": os.path.exists(path)}, f)
    entries = sorted(n for n in os.listdir(BACKUP_DIR) if n.endswith(".bak.json"))
    for old in entries[:-MAX_BACKUPS]:
        for victim in (old, old[: -len(".json")]):
            try:
                os.remove(os.path.join(BACKUP_DIR, victim))
            except OSError:
                pass


def _approved(confirmed: bool, description: str) -> bool:
    # confirmed=True only comes from the spoken-approval path (model args are stripped of it).
    return confirmed or _confirm(description, force=True)


def _short(text: str, limit: int = 40) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def read_text_file(path: str) -> str:
    resolved, problem = _check_path(path)
    if problem:
        return problem
    try:
        text = _read(resolved)
    except UnicodeDecodeError:
        return f"{_friendly_file_name(resolved)} isn't plain UTF-8 text, so I can't read it."
    except OSError as e:
        return f"Couldn't read {_friendly_file_name(resolved)}: {e}"
    if len(text) > MAX_READ_CHARS:
        return f"{resolved} (first {MAX_READ_CHARS} characters):\n{text[:MAX_READ_CHARS]}"
    return f"{resolved}:\n{text}" if text else f"{resolved} is empty."


def edit_text_file(path: str, find: str, replace: str, confirmed: bool = False) -> str:
    resolved, problem = _check_path(path)
    if problem:
        return problem
    name = _friendly_file_name(resolved)
    if not find:
        return "Tell me the exact text to change."
    try:
        text = _read(resolved)
    except (OSError, UnicodeDecodeError) as e:
        return f"Couldn't read {name}: {e}"
    count = text.count(find)
    if count == 0:
        return f"I couldn't find that text in {name}. Read the file first to get it exactly."
    if count > 1:
        return f"That text appears {count} times in {name}; include more of the surrounding text so I change the right one."
    if not _approved(confirmed, f"change '{_short(find)}' to '{_short(replace)}' in {name}"):
        return "Cancelled by user."
    try:
        _backup(resolved)
        _write(resolved, text.replace(find, replace, 1))
    except OSError as e:
        return f"Couldn't save {name}: {e}"
    return f"Updated {name}."


def append_to_text_file(path: str, text: str, confirmed: bool = False) -> str:
    resolved, problem = _check_path(path)
    if problem:
        return problem
    name = _friendly_file_name(resolved)
    if not text:
        return "What should I add?"
    try:
        current = _read(resolved)
    except (OSError, UnicodeDecodeError) as e:
        return f"Couldn't read {name}: {e}"
    if not _approved(confirmed, f"add '{_short(text)}' to the end of {name}"):
        return "Cancelled by user."
    newline = "\r\n" if "\r\n" in current else "\n"
    joiner = "" if not current or current.endswith(("\n", "\r")) else newline
    try:
        _backup(resolved)
        _write(resolved, current + joiner + text.replace("\n", newline) + newline)
    except OSError as e:
        return f"Couldn't save {name}: {e}"
    return f"Added that to {name}."


def create_text_file(path: str, content: str = "", confirmed: bool = False) -> str:
    resolved, problem = _check_path(path, must_exist=False)
    if problem:
        return problem
    name = _friendly_file_name(resolved)
    if os.path.exists(resolved):
        return f"{name} already exists - I can add to it or change part of it instead."
    if not os.path.isdir(os.path.dirname(resolved)):
        return f"The folder for {name} doesn't exist."
    if not _approved(confirmed, f"create {name} in {os.path.dirname(resolved)}"):
        return "Cancelled by user."
    try:
        _backup(resolved)
        _write(resolved, content)
    except OSError as e:
        return f"Couldn't create {name}: {e}"
    return f"Created {name}."


def undo_last_file_edit(confirmed: bool = False) -> str:
    try:
        entries = sorted(n for n in os.listdir(BACKUP_DIR) if n.endswith(".bak.json"))
    except OSError:
        entries = []
    if not entries:
        return "There's no file edit to undo."
    meta_path = os.path.join(BACKUP_DIR, entries[-1])
    copy = meta_path[: -len(".json")]
    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    target = meta["path"]
    name = _friendly_file_name(target)
    if not _approved(confirmed, f"undo my last change to {name}"):
        return "Cancelled by user."
    try:
        if meta.get("existed"):
            shutil.copy2(copy, target)
        elif os.path.exists(target):
            os.remove(target)  # the edit created it
        os.remove(meta_path)
        if os.path.exists(copy):
            os.remove(copy)
    except OSError as e:
        return f"Couldn't undo the change to {name}: {e}"
    return f"Undid my last change to {name}."


_PATH_PARAM = {"type": "string", "description": "Full path, or a path like ~/Documents/notes.txt."}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_text_file",
            "description": (
                "Reads a plain-text file (notes, markdown, csv, json, code) in the user's "
                "folder. Use before editing so you know the exact text."
            ),
            "parameters": {"type": "object", "properties": {"path": _PATH_PARAM}, "required": ["path"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_text_file",
            "description": (
                "Changes one exact piece of text in a plain-text file, e.g. 'fix the typo in "
                "my notes', 'change the date in todo.md'. `find` must match the file exactly "
                "and appear once. The user is asked to approve first."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": _PATH_PARAM,
                    "find": {"type": "string", "description": "Exact existing text to replace."},
                    "replace": {"type": "string", "description": "New text (empty to delete it)."},
                },
                "required": ["path", "find", "replace"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "append_to_text_file",
            "description": "Adds text as new line(s) at the end of a plain-text file, e.g. 'add milk to my shopping list'. Asks the user first.",
            "parameters": {
                "type": "object",
                "properties": {"path": _PATH_PARAM, "text": {"type": "string", "description": "Text to add."}},
                "required": ["path", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_text_file",
            "description": "Creates a new plain-text file with the given content. Never overwrites. Asks the user first.",
            "parameters": {
                "type": "object",
                "properties": {"path": _PATH_PARAM, "content": {"type": "string", "description": "The file's text."}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "undo_last_file_edit",
            "description": "Undoes the most recent file change made with the file editing tools.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

FUNCTIONS = {
    "read_text_file": read_text_file,
    "edit_text_file": edit_text_file,
    "append_to_text_file": append_to_text_file,
    "create_text_file": create_text_file,
    "undo_last_file_edit": undo_last_file_edit,
}
