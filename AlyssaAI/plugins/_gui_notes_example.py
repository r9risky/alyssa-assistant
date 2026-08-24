"""
Example plugin: a little sticky-notes tab inside Settings, to show what a
plugin can do with the GUI extension point (not just spoken FUNCTIONS/TOOLS).

Disabled by default - this file starts with "_" (see plugin_loader.py's
underscore convention). Rename it to gui_notes_example.py and hit "Reload
Plugins" in Settings > Plugins to try it; a "Notes" tab appears at the end
of the sidebar without restarting Alyssa.

THE CONTRACT
------------
Two module-level names are all a plugin needs to add a Settings tab:

- GUI_TAB_TITLE (str): the label shown in the sidebar.
- build_gui_tab(dialog) -> QWidget | None: called once when the tab is
  (re)built. `dialog` is the live ConfigDialog instance, so you can reuse
  its theming/helpers (dialog._t() for the current color dict,
  dialog._help_label(...) for a themed status line, dialog.companion for
  the running assistant, etc.) instead of hardcoding your own styling.
  Return None if there's nothing to show (e.g. a prerequisite isn't met).

Optionally set GUI_TAB_SCROLL = False if your widget wants to manage its
own scrolling/layout (a canvas, a splitter, ...) rather than being dropped
into a QScrollArea the way a plain form tab is.

Everything below FUNCTIONS/TOOLS still works exactly like any other
plugin - this one just also happens to have a face.
"""
import json
import os

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton,
    QListWidget, QListWidgetItem,
)

NOTES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_notes_example_data.json")


def _load_notes() -> list:
    try:
        with open(NOTES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save_notes(notes: list):
    try:
        with open(NOTES_PATH, "w", encoding="utf-8") as f:
            json.dump(notes, f, indent=2)
    except OSError:
        pass


# -- Spoken/typed side: works with or without the GUI tab below -------------

def add_note(text: str) -> str:
    """Adds a note to the plugin's list."""
    notes = _load_notes()
    notes.append(text)
    _save_notes(notes)
    return f"Added: {text}"


FUNCTIONS = {"add_note": add_note}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "add_note",
            "description": "Save a short note for later.",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string", "description": "The note to save."}},
                "required": ["text"],
            },
        },
    },
]


# -- GUI side: a plain list + add/delete, no different from any other -------
# -- QWidget you'd build in a normal PySide6 app.                     -------

GUI_TAB_TITLE = "Notes"


def build_gui_tab(dialog) -> QWidget:
    theme = dialog._t()

    w = QWidget()
    layout = QVBoxLayout(w)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(10)

    intro = dialog._help_label(
        "Example plugin tab - notes saved here are also readable/writable "
        "by voice via the add_note tool. Try: \"remember to buy milk\"."
    )
    intro.setWordWrap(True)
    intro.setStyleSheet(f"color: {theme['subtext']}; font-size: 11px;")
    layout.addWidget(intro)

    note_list = QListWidget()
    for note in _load_notes():
        note_list.addItem(QListWidgetItem(note))
    layout.addWidget(note_list, 1)

    row = QHBoxLayout()
    entry = QLineEdit()
    entry.setPlaceholderText("Type a note and press Enter or Add…")
    add_btn = QPushButton("Add")
    delete_btn = QPushButton("Delete selected")
    row.addWidget(entry, 1)
    row.addWidget(add_btn)
    layout.addLayout(row)
    layout.addWidget(delete_btn)

    def refresh():
        note_list.clear()
        for note in _load_notes():
            note_list.addItem(QListWidgetItem(note))

    def on_add():
        text = entry.text().strip()
        if not text:
            return
        add_note(text)
        entry.clear()
        refresh()

    def on_delete():
        item = note_list.currentItem()
        if item is None:
            return
        notes = _load_notes()
        row_index = note_list.currentRow()
        if 0 <= row_index < len(notes):
            del notes[row_index]
            _save_notes(notes)
        refresh()

    entry.returnPressed.connect(on_add)
    add_btn.clicked.connect(on_add)
    delete_btn.clicked.connect(on_delete)

    return w
