"""
Sticky notes (voice + Settings tab). Same sticky_notes.json for both.

Tools: add/list/delete/clear. GUI_PANEL_TITLE + build_gui_panel (see
plugin_loader.py / README). Panel polls the file while the tab is open
(no push from the tool-call side).
"""
import json
import os
import threading
import time

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_NOTES_FILE = os.path.join(_BASE_DIR, "sticky_notes.json")
_lock = threading.RLock()


def _load() -> list:
    with _lock:
        if not os.path.exists(_NOTES_FILE):
            return []
        try:
            with open(_NOTES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError):
            return []


def _save(notes: list):
    # Atomic write (temp + replace) so a crash can't corrupt sticky_notes.json.
    with _lock:
        tmp_path = _NOTES_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(notes, f, indent=2)
        os.replace(tmp_path, _NOTES_FILE)


def _next_id(notes: list) -> int:
    return max((n.get("id", 0) for n in notes), default=0) + 1


def add_sticky_note(text: str) -> str:
    """Adds a new sticky note."""
    text = (text or "").strip()
    if not text:
        return "Can't add an empty sticky note - what should it say?"
    with _lock:
        notes = _load()
        note_id = _next_id(notes)
        notes.append({"id": note_id, "text": text, "created": time.time()})
        _save(notes)
    return f"Added sticky note #{note_id}: {text}"


def list_sticky_notes() -> str:
    """Lists all current sticky notes."""
    notes = _load()
    if not notes:
        return "No sticky notes right now."
    lines = [f"#{n.get('id')}: {n.get('text', '')}" for n in notes]
    return "Sticky notes:\n" + "\n".join(lines)


def delete_sticky_note(note_id: int) -> str:
    """Delete one note by id from list_sticky_notes."""
    with _lock:
        notes = _load()
        try:
            note_id = int(note_id)
        except (TypeError, ValueError):
            return f"'{note_id}' isn't a valid sticky note number."
        remaining = [n for n in notes if n.get("id") != note_id]
        if len(remaining) == len(notes):
            return f"No sticky note #{note_id} found."
        _save(remaining)
    return f"Deleted sticky note #{note_id}."


def clear_sticky_notes() -> str:
    """Deletes every sticky note."""
    with _lock:
        count = len(_load())
        _save([])
    return f"Cleared {count} sticky note{'s' if count != 1 else ''}."


FUNCTIONS = {
    "add_sticky_note": add_sticky_note,
    "list_sticky_notes": list_sticky_notes,
    "delete_sticky_note": delete_sticky_note,
    "clear_sticky_notes": clear_sticky_notes,
}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "add_sticky_note",
            "description": (
                "Adds a short sticky note the user wants jotted down onto a "
                "visible running list (a to-do/shopping-list style item) - "
                "different from remember_fact, which is for facts about the "
                "user rather than a list of notes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The note's text."},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_sticky_notes",
            "description": "Lists all current sticky notes.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_sticky_note",
            "description": "Deletes one sticky note by its number, as shown by list_sticky_notes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "note_id": {"type": "integer", "description": "The note's number."},
                },
                "required": ["note_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clear_sticky_notes",
            "description": "Deletes every sticky note at once.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


# Settings tab: GUI_PANEL_TITLE (str) + build_gui_panel(parent) -> QWidget.
GUI_PANEL_TITLE = "Sticky Notes"

# Opt out of settings_dialog.py's QScrollArea; QListWidget already scrolls.
GUI_PANEL_SCROLLABLE = False


def build_gui_panel(parent):
    """Settings tab: live list, add box, Delete/Clear. Polls _NOTES_FILE every 2s."""
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import (
        QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
        QPushButton, QVBoxLayout, QWidget,
    )

    w = QWidget()
    w.setObjectName("tabPage")
    layout = QVBoxLayout(w)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(10)

    intro = QLabel(
        "These are the same notes Alyssa can add, read, and delete by "
        "voice (\u201cadd a sticky note: ...\u201d) - this list refreshes "
        "on its own."
    )
    intro.setWordWrap(True)
    intro.setStyleSheet(f"color: {parent._t()['subtext']}; font-size: 11px;")
    layout.addWidget(intro)

    note_list = QListWidget()
    layout.addWidget(note_list, 1)

    add_row = QHBoxLayout()
    entry = QLineEdit()
    entry.setPlaceholderText("New sticky note...")
    add_row.addWidget(entry, 1)
    add_btn = QPushButton("Add")
    add_row.addWidget(add_btn)
    layout.addLayout(add_row)

    button_row = QHBoxLayout()
    delete_btn = QPushButton("Delete Selected")
    button_row.addWidget(delete_btn)
    clear_btn = QPushButton("Clear All")
    button_row.addWidget(clear_btn)
    button_row.addStretch(1)
    layout.addLayout(button_row)

    status = QLabel("")
    status.setStyleSheet(f"color: {parent._t()['subtext']}; font-size: 11px;")
    layout.addWidget(status)

    # Rebuild the list only when (id, text) on disk actually changed.
    state = {"signature": None}

    def refresh(force: bool = False):
        notes = _load()
        signature = tuple((n.get("id"), n.get("text")) for n in notes)
        if not force and signature == state["signature"]:
            return
        state["signature"] = signature
        note_list.clear()
        for n in notes:
            item = QListWidgetItem(f"#{n.get('id')}   {n.get('text', '')}")
            item.setData(Qt.UserRole, n.get("id"))
            note_list.addItem(item)

    def on_add():
        text = entry.text().strip()
        if not text:
            return
        add_sticky_note(text)
        entry.clear()
        refresh(force=True)
        status.setText("Added.")

    def on_delete():
        item = note_list.currentItem()
        if item is None:
            status.setText("Select a note to delete first.")
            return
        delete_sticky_note(item.data(Qt.UserRole))
        refresh(force=True)
        status.setText("Deleted.")

    def on_clear():
        if note_list.count() == 0:
            return
        clear_sticky_notes()
        refresh(force=True)
        status.setText("Cleared all notes.")

    add_btn.clicked.connect(on_add)
    entry.returnPressed.connect(on_add)
    delete_btn.clicked.connect(on_delete)
    clear_btn.clicked.connect(on_clear)

    poll_timer = QTimer(w)
    poll_timer.setInterval(2000)
    poll_timer.timeout.connect(refresh)
    poll_timer.start()
    # Stop the poll timer when the widget is destroyed (e.g. plugin reload).
    w.destroyed.connect(lambda: poll_timer.stop())

    refresh(force=True)
    return w
