import io
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import zipfile

import updater


class UpdaterTests(unittest.TestCase):
    def test_apply_release_preserves_personal_data_and_adds_new_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = root / "install"
            release = root / "release"
            (install / "plugins").mkdir(parents=True)
            (release / "plugins").mkdir(parents=True)

            (install / "config.py").write_text(
                "API_KEY = 'personal'\nCHOICES = [\n    'mine',\n]\n", encoding="utf-8"
            )
            (release / "config.py").write_text(
                "API_KEY = ''\nCHOICES = ['default']\nNEW_OPTION = True\n", encoding="utf-8"
            )
            (install / "overlay.py").write_text("old\n", encoding="utf-8")
            (release / "overlay.py").write_text("new\n", encoding="utf-8")
            (install / "memory.json").write_text('["remember me"]', encoding="utf-8")
            (release / "memory.json").write_text("[]", encoding="utf-8")
            (install / "color_themes.json").write_text('{"mine": {}}', encoding="utf-8")
            (release / "color_themes.json").write_text('{"default": {}}', encoding="utf-8")
            (install / "plugins" / "weather.py").write_text("personal\n", encoding="utf-8")
            (release / "plugins" / "weather.py").write_text("release\n", encoding="utf-8")
            (release / "plugins" / "new_plugin.py").write_text("new\n", encoding="utf-8")

            updater._apply_release(release, install)

            config = (install / "config.py").read_text(encoding="utf-8")
            self.assertIn("API_KEY = 'personal'", config)
            self.assertIn("'mine'", config)
            self.assertIn("NEW_OPTION = True", config)
            self.assertEqual((install / "overlay.py").read_text(encoding="utf-8"), "new\n")
            self.assertEqual((install / "memory.json").read_text(encoding="utf-8"), '["remember me"]')
            self.assertEqual((install / "color_themes.json").read_text(encoding="utf-8"), '{"mine": {}}')
            self.assertEqual((install / "plugins" / "weather.py").read_text(encoding="utf-8"), "personal\n")
            self.assertEqual((install / "plugins" / "new_plugin.py").read_text(encoding="utf-8"), "new\n")

    def test_safe_extract_rejects_parent_traversal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "bad.zip"
            with zipfile.ZipFile(archive, "w") as zipped:
                zipped.writestr("../outside.py", "bad")
            with self.assertRaisesRegex(RuntimeError, "unsafe path"):
                updater._safe_extract(archive, root / "source")

    def test_apply_release_rolls_back_an_earlier_change_on_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = root / "install"
            release = root / "release"
            install.mkdir()
            release.mkdir()
            for name in ("a.py", "b.py"):
                (install / name).write_text("old\n", encoding="utf-8")
                (release / name).write_text("new\n", encoding="utf-8")

            real_copy = updater._atomic_copy

            def failing_copy(source, destination):
                if source.parent == release and destination.name == "b.py":
                    raise OSError("disk full")
                return real_copy(source, destination)

            with patch.object(updater, "_atomic_copy", side_effect=failing_copy):
                with self.assertRaisesRegex(OSError, "disk full"):
                    updater._apply_release(release, install)

            self.assertEqual((install / "a.py").read_text(encoding="utf-8"), "old\n")
            self.assertEqual((install / "b.py").read_text(encoding="utf-8"), "old\n")

    def test_current_release_does_not_download_again(self):
        response = Mock()
        response.json.return_value = {
            "tag_name": updater.CURRENT_VERSION,
            "zipball_url": "https://example.invalid/release.zip",
        }
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(updater.requests, "get", return_value=response) as get:
                self.assertEqual(
                    updater.install_latest(temporary),
                    (False, updater.CURRENT_VERSION),
                )
        get.assert_called_once()

    def test_check_latest_compares_versions_and_returns_release_notes(self):
        response = Mock()
        response.json.return_value = {
            "tag_name": "v1.6.0",
            "zipball_url": "https://example.invalid/release.zip",
            "body": "New update flow",
        }
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(updater.requests, "get", return_value=response):
                release = updater.check_latest(temporary)

        self.assertEqual(release["current_version"], updater.CURRENT_VERSION)
        self.assertEqual(release["latest_version"], "v1.6.0")
        self.assertTrue(release["update_available"])
        self.assertEqual(release["notes"], "New update flow")

    def test_check_latest_rejects_uncomparable_version(self):
        response = Mock()
        response.json.return_value = {
            "tag_name": "latest",
            "zipball_url": "https://example.invalid/release.zip",
        }
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(updater.requests, "get", return_value=response):
                with self.assertRaisesRegex(RuntimeError, "Couldn't compare"):
                    updater.check_latest(temporary)

    def test_install_release_downloads_applies_and_records_version(self):
        archive_bytes = io.BytesIO()
        with zipfile.ZipFile(archive_bytes, "w") as zipped:
            for name, contents in (
                ("alyssa-release/main.py", "new main\n"),
                ("alyssa-release/overlay.py", "new overlay\n"),
                ("alyssa-release/config.py", "SETTING = 'default'\n"),
            ):
                zipped.writestr(name, contents)

        response = Mock()
        response.iter_content.return_value = [archive_bytes.getvalue()]
        release = {
            "latest_version": "v1.6.0",
            "download_url": "https://example.invalid/release.zip",
        }
        with tempfile.TemporaryDirectory() as temporary:
            install = Path(temporary)
            (install / "main.py").write_text("old main\n", encoding="utf-8")
            (install / "config.py").write_text("SETTING = 'mine'\n", encoding="utf-8")
            with patch.object(updater.requests, "get", return_value=response):
                self.assertEqual(updater.install_release(install, release), "v1.6.0")

            self.assertEqual((install / "main.py").read_text(encoding="utf-8"), "new main\n")
            self.assertEqual((install / "overlay.py").read_text(encoding="utf-8"), "new overlay\n")
            self.assertIn("SETTING = 'mine'", (install / "config.py").read_text(encoding="utf-8"))
            self.assertEqual((install / ".alyssa-version").read_text(encoding="utf-8"), "v1.6.0\n")

    def test_manifest_detects_locally_edited_application_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = root / "install"
            release = root / "release"
            install.mkdir()
            release.mkdir()
            (install / "overlay.py").write_text("official\n", encoding="utf-8")
            (release / "overlay.py").write_text("next release\n", encoding="utf-8")
            updater._write_manifest(release, install)

            (install / "overlay.py").write_text("my custom tab\n", encoding="utf-8")

            self.assertEqual(
                updater._local_changes(release, install), ["overlay.py"]
            )

    def test_manifest_ignores_personal_config_and_plugins(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = root / "install"
            release = root / "release"
            (install / "plugins").mkdir(parents=True)
            (release / "plugins").mkdir(parents=True)
            for path, text in (
                (install / "config.py", "MY_SETTING = True\n"),
                (release / "config.py", "MY_SETTING = False\n"),
                (install / "plugins" / "mine.py", "personal\n"),
                (release / "plugins" / "mine.py", "official\n"),
            ):
                path.write_text(text, encoding="utf-8")

            updater._write_manifest(release, install)

            self.assertEqual(updater._local_changes(release, install), [])

    def test_git_detects_custom_tab_code_but_ignores_personal_settings(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = root / "install"
            release = root / "release"
            (install / ".git").mkdir(parents=True)
            release.mkdir()
            (release / "overlay.py").write_text("next release\n", encoding="utf-8")
            (release / "overlay_config.json").write_text("{}\n", encoding="utf-8")
            results = (
                Mock(stdout=b"overlay.py\0overlay_config.json\0"),
                Mock(stdout=b""),
            )

            with patch.object(updater.subprocess, "run", side_effect=results):
                self.assertEqual(
                    updater._local_changes(release, install), ["overlay.py"]
                )


if __name__ == "__main__":
    unittest.main()
