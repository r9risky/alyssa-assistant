import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import config
from plugins import system_watch


class SystemWatchTests(unittest.TestCase):
    def setUp(self):
        system_watch._disk_was_low = False

    def test_disabled_check_does_not_read_disk(self):
        with (
            patch.object(config, "SYSTEM_WATCH_ENABLED", False),
            patch.object(system_watch.shutil, "disk_usage") as disk_usage,
        ):
            self.assertIsNone(system_watch.check_watch())
            disk_usage.assert_not_called()

    def test_low_disk_alert_fires_once_until_space_recovers(self):
        gb = 1024 ** 3
        high = SimpleNamespace(total=100 * gb, used=95 * gb, free=5 * gb)
        normal = SimpleNamespace(total=100 * gb, used=50 * gb, free=50 * gb)

        with (
            patch.object(config, "SYSTEM_WATCH_ENABLED", True),
            patch.object(system_watch.shutil, "disk_usage", return_value=high) as disk_usage,
        ):
            self.assertIn("95% used", system_watch.check_watch())
            self.assertIsNone(system_watch.check_watch())
            disk_usage.return_value = normal
            self.assertIsNone(system_watch.check_watch())
            disk_usage.return_value = high
            self.assertIn("95% used", system_watch.check_watch())


if __name__ == "__main__":
    unittest.main()
