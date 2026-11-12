"""Regression tests for the Discord call crash, Vesktop detection, and call panel styling."""

import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import plugins.call_mode as call_mode
import voice_playback
from call_ears import get_call_session


def _procs(*names):
    return [SimpleNamespace(info={"pid": i + 100, "name": n}) for i, n in enumerate(names)]


def test_vesktop_is_detected_as_call_app():
    with patch("psutil.process_iter", return_value=_procs("explorer.exe", "Vesktop.exe")):
        assert call_mode.resolve_call_app("auto") == ("Vesktop", [101])
        # "join the Discord call" also works when the user runs Vesktop.
        assert call_mode.resolve_call_app("Discord") == ("Vesktop", [101])


def test_requested_app_not_running_keeps_its_name():
    with patch("psutil.process_iter", return_value=_procs("explorer.exe")):
        assert call_mode.resolve_call_app("zoom") == ("Zoom", [])
        assert call_mode.resolve_call_app("") == ("Discord", [])


def test_join_call_does_not_start_a_second_speaker():
    with patch("psutil.process_iter", return_value=_procs()), \
            patch.object(voice_playback, "speak") as speak:
        reply = call_mode.join_call({"app": "auto", "message": "on in 15"})
        time.sleep(0.7)
    get_call_session().end_call()
    speak.assert_not_called()
    assert "Joined Discord" in reply and "on in 15" in reply


def test_play_is_serialized_across_threads():
    active, overlap = [0], []

    def fake_play(path, stop_event=None):
        active[0] += 1
        overlap.append(active[0])
        time.sleep(0.05)
        active[0] -= 1
        return True

    with patch.object(voice_playback, "_play_locked", side_effect=fake_play):
        threads = [threading.Thread(target=voice_playback._play, args=("x.mp3",)) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    assert max(overlap) == 1


def test_call_panel_paint_does_not_restyle():
    from PySide6.QtWidgets import QApplication
    from overlay.call_panel import CallPanelWidget
    _app = QApplication.instance() or QApplication([])
    panel = CallPanelWidget(None, {"color_theme": "light"})
    with patch.object(panel, "setStyleSheet") as restyle, \
            patch.object(panel.end_btn, "setStyleSheet") as restyle_btn:
        panel.grab()
    restyle.assert_not_called()
    restyle_btn.assert_not_called()
