"""Regressions from the Nov 2026 Capri/Gemini overhaul (see PR description)."""

import contextlib
import inspect
import os
import threading
import time

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import actions
import brain
import recorder


def test_every_tool_accepts_its_declared_arguments():
    """The brain calls func(**model_args); plugins that only took `args` crashed."""
    actions.reload_plugins()
    brain.reload_plugin_tools()
    broken = []
    for tool in brain.TOOLS:
        name = tool["function"]["name"]
        func = actions.FUNCTIONS[name]
        params = inspect.signature(func).parameters
        if any(p.kind == p.VAR_KEYWORD for p in params.values()):
            continue
        declared = tool["function"].get("parameters", {}).get("properties", {})
        broken += [f"{name}({arg})" for arg in declared if arg not in params]
    assert broken == []


def test_core_imports_of_plugins_share_the_loaded_instance():
    """dialogue.py imports plugins.watch_along directly; it must see the tools' state."""
    actions.reload_plugins()
    import plugins.watch_along as watch_along

    assert actions.FUNCTIONS["start_watch_along"].__globals__ is watch_along.__dict__


def test_run_on_gui_thread_runs_calls_from_worker_threads():
    """A context-less QTimer.singleShot from a worker thread never fires."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from overlay.widgets import run_on_gui_thread

    app = QApplication.instance() or QApplication([])
    ran_on = []
    worker = threading.Thread(
        target=run_on_gui_thread, args=(lambda: ran_on.append(threading.current_thread()),)
    )
    worker.start()
    worker.join()
    QTimer.singleShot(200, app.quit)
    app.exec()
    assert ran_on == [threading.main_thread()]


class _LoudVad:
    engine = "test"

    def __init__(self, *a, **k):
        pass

    def is_speech(self, chunk):
        return bool(np.abs(np.asarray(chunk)).max() > 1000)

    def reset(self):
        pass


def test_wake_word_mode_without_model_still_records_speech(monkeypatch):
    """With no ONNX model the heuristic detector is skipped and VAD starts the
    recording; main.py then requires her name in the transcript."""
    real_config = recorder.config

    class _WakeConfig:
        WAKE_WORD_MODE = "wake_word"
        ADAPTIVE_SILENCE_ENABLED = False
        SILENCE_SECONDS = 0.1
        MAX_RECORD_SECONDS = 2

        def __getattr__(self, name):
            return getattr(real_config, name)

    chunks = [np.full(512, 5000, dtype=np.int16)] * 10 + [np.zeros(512, dtype=np.int16)] * 60

    class _Stream:
        def read(self, frames):
            return (chunks.pop(0) if chunks else np.zeros(frames, dtype=np.int16)), False

    @contextlib.contextmanager
    def _fake_open(**kwargs):
        yield _Stream()

    monkeypatch.setattr(recorder, "config", _WakeConfig())
    monkeypatch.setattr(recorder, "VoiceActivityDetector", _LoudVad)
    monkeypatch.setattr(recorder, "_open_stream", _fake_open)
    monkeypatch.setattr(recorder.transcribe, "start_streaming", lambda: None)
    # The recorder transcribes early during the end pause; keep that off real Whisper.
    monkeypatch.setattr(recorder.transcribe, "_transcribe_impl", lambda audio: "hey Alyssa")
    recorder._talk_request.clear()

    detector = recorder.wake_word.get_wake_word_detector()
    assert not detector.has_model
    audio = recorder.record_command()
    assert audio is not None and audio.size > 0
    assert not recorder.was_wake_word_triggered()  # so main.py checks for her name
    assert recorder.transcribe.transcribe(audio) == "hey Alyssa"  # early result reused


def test_mic_error_backs_off_instead_of_spinning(monkeypatch):
    monkeypatch.setattr(recorder, "_MIC_ERROR_BACKOFF_SECONDS", 0.2)
    recorder._talk_request.clear()

    def failing(result_q, *a, **k):
        result_q.put((None, RuntimeError("no mic")))

    monkeypatch.setattr(recorder, "_record_command_blocking", failing)
    start = time.monotonic()
    assert recorder.record_command() is None
    assert time.monotonic() - start >= 0.15
