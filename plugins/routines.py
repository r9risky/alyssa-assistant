"""Routines: one phrase runs several steps ("gaming mode" -> open Discord, open Steam, quiet mode on).

Saying a routine's name (or "start <name>") runs it with no model call at all.
Make them by voice ("make a routine called gaming mode that opens Discord and Steam")
or in Settings > Routines, one per line:  gaming mode: open discord; open steam; quiet mode on

Steps she understands: open <app>, open site <url>, close window, play <song>,
volume <0-100>, quiet mode on/off, press <keys>, say <words>, wait <seconds>.
"""
import json
import os
import re
import sys
import threading
import time

if getattr(sys, "frozen", False):
    _BASE_DIR = os.path.dirname(sys.executable)
else:
    _BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROUTINES_FILE = os.path.join(_BASE_DIR, "routines.json")
_lock = threading.Lock()
MAX_WAIT_SECONDS = 30
STEP_HELP = (
    "open <app>, open site <url>, close window, play <song>, volume <0-100>, "
    "quiet mode on/off, press <keys>, say <words>, wait <seconds>"
)


def _norm(name: str) -> str:
    return " ".join(re.sub(r"[^\w\s'-]", " ", (name or "").lower()).split())


def load() -> dict:
    """{name: [step, ...]} in the order they were made."""
    try:
        with open(ROUTINES_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {_norm(k): [str(s) for s in v] for k, v in data.items() if _norm(k) and isinstance(v, list)}


def save(routines: dict) -> None:
    tmp = ROUTINES_FILE + ".tmp"
    with _lock:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(routines, f, indent=2)
        os.replace(tmp, ROUTINES_FILE)  # atomic: a crash mid-write can't wipe your routines


def parse_text(text: str) -> dict:
    """The Settings editor's 'name: step; step' lines -> {name: [steps]}."""
    routines = {}
    for line in (text or "").splitlines():
        name, sep, steps = line.partition(":")
        steps = [s.strip() for s in re.split(r"[;\n]", steps) if s.strip()]
        if sep and _norm(name) and steps:
            routines[_norm(name)] = steps
    return routines


def format_text(routines: dict) -> str:
    return "\n".join(f"{name}: {'; '.join(steps)}" for name, steps in routines.items())


def _call(tool: str, **kwargs) -> str:
    import actions

    func = actions.FUNCTIONS.get(tool)
    if func is None:
        return f"couldn't {tool.replace('_', ' ')} (that tool isn't available)"
    return str(func(**kwargs)).strip()


def run_step(step: str) -> str:
    """Do one step; returns what happened, in a few words."""
    s = step.strip().rstrip(".!")
    low = s.lower()
    if m := re.match(r"^(?:open|go to)\s+(?:the\s+)?(?:site|website|url|page)\s+(.+)$", low):
        url = s[m.start(1):]
        return _call("open_url", url=url if "://" in url else "https://" + url)
    if re.match(r"^close(?:\s+(?:the\s+)?(?:window|it|this))?$", low):
        return _call("close_window")
    if m := re.match(r"^(?:open|launch|start)\s+(.+)$", low):
        return _call("open_app", app_name=s[m.start(1):])
    if m := re.match(r"^play\s+(.+)$", low):
        return _call("play_music", query=s[m.start(1):])
    if m := re.match(r"^(?:set\s+(?:the\s+)?)?volume\s+(?:to\s+)?(\d{1,3})\s*%?$", low):
        return _call("set_volume_level", percent=max(0, min(100, int(m.group(1)))))
    if m := re.match(r"^quiet\s+mode\s+(on|off)$", low):
        return _call("set_quiet_mode", enabled=m.group(1) == "on")
    if m := re.match(r"^press\s+(.+)$", low):
        return _call("press_keys", keys=m.group(1))
    if m := re.match(r"^say\s+(.+)$", low):
        return s[m.start(1):]
    if m := re.match(r"^wait\s+(\d+(?:\.\d+)?)\s*(?:s|sec|secs|seconds?)?$", low):
        time.sleep(min(MAX_WAIT_SECONDS, float(m.group(1))))
        return ""
    return f"skipped \"{s}\" (I only know: {STEP_HELP})"


def run(name: str) -> str:
    steps = load().get(_norm(name))
    if not steps:
        return f"I don't have a routine called {name}."
    said = [part for part in (run_step(step) for step in steps) if part]
    return f"{_norm(name).capitalize()}: " + " ".join(p if p[-1:] in ".!?" else p + "." for p in said) if said else "Done."


def handle_intent(cleaned: str, text: str = ""):
    """'gaming mode', 'start gaming mode', 'gaming mode on' run that routine, no model call."""
    phrase = _norm(cleaned)
    phrase = re.sub(r"^(?:start|run|do|begin|activate|switch to|let's do|lets do)\s+", "", phrase)
    phrase = re.sub(r"\s+(?:on|time|please)$", "", phrase)
    routines = load()
    for name in (phrase, phrase.removeprefix("the ").removesuffix(" routine")):
        if name in routines:
            return run(name)
    return None


def create_routine(name: str = "", steps=None, **kwargs) -> str:
    name = _norm(name)
    if isinstance(steps, str):
        steps = re.split(r"[;\n]", steps)
    steps = [str(s).strip() for s in (steps or []) if str(s).strip()]
    if not name or not steps:
        return "A routine needs a name and at least one step."
    routines = load()
    routines[name] = steps
    save(routines)
    return f"Saved {name}: {'; '.join(steps)}. Just say \"{name}\" to run it."


def delete_routine(name: str = "", **kwargs) -> str:
    routines = load()
    if routines.pop(_norm(name), None) is None:
        return f"There's no routine called {name}."
    save(routines)
    return f"Deleted the {_norm(name)} routine."


def list_routines(**kwargs) -> str:
    routines = load()
    if not routines:
        return "You don't have any routines yet."
    return "Your routines: " + "; ".join(f"{n} ({len(s)} step{'s' if len(s) != 1 else ''})" for n, s in routines.items()) + "."


# Each result is already the whole reply: no second model call to reword it.
INSTANT_REPLY_TOOLS = {"create_routine", "delete_routine", "list_routines"}

FUNCTIONS = {
    "create_routine": create_routine,
    "delete_routine": delete_routine,
    "list_routines": list_routines,
}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "create_routine",
            "description": (
                "Save a routine: a name the user can say later to run several steps at once. "
                f"Write each step in this form: {STEP_HELP}."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "What the user will say, e.g. 'gaming mode'"},
                    "steps": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name", "steps"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_routine",
            "description": "Delete a saved routine by name.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_routines",
            "description": "List the user's saved routines.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

GUI_PANEL_TITLE = "Routines"


def build_gui_panel(parent):
    """Settings tab: edit every routine as text, one per line."""
    from PySide6.QtWidgets import QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

    w = QWidget()
    w.setObjectName("tabPage")
    layout = QVBoxLayout(w)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(10)
    intro = QLabel(
        "One routine per line: its name, a colon, then steps separated by semicolons. "
        "Say the name (or “start <name>”) to run it, with no AI call.\n"
        f"Steps: {STEP_HELP}."
    )
    intro.setWordWrap(True)
    intro.setStyleSheet(f"color: {parent._t()['subtext']}; font-size: 11px;")
    layout.addWidget(intro)
    editor = QPlainTextEdit(format_text(load()))
    editor.setPlaceholderText("gaming mode: open discord; open steam; quiet mode on")
    layout.addWidget(editor, 1)
    row = QHBoxLayout()
    status = QLabel("")
    save_btn = QPushButton("Save Routines")
    save_btn.setObjectName("primaryButton")
    row.addWidget(status, 1)
    row.addWidget(save_btn)
    layout.addLayout(row)

    def _save():
        routines = parse_text(editor.toPlainText())
        try:
            save(routines)
        except OSError as e:
            status.setText(f"Couldn't save: {e}")
            return
        editor.setPlainText(format_text(routines))
        status.setText(f"Saved {len(routines)} routine{'s' if len(routines) != 1 else ''}.")

    save_btn.clicked.connect(_save)
    return w
