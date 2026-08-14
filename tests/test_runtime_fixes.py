import os
import runpy
import sys
import tempfile
import threading
import unittest
from datetime import datetime
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main
import overlay
import actions
import brain
from brain import dialogue
from brain import providers
from brain.providers import gemini
import nameutil
import recorder
import transcribe
import voice
import config
from plugins import calendar_gmail, reminders


class PortableTimeTests(unittest.TestCase):
    def test_time_formatting_works_without_unix_strftime_flags(self):
        value = datetime(2026, 8, 13, 9, 5)
        self.assertEqual(reminders._format_time(value), "9:05 AM")
        self.assertEqual(calendar_gmail._format_time(value), "9:05 AM")


class AtomicConfigWriteTests(unittest.TestCase):
    def test_failed_replace_preserves_original_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "config.py")
            with open(path, "w", encoding="utf-8") as f:
                f.write("VALUE = 'original'\n")

            with patch.object(overlay.os, "replace", side_effect=OSError("disk error")):
                with self.assertRaises(OSError):
                    overlay._atomic_write_text(path, "VALUE = 'new'\n")

            with open(path, "r", encoding="utf-8") as f:
                self.assertEqual(f.read(), "VALUE = 'original'\n")
            self.assertFalse(os.path.exists(path + ".tmp"))


class GeminiVisionRetryTests(unittest.TestCase):
    def test_temporary_unavailable_response_is_retried(self):
        class Response:
            def __init__(self, status_code, body):
                self.status_code = status_code
                self.text = str(body)
                self._body = body

            def raise_for_status(self):
                if self.status_code >= 400:
                    raise gemini.requests.exceptions.HTTPError(response=self)

            def json(self):
                return self._body

        unavailable = Response(503, {"error": {"status": "UNAVAILABLE"}})
        success = Response(200, {
            "candidates": [{"content": {"parts": [{"text": "I can see your desktop."}]}}]
        })

        with (
            patch.object(config, "GEMINI_API_KEY", "test-key"),
            patch.object(gemini._HTTP_SESSION, "post", side_effect=[unavailable, success]) as post,
            patch.object(gemini.time, "sleep") as sleep,
        ):
            result = gemini._describe_image_gemini(b"image", "image/jpeg", "describe")

        self.assertEqual(result, "I can see your desktop.")
        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once_with(1)


class VoiceLifecycleTests(unittest.TestCase):
    def test_short_approval_lead_in_uses_sentence_pipeline(self):
        with (
            patch.object(voice, "_speak_pipelined", return_value=False) as pipelined,
            patch.object(voice, "_speak_one") as one,
        ):
            voice.speak("I need your approval. May I open Chrome?")

        pipelined.assert_called_once()
        one.assert_not_called()

    def test_interrupted_pipeline_cleanup_skips_unstarted_jobs(self):
        fd, path = tempfile.mkstemp(suffix=".mp3")
        os.close(fd)
        created_threads = []
        real_thread = threading.Thread

        def make_thread(*args, **kwargs):
            thread = real_thread(*args, **kwargs)
            created_threads.append(thread)
            return thread

        stop_event = threading.Event()
        stop_event.set()
        with (
            patch.object(voice, "_synthesize_to_temp_file", return_value=path),
            patch.object(voice.threading, "Thread", side_effect=make_thread),
        ):
            voice._speak_pipelined(["first", "never started"], stop_event=stop_event)

        for thread in created_threads:
            thread.join(timeout=1.0)
        self.assertTrue(created_threads)
        self.assertTrue(all(not thread.is_alive() for thread in created_threads))
        self.assertFalse(os.path.exists(path))

    def test_playback_unloads_music_resource(self):
        calls = []

        class Music:
            def load(self, path): calls.append("load")
            def set_volume(self, value): calls.append("volume")
            def play(self): calls.append("play")
            def get_busy(self): return False
            def stop(self): calls.append("stop")
            def unload(self): calls.append("unload")

        with (
            patch.object(voice, "_ensure_mixer"),
            patch.object(voice.pygame.mixer, "music", Music()),
        ):
            self.assertTrue(voice._play("speech.mp3"))

        self.assertEqual(calls[-2:], ["stop", "unload"])

    def test_playback_tracks_live_edge_volume_changes(self):
        volumes = []

        class Music:
            def load(self, path): pass
            def set_volume(self, value): volumes.append(value)
            def play(self): pass
            def get_busy(self):
                if len(volumes) == 1:
                    config.EDGE_TTS_VOLUME = "-50%"
                    return True
                return False
            def stop(self): pass
            def unload(self): pass

        original = config.EDGE_TTS_VOLUME
        try:
            config.EDGE_TTS_VOLUME = "+0%"
            with (
                patch.object(voice, "_ensure_mixer"),
                patch.object(voice.pygame.mixer, "music", Music()),
            ):
                voice._play("speech.mp3")
        finally:
            config.EDGE_TTS_VOLUME = original

        self.assertEqual(volumes, [1.0, 0.0])

    def test_speak_never_waits_forever_for_barge_in_listener(self):
        join_calls = []

        class ListenerThread:
            def __init__(self, *args, **kwargs): pass
            def start(self): pass
            def join(self, *args, **kwargs): join_calls.append((args, kwargs))

        with (
            patch.object(main.config, "SPEAK_RESPONSES", True),
            patch.object(main.threading, "Thread", ListenerThread),
            patch.object(main.voice, "speak"),
        ):
            main.speak("hello")

        self.assertEqual(join_calls, [((), {"timeout": 1.0})])


class MicrophoneSelectionTests(unittest.TestCase):
    def test_configured_microphone_is_used_for_every_input_stream(self):
        opened_with = {}

        class Stream:
            def __init__(self, **kwargs):
                opened_with.update(kwargs)
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                pass

        devices = [
            {"name": "Silent virtual input", "max_input_channels": 2},
            {"name": "Microphone (PD100X)", "max_input_channels": 1},
        ]
        with (
            patch.object(recorder.config, "MICROPHONE_DEVICE", "PD100X", create=True),
            patch.object(recorder.sd, "query_devices", return_value=devices),
            patch.object(recorder.sd, "check_input_settings"),
            patch.object(recorder.sd, "InputStream", Stream),
        ):
            with recorder._open_stream(samplerate=16000, channels=1, dtype="int16"):
                pass

        self.assertEqual(opened_with["device"], 1)


class FollowupAuditFixTests(unittest.TestCase):
    def test_missing_app_stays_a_failure_through_fast_reply(self):
        dialogue._recent_action_context.clear()
        with (
            patch.object(actions.config, "CONFIRM_BEFORE_ACTIONS", False),
            patch.object(actions, "_resolve_app_path", return_value=None),
        ):
            output = actions.open_app("DefinitelyMissing")

        self.assertIn("couldn't", output)
        self.assertEqual(
            dialogue._natural_fast_reply("open_app", {"app_name": "DefinitelyMissing"}, output, "open it"),
            output,
        )
        dialogue._record_recent_action("open_app", {"app_name": "DefinitelyMissing"}, output)
        self.assertEqual(dialogue._recent_action_context, [])

    def test_name_match_cache_tracks_live_config_changes(self):
        original_name = nameutil.config.ASSISTANT_NAME
        try:
            nameutil._compile_name_pattern.cache_clear()
            nameutil.name_pattern()  # populate the cache for the old name
            nameutil.config.ASSISTANT_NAME = "NovaUniqueWakeName"
            self.assertTrue(nameutil.contains_name("NovaUniqueWakeName, hello"))
        finally:
            nameutil.config.ASSISTANT_NAME = original_name
            nameutil._compile_name_pattern.cache_clear()

    def test_whisper_async_reload_does_not_reload_on_caller_thread(self):
        targets = []

        class Worker:
            def __init__(self, target, daemon):
                targets.append(target)
            def start(self):
                pass

        with (
            patch.object(transcribe.threading, "Thread", Worker),
            patch.object(transcribe, "reload_model", side_effect=AssertionError("ran synchronously")),
        ):
            transcribe.reload_model_async()

        self.assertEqual(len(targets), 1)

    def test_whisper_reload_waits_for_active_inference(self):
        inference_started = threading.Event()
        release_inference = threading.Event()
        reload_finished = threading.Event()

        def infer(_audio):
            inference_started.set()
            release_inference.wait(1)
            return "done"

        with patch.object(transcribe, "_transcribe_impl_locked", side_effect=infer):
            inference = threading.Thread(target=transcribe._transcribe_impl, args=(b"audio",))
            inference.start()
            self.assertTrue(inference_started.wait(1))
            reload_thread = threading.Thread(
                target=lambda: (transcribe.reload_model(), reload_finished.set())
            )
            reload_thread.start()
            self.assertFalse(reload_finished.wait(0.05))
            release_inference.set()
            inference.join(1)
            reload_thread.join(1)

        self.assertTrue(reload_finished.is_set())

    def test_watcher_loop_uses_reloaded_watcher_list(self):
        calls = []
        old = {"name": "old.py", "func": lambda: calls.append("old"), "interval": 10}
        new = {"name": "new.py", "func": lambda: calls.append("new"), "interval": 10}

        class StopLoop(Exception):
            pass

        with (
            patch.object(main.plugin_loader, "get_watchers", side_effect=[[old], [new]]),
            patch.object(main.time, "time", return_value=0.0),
            patch.object(main.time, "sleep", side_effect=[None, StopLoop]),
        ):
            with self.assertRaises(StopLoop):
                main.run_watcher_loop()

        self.assertEqual(calls, ["old", "new"])

    def test_malformed_provider_response_returns_spoken_error(self):
        brain.clear_conversation_history()
        with patch.object(providers, "_call_model", return_value={"unexpected": True}):
            reply = brain.handle_command("answer this provider response test")
        self.assertIn("malformed response", reply)


class FinalAuditFixTests(unittest.TestCase):
    def tearDown(self):
        actions.consume_restart_request()

    def test_restart_alyssa_request_is_consumed_once(self):
        self.assertEqual(actions.restart_alyssa(), "Restarting Alyssa.")
        self.assertTrue(actions.consume_restart_request())
        self.assertFalse(actions.consume_restart_request())

    def test_relaunch_alyssa_reuses_the_current_python_command(self):
        with (
            patch.object(actions.sys, "executable", r"C:\Python\python.exe"),
            patch.object(actions.sys, "argv", ["main.py", "--demo"]),
            patch.object(actions.os, "execv") as execv,
        ):
            actions.relaunch_alyssa()

        execv.assert_called_once_with(
            r"C:\Python\python.exe",
            [r"C:\Python\python.exe", "main.py", "--demo"],
        )

    def test_restart_alyssa_voice_command_does_not_restart_pc(self):
        with patch.object(providers, "_call_model") as call_model:
            self.assertEqual(brain.handle_command("restart"), "Restarting Alyssa.")
        call_model.assert_not_called()

    def test_gemini_api_key_loads_from_environment(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "environment-key"}):
            loaded = runpy.run_path(config.__file__)
        self.assertEqual(loaded["GEMINI_API_KEY"], "environment-key")

    def test_plugin_local_google_credentials_are_accepted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_dir = os.path.join(temp_dir, "plugins")
            os.makedirs(plugin_dir)
            credentials_path = os.path.join(plugin_dir, "credentials.json")
            with open(credentials_path, "w", encoding="utf-8") as f:
                f.write("{}")

            with (
                patch.object(calendar_gmail, "_BASE_DIR", temp_dir),
                patch.object(calendar_gmail, "_CREDENTIALS_PATH", os.path.join(temp_dir, "credentials.json")),
                patch.object(calendar_gmail, "_TOKEN_PATH", os.path.join(temp_dir, "token.json")),
                patch.object(calendar_gmail, "_GOOGLE_LIBS_AVAILABLE", True),
            ):
                self.assertEqual(calendar_gmail._find_credentials_path(), credentials_path)
                self.assertIsNone(calendar_gmail._availability_check())

    def test_user_environment_paths_return_expanded(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {"ALYSSA_TEST_PATH": temp_dir}):
                resolved = actions._resolve_placeholder_user_path(
                    r"%ALYSSA_TEST_PATH%\document.txt"
                )
        self.assertNotIn("%ALYSSA_TEST_PATH%", resolved)
        self.assertEqual(resolved, os.path.normpath(os.path.join(temp_dir, "document.txt")))

    def test_failed_power_commands_report_failure(self):
        failure = actions.subprocess.CalledProcessError(1, ["shutdown"])
        with patch.object(actions.subprocess, "run", side_effect=failure):
            reply = actions.system_power_action("restart", confirmed=True)
        self.assertIn("Couldn't restart", reply)

    def test_failed_workstation_lock_reports_failure(self):
        class User32:
            @staticmethod
            def LockWorkStation():
                return 0

        class Windll:
            user32 = User32()

        with patch.object(actions.ctypes, "windll", Windll()):
            reply = actions.system_power_action("lock")
        self.assertEqual(reply, "Couldn't lock the PC.")

    def test_content_search_stops_at_aggregate_byte_limit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            first = os.path.join(temp_dir, "a.txt")
            second = os.path.join(temp_dir, "b.txt")
            with open(first, "w", encoding="utf-8") as f:
                f.write("xxxx")
            with open(second, "w", encoding="utf-8") as f:
                f.write("needle")

            with (
                patch.object(actions, "_MAX_CONTENT_SEARCH_BYTES", 4),
                patch.object(actions, "_MAX_CONTENT_FILE_BYTES", 100),
                patch.object(actions.os, "walk", return_value=[(temp_dir, [], ["a.txt", "b.txt"])]),
            ):
                reply = actions.search_files("needle", temp_dir, search_contents=True)

        self.assertIn("Content search stopped", reply)

    def test_content_search_counts_bytes_read_after_file_growth(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "grown.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write("xxxxneedle")

            with (
                patch.object(actions, "_MAX_CONTENT_SEARCH_BYTES", 4),
                patch.object(actions, "_MAX_CONTENT_FILE_BYTES", 100),
                patch.object(actions.os.path, "getsize", return_value=1),
            ):
                reply = actions.search_files("needle", temp_dir, search_contents=True)

        self.assertIn("No files matching", reply)
        self.assertIn("Content search stopped", reply)


if __name__ == "__main__":
    unittest.main()
