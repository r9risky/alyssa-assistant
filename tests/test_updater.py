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
                ("alyssa-release/overlay/__init__.py", "new overlay\n"),
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
            self.assertEqual(
                (install / "overlay" / "__init__.py").read_text(encoding="utf-8"),
                "new overlay\n",
            )
            self.assertIn("SETTING = 'mine'", (install / "config.py").read_text(encoding="utf-8"))
            self.assertEqual((install / ".alyssa-version").read_text(encoding="utf-8"), "v1.6.0\n")

    def test_apply_release_replaces_edited_application_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = root / "install"
            old_release = root / "old-release"
            new_release = root / "new-release"
            install.mkdir()
            old_release.mkdir()
            new_release.mkdir()
            (install / "overlay.py").write_text("official\n", encoding="utf-8")
            (old_release / "overlay.py").write_text("official\n", encoding="utf-8")
            (new_release / "overlay.py").write_text("next release\n", encoding="utf-8")
            updater._write_manifest(old_release, install)

            (install / "overlay.py").write_text("my custom tab\n", encoding="utf-8")
            updater._apply_release(new_release, install)

            self.assertEqual(
                (install / "overlay.py").read_text(encoding="utf-8"), "next release\n"
            )

    def test_apply_release_removes_old_managed_files_but_keeps_personal_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = root / "install"
            old_release = root / "old-release"
            new_release = root / "new-release"
            (install / "plugins").mkdir(parents=True)
            old_release.mkdir()
            new_release.mkdir()
            (install / "old_module.py").write_text("old\n", encoding="utf-8")
            (old_release / "old_module.py").write_text("old\n", encoding="utf-8")
            (install / "config.py").write_text("MY_SETTING = True\n", encoding="utf-8")
            (install / "plugins" / "mine.py").write_text("personal\n", encoding="utf-8")
            updater._write_manifest(old_release, install)

            updater._apply_release(new_release, install)

            self.assertFalse((install / "old_module.py").exists())
            self.assertTrue((install / "config.py").exists())
            self.assertTrue((install / "plugins" / "mine.py").exists())

    def test_manifestless_legacy_zip_removes_only_known_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = root / "install"
            release = root / "release"
            (install / "plugins").mkdir(parents=True)
            (release / "overlay").mkdir(parents=True)
            (release / "plugins").mkdir()
            (install / ".alyssa-version").write_text("v1.0.8\n", encoding="utf-8")
            (install / "overlay.py").write_text("legacy\n", encoding="utf-8")
            (install / "start_alyssa.bat").write_text("legacy\n", encoding="utf-8")
            (install / "voice.py").write_text("legacy\n", encoding="utf-8")
            (install / "personal.py").write_text("mine\n", encoding="utf-8")
            (install / "plugins" / "mine.py").write_text("mine\n", encoding="utf-8")
            (install / "plugins" / "weather.py").write_text("legacy\n", encoding="utf-8")
            (release / "overlay" / "__init__.py").write_text("new\n", encoding="utf-8")
            (release / "plugins" / "weather.py").write_text("new\n", encoding="utf-8")

            managed = updater._managed_paths(release, install)
            updater._apply_release(release, install, managed)
            updater._write_manifest(release, install, managed)

            self.assertFalse((install / "overlay.py").exists())
            self.assertFalse((install / "start_alyssa.bat").exists())
            self.assertFalse((install / "voice.py").exists())
            self.assertTrue((install / "personal.py").exists())
            self.assertTrue((install / "plugins" / "mine.py").exists())
            self.assertEqual(
                (install / "plugins" / "weather.py").read_text(encoding="utf-8"),
                "new\n",
            )
            self.assertTrue((install / "overlay" / "__init__.py").exists())
            self.assertTrue((install / updater.MANIFEST_FILE).exists())

    def test_manifestless_release_snapshots_cover_supported_versions(self):
        cases = {
            "v1.0.8": ("actions.py", "actions/bridges.py"),
            "v1.1.0": ("LATENCY_AUDIT.md", "actions.py"),
            "v1.1.1": ("credential_store.py", "LATENCY_AUDIT.md"),
            "v1.1.2": ("credential_store.py", "LATENCY_AUDIT.md"),
            "v1.1.4": ("voice_playback.py", "tests/test_tool_filtering.py"),
            "v1.1.10": ("tests/test_tool_filtering.py", "alyssaai.zip"),
        }
        with tempfile.TemporaryDirectory() as temporary:
            install = Path(temporary)
            marker = install / ".alyssa-version"
            for version, (known, absent) in cases.items():
                with self.subTest(version=version):
                    marker.write_text(version + "\n", encoding="utf-8")
                    managed = updater._previously_managed_paths(install)
                    self.assertIn(known, managed)
                    self.assertNotIn(absent, managed)

    def test_managed_release_plugin_is_updated_and_removed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = root / "install"
            first_release = root / "first-release"
            second_release = root / "second-release"
            third_release = root / "third-release"
            for path in (install, first_release, second_release, third_release):
                (path / "plugins").mkdir(parents=True)
            (first_release / "plugins" / "official.py").write_text("v1\n", encoding="utf-8")

            managed = updater._managed_paths(first_release, install)
            updater._apply_release(first_release, install, managed)
            updater._write_manifest(first_release, install, managed)
            (second_release / "plugins" / "official.py").write_text("v2\n", encoding="utf-8")

            managed = updater._managed_paths(second_release, install)
            updater._apply_release(second_release, install, managed)
            updater._write_manifest(second_release, install, managed)
            self.assertEqual(
                (install / "plugins" / "official.py").read_text(encoding="utf-8"), "v2\n"
            )

            updater._apply_release(third_release, install)
            self.assertFalse((install / "plugins" / "official.py").exists())

    def test_git_install_updates_tracked_plugin_and_removes_only_tracked_old_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = root / "install"
            release = root / "release"
            for path in (install, release):
                (path / "plugins").mkdir(parents=True)
            (install / ".git").mkdir()
            (install / "old.py").write_text("old\n", encoding="utf-8")
            (install / "personal.py").write_text("mine\n", encoding="utf-8")
            (install / "config.py").write_text("SETTING = 'mine'\n", encoding="utf-8")
            (install / "plugins" / "official.py").write_text("v1\n", encoding="utf-8")
            (release / "plugins" / "official.py").write_text("v2\n", encoding="utf-8")
            tracked = Mock(stdout=b"old.py\0config.py\0plugins/official.py\0")

            with patch.object(updater.subprocess, "run", return_value=tracked):
                updater._apply_release(release, install)

            self.assertFalse((install / "old.py").exists())
            self.assertTrue((install / "personal.py").exists())
            self.assertTrue((install / "config.py").exists())
            self.assertEqual(
                (install / "plugins" / "official.py").read_text(encoding="utf-8"), "v2\n"
            )

    def test_check_latest_rejects_non_https_download_url(self):
        response = Mock()
        response.json.return_value = {
            "tag_name": "v1.6.0",
            "zipball_url": "http://example.invalid/release.zip",
        }
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(updater.requests, "get", return_value=response):
                with self.assertRaisesRegex(RuntimeError, "HTTPS"):
                    updater.check_latest(temporary)

    def test_safe_extract_rejects_oversized_expanded_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "large.zip"
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
                zipped.writestr("alyssa-release/main.py", "x" * 64)
            with patch.object(updater, "MAX_EXTRACTED_BYTES", 32):
                with self.assertRaisesRegex(RuntimeError, "expanded size"):
                    updater._safe_extract(archive, root / "source")

    def test_install_release_closes_download_response(self):
        archive_bytes = io.BytesIO()
        with zipfile.ZipFile(archive_bytes, "w") as zipped:
            zipped.writestr("alyssa-release/main.py", "new main\n")
            zipped.writestr("alyssa-release/config.py", "SETTING = True\n")
            zipped.writestr("alyssa-release/overlay/__init__.py", "new overlay\n")
        response = Mock()
        response.iter_content.return_value = [archive_bytes.getvalue()]
        release = {
            "latest_version": "v1.6.0",
            "download_url": "https://example.invalid/release.zip",
        }

        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(updater.requests, "get", return_value=response):
                updater.install_release(temporary, release)

        response.close.assert_called_once_with()

if __name__ == "__main__":
    unittest.main()
