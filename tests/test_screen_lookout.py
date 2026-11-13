import unittest
from unittest.mock import patch

import actions
import config
from plugins import code_husk_mode, screen_lookout


class ScreenLookoutTests(unittest.TestCase):
    def setUp(self):
        screen_lookout.clear_all_watches()
        screen_lookout._last_watch_look = (None, 0.0)

    def tearDown(self):
        screen_lookout.clear_all_watches()

    def test_watchers_use_the_hook_the_loader_runs(self):
        # plugin_loader only runs check_watch; check_proactive was never called.
        self.assertTrue(callable(getattr(screen_lookout, "check_watch", None)))
        self.assertTrue(callable(getattr(code_husk_mode, "check_watch", None)))

    def test_unchanged_screen_skips_the_vision_call(self):
        screen_lookout.watch_for("download complete")
        same = bytes(32 * 18)
        with (
            patch.object(config, "QUIET_MODE", False),
            patch.object(config, "SCREEN_WATCH_ENABLED", False),
            patch.object(config, "CHECK_IN_ENABLED", False),
            patch.object(screen_lookout, "_screen_fingerprint", return_value=same),
            patch.object(actions.bridges, "describe_screen", return_value="A browser tab.") as look,
            patch.object(screen_lookout.memory, "record_screen_observation"),
        ):
            screen_lookout.check_watch()
            screen_lookout.check_watch()
            self.assertEqual(look.call_count, 1)
            with patch.object(screen_lookout.time, "time", return_value=screen_lookout.time.time() + 61):
                screen_lookout.check_watch()  # still re-checks now and then for small changes
            self.assertEqual(look.call_count, 2)

    def test_watch_fires_when_it_sees_the_condition(self):
        screen_lookout.watch_for("download complete")
        with (
            patch.object(config, "QUIET_MODE", False),
            patch.object(config, "SCREEN_WATCH_ENABLED", False),
            patch.object(config, "CHECK_IN_ENABLED", False),
            patch.object(screen_lookout, "_screen_fingerprint", return_value=None),
            patch.object(actions.bridges, "describe_screen", return_value="Chrome shows Download complete."),
            patch.object(screen_lookout.memory, "record_screen_observation"),
        ):
            self.assertIn("download complete", screen_lookout.check_watch())


if __name__ == "__main__":
    unittest.main()
