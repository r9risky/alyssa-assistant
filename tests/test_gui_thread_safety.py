"""Widgets touched from the assistant worker thread must end up on the GUI thread."""
import ast
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _run_in_worker(qapp, fn):
    """Run fn on a worker thread while the GUI thread pumps events, like the real app."""
    import time

    result = {}
    worker = threading.Thread(target=lambda: result.setdefault("value", fn()))
    worker.start()
    deadline = time.monotonic() + 5
    while worker.is_alive() and time.monotonic() < deadline:
        qapp.processEvents()
        worker.join(0.01)
    assert not worker.is_alive(), "worker thread hung waiting for the GUI thread"
    return result.get("value")


def test_companion_calls_from_a_worker_run_on_the_gui_thread(qapp, monkeypatch):
    from PySide6.QtCore import QObject, QThread
    from overlay import widgets

    class FakeBubble(QObject):
        def show_message(self, text):
            self.thread_seen = QThread.currentThread()
            return text.upper()

    class FakeCompanion(QObject):
        def __init__(self):
            super().__init__()
            self.bubble = FakeBubble()
            self.mood = None

        def set_mood(self, mood):
            self.thread_seen = QThread.currentThread()
            self.mood = mood
            return self.bubble

    companion = FakeCompanion()
    monkeypatch.setattr(widgets, "_ACTIVE_COMPANION_WINDOW", companion)
    gui_thread = qapp.thread()

    def tool():
        win = widgets.get_active_companion()
        returned = win.set_mood("happy")  # plugin forgot run_on_gui_thread
        shown = win.bubble.show_message("hi")
        return win, returned, shown

    win, returned, shown = _run_in_worker(qapp, tool)

    assert isinstance(win, widgets.GuiThreadProxy)
    assert isinstance(returned, widgets.GuiThreadProxy)  # QObject results stay wrapped
    assert companion.mood == "happy"
    assert shown == "HI"
    assert companion.thread_seen is gui_thread
    assert companion.bubble.thread_seen is gui_thread


def test_companion_is_unwrapped_on_the_gui_thread(qapp, monkeypatch):
    from PySide6.QtCore import QObject
    from overlay import widgets

    companion = QObject()
    monkeypatch.setattr(widgets, "_ACTIVE_COMPANION_WINDOW", companion)
    assert widgets.get_active_companion() is companion
    monkeypatch.setattr(widgets, "_ACTIVE_COMPANION_WINDOW", None)
    assert widgets.get_active_companion() is None


def test_call_on_gui_thread_passes_errors_back(qapp):
    from overlay import widgets

    def boom():
        raise ValueError("nope")

    def worker():
        try:
            widgets.call_on_gui_thread(boom)
        except ValueError as e:
            return str(e)

    assert _run_in_worker(qapp, worker) == "nope"


def test_call_on_gui_thread_gives_up_instead_of_hanging(qapp):
    from overlay import widgets

    # GUI thread never pumps events here, so the call can't run in time.
    result = {}
    worker = threading.Thread(
        target=lambda: result.setdefault("value", widgets.call_on_gui_thread(lambda: 1, timeout=0.05))
    )
    worker.start()
    worker.join(2)
    assert not worker.is_alive()
    assert result["value"] is None
    qapp.processEvents()  # flush the late call


# GUI-thread-only entry points by contract: Settings builds plugin panels on the GUI thread.
_GUI_BUILDERS = {"build_gui", "build_gui_panel"}


def _module_level_qt_constructions(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    qt_classes = {
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and any(isinstance(b, ast.Name) and b.id.startswith("Q") for b in node.bases)
    }
    for fn in tree.body:
        if not isinstance(fn, ast.FunctionDef) or fn.name in _GUI_BUILDERS:
            continue
        stack = list(fn.body)
        while stack:
            node = stack.pop()
            # Nested functions/lambdas are what gets handed to run_on_gui_thread.
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                continue
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                name = node.func.id
                is_qt_widget = name in qt_classes or (
                    name.startswith("Q") and name[1:2].isupper()
                    and name not in {"QApplication", "QCoreApplication", "QGuiApplication"}
                )
                if is_qt_widget:
                    yield f"{path.relative_to(ROOT)}:{node.lineno} {fn.name}() creates {name}"
            stack.extend(ast.iter_child_nodes(node))


def test_tools_do_not_create_widgets_on_the_worker_thread():
    """Tools run on the assistant thread; widgets must be built inside a function
    passed to run_on_gui_thread (or in build_gui/build_gui_panel)."""
    offenders = [
        hit
        for folder in ("plugins", "actions")
        for path in sorted((ROOT / folder).glob("*.py"))
        for hit in _module_level_qt_constructions(path)
    ]
    assert offenders == []
