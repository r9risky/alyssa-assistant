import hashlib
import io
import json
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

import updater


ARCHIVE_NAME = "alyssa-assistant-v9.9.0.zip"
DOWNLOAD_URL = (
    "https://github.com/r9risky/alyssa-assistant/releases/download/"
    f"v9.9.0/{ARCHIVE_NAME}"
)
FINAL_DOWNLOAD_URL = (
    "https://release-assets.githubusercontent.com/github-production-release-asset/"
    "123/archive.zip?sp=read"
)


class UpdaterTests(unittest.TestCase):
    def test_render_release_notes_html_renders_header(self):
        self.assertEqual(
            updater.render_release_notes_html("## Highlights"),
            "<h2>Highlights</h2>",
        )

    def test_render_release_notes_html_renders_bullet_and_ordered_lists(self):
        rendered = updater.render_release_notes_html(
            "- First change\n* Second change\n\n1. Download\n2. Restart"
        )

        self.assertEqual(
            rendered,
            "<ul><li>First change</li><li>Second change</li></ul>\n"
            "<ol><li>Download</li><li>Restart</li></ol>",
        )

    def test_render_release_notes_html_renders_inline_formatting(self):
        rendered = updater.render_release_notes_html(
            "**Bold**, _italic_, and `code <value>`."
        )

        self.assertEqual(
            rendered,
            "<p><b>Bold</b>, <i>italic</i>, and "
            "<code>code &lt;value&gt;</code>.</p>",
        )

    def test_render_release_notes_html_renders_link(self):
        self.assertEqual(
            updater.render_release_notes_html(
                "See [the changelog](https://example.com/releases?a=1&b=2)."
            ),
            '<p>See <a href="https://example.com/releases?a=1&amp;b=2">'
            "the changelog</a>.</p>",
        )

    def test_render_release_notes_html_escapes_html_injection(self):
        rendered = updater.render_release_notes_html("<script>alert(1)</script>")

        self.assertEqual(
            rendered,
            "<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>",
        )
        self.assertNotIn("<script>", rendered)

    def test_render_release_notes_html_renders_plain_paragraph(self):
        self.assertEqual(
            updater.render_release_notes_html(
                "A plain release note.\nStill the same paragraph."
            ),
            "<p>A plain release note. Still the same paragraph.</p>",
        )

    def test_merge_config_keeps_new_default_when_old_value_uses_removed_name(self):
        old_source = (
            "import os\n"
            "SETTING = os.environ.get('SETTING', '')\n"
        )
        new_source = (
            "import credential_store\n"
            "SETTING = credential_store.get_secret('SETTING')\n"
        )
        credential_store = Mock()
        credential_store.get_secret.return_value = "new-default"

        with patch("builtins.print") as output:
            merged = updater.merge_config_settings(new_source, old_source)

        namespace = {}
        with patch.dict("sys.modules", {"credential_store": credential_store}):
            exec(merged, namespace)

        self.assertEqual(namespace["SETTING"], "new-default")
        output.assert_called_once_with(
            "config: kept new default for SETTING - old value referenced "
            "'os', which is not yet imported or defined"
        )

    def test_merge_config_preserves_plain_literal_value(self):
        merged = updater.merge_config_settings(
            "SETTING = 'new-default'\n", 'SETTING = "personal"\n'
        )
        namespace = {}
        exec(merged, namespace)
        self.assertEqual(namespace["SETTING"], "personal")

    def test_merge_config_never_preserves_known_secret_fields(self):
        for name in updater.SECRET_CONFIG_FIELDS:
            with self.subTest(name=name):
                new_source = f"{name} = 'new-default'\n"
                old_source = f"{name} = 'legacy-plaintext'\n"
                self.assertEqual(
                    updater.merge_config_settings(new_source, old_source),
                    new_source,
                )

    def test_merge_config_resolves_names_in_definition_order(self):
        new_source = (
            "def earlier():\n"
            "    return 'new-earlier'\n"
            "EARLIER = earlier()\n"
            "LATER = 'new-later'\n"
            "def later():\n"
            "    return 'new-later'\n"
        )
        old_source = (
            "EARLIER = earlier() + '-personal'\n"
            "LATER = later() + '-personal'\n"
        )

        merged = updater.merge_config_settings(new_source, old_source)
        namespace = {}
        exec(merged, namespace)

        self.assertEqual(namespace["EARLIER"], "new-earlier-personal")
        self.assertEqual(namespace["LATER"], "new-later")

    def test_merge_config_falls_back_when_spliced_source_is_invalid(self):
        new_source = 'é = "new-default"\n'
        old_source = 'é = "personal"\n'

        with patch("builtins.print") as output:
            merged = updater.merge_config_settings(new_source, old_source)

        self.assertEqual(merged, new_source)
        compile(merged, "config.py", "exec")
        output.assert_called_once_with(
            "config: merged settings were invalid; kept new config unchanged"
        )

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
            # resolve(): Windows temp dirs can be 8.3 short names (RUNNER~1), but
            # the updater resolves install_root, so staged paths use the long name.
            root = Path(temporary).resolve()
            install = root / "install"
            release = root / "release"
            install.mkdir()
            release.mkdir()
            for name in ("a.py", "b.py"):
                (install / name).write_text("old\n", encoding="utf-8")
                (release / name).write_text("new\n", encoding="utf-8")

            real_copy = updater._atomic_copy

            def failing_copy(source, destination):
                staged_root = install / updater.TRANSACTION_DIR / "new"
                if source.is_relative_to(staged_root) and destination.name == "b.py":
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
            "tag_name": "v9.9.0",
            "body": "New update flow",
            "assets": [
                {
                    "name": ARCHIVE_NAME,
                    "browser_download_url": DOWNLOAD_URL,
                    "digest": "sha256:" + "a" * 64,
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(updater.requests, "get", return_value=response):
                release = updater.check_latest(temporary)

        self.assertEqual(release["current_version"], updater.CURRENT_VERSION)
        self.assertEqual(release["latest_version"], "v9.9.0")
        self.assertTrue(release["update_available"])
        self.assertEqual(release["notes"], "New update flow")
        self.assertEqual(release["download_url"], DOWNLOAD_URL)
        self.assertEqual(release["sha256"], "a" * 64)

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

        archive_data = archive_bytes.getvalue()
        response = Mock()
        response.iter_content.return_value = [archive_data]
        response.headers = {}
        response.url = FINAL_DOWNLOAD_URL
        release = {
            "latest_version": "v9.9.0",
            "archive_name": ARCHIVE_NAME,
            "download_url": DOWNLOAD_URL,
            "sha256": hashlib.sha256(archive_data).hexdigest(),
        }
        with tempfile.TemporaryDirectory() as temporary:
            install = Path(temporary)
            (install / "main.py").write_text("old main\n", encoding="utf-8")
            (install / "config.py").write_text("SETTING = 'mine'\n", encoding="utf-8")
            with patch.object(updater.requests, "get", return_value=response):
                self.assertEqual(updater.install_release(install, release), "v9.9.0")

            self.assertEqual((install / "main.py").read_text(encoding="utf-8"), "new main\n")
            self.assertEqual(
                (install / "overlay" / "__init__.py").read_text(encoding="utf-8"),
                "new overlay\n",
            )
            self.assertIn("SETTING = 'mine'", (install / "config.py").read_text(encoding="utf-8"))
            self.assertEqual((install / ".alyssa-version").read_text(encoding="utf-8"), "v9.9.0\n")

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
            "tag_name": "v9.9.0",
            "assets": [
                {
                    "name": ARCHIVE_NAME,
                    "browser_download_url": "http://github.com/r9risky/alyssa-assistant/releases/download/v9.9.0/"
                    + ARCHIVE_NAME,
                    "digest": "sha256:" + "a" * 64,
                }
            ],
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
        archive_data = archive_bytes.getvalue()
        response = Mock()
        response.iter_content.return_value = [archive_data]
        response.headers = {}
        response.url = FINAL_DOWNLOAD_URL
        release = {
            "latest_version": "v9.9.0",
            "archive_name": ARCHIVE_NAME,
            "download_url": DOWNLOAD_URL,
            "sha256": hashlib.sha256(archive_data).hexdigest(),
        }

        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(updater.requests, "get", return_value=response):
                updater.install_release(temporary, release)

        response.close.assert_called_once_with()

    def test_launchers_do_not_request_administrator_privileges(self):
        root = Path(__file__).resolve().parents[1]
        launcher = (root / "scripts" / "start_alyssa.bat").read_text(encoding="utf-8")
        startup = (root / "scripts" / "install_startup.bat").read_text(encoding="utf-8")

        self.assertNotIn("runas", launcher.casefold())
        self.assertNotIn("runas", startup.casefold())
        self.assertNotIn("runlevel highest", startup.casefold())
        self.assertIn("-runlevel limited", startup.casefold())

    def test_check_latest_fails_closed_without_a_published_checksum(self):
        response = Mock()
        response.json.return_value = {
            "tag_name": "v9.9.0",
            "assets": [
                {
                    "name": ARCHIVE_NAME,
                    "browser_download_url": DOWNLOAD_URL,
                    "digest": None,
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(updater.requests, "get", return_value=response):
                with self.assertRaisesRegex(RuntimeError, "no SHA-256"):
                    updater.check_latest(temporary)

    def test_check_latest_accepts_a_published_checksum_asset(self):
        checksum_name = "SHA256SUMS"
        checksum_url = (
            "https://github.com/r9risky/alyssa-assistant/releases/download/"
            f"v9.9.0/{checksum_name}"
        )
        response = Mock()
        response.json.return_value = {
            "tag_name": "v9.9.0",
            "assets": [
                {
                    "name": ARCHIVE_NAME,
                    "browser_download_url": DOWNLOAD_URL,
                    "digest": None,
                },
                {
                    "name": checksum_name,
                    "browser_download_url": checksum_url,
                },
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(updater.requests, "get", return_value=response):
                release = updater.check_latest(temporary)

        self.assertIsNone(release["sha256"])
        self.assertEqual(release["checksum_url"], checksum_url)
        self.assertEqual(
            updater._parse_checksum_file(
                "b" * 64 + "  " + ARCHIVE_NAME, ARCHIVE_NAME
            ),
            "b" * 64,
        )

    def test_checksum_mismatch_is_rejected_before_extraction_or_install(self):
        archive_data = b"not the expected archive"
        response = Mock()
        response.iter_content.return_value = [archive_data]
        response.headers = {}
        response.url = FINAL_DOWNLOAD_URL
        release = {
            "latest_version": "v9.9.0",
            "archive_name": ARCHIVE_NAME,
            "download_url": DOWNLOAD_URL,
            "sha256": "0" * 64,
        }
        with tempfile.TemporaryDirectory() as temporary:
            install = Path(temporary)
            (install / "main.py").write_text("old main\n", encoding="utf-8")
            with (
                patch.object(updater.requests, "get", return_value=response),
                patch.object(updater, "_safe_extract") as extract,
                patch.object(updater, "_apply_release") as apply_release,
            ):
                with self.assertRaisesRegex(RuntimeError, "checksum did not match"):
                    updater.install_release(install, release)

            extract.assert_not_called()
            apply_release.assert_not_called()
            self.assertEqual(
                (install / "main.py").read_text(encoding="utf-8"), "old main\n"
            )
            self.assertFalse((install / updater.TRANSACTION_DIR).exists())

    def test_unexpected_final_download_host_is_rejected_before_extraction(self):
        archive_data = b"archive"
        response = Mock()
        response.iter_content.return_value = [archive_data]
        response.headers = {}
        response.url = "https://example.invalid/redirected.zip"
        release = {
            "latest_version": "v9.9.0",
            "archive_name": ARCHIVE_NAME,
            "download_url": DOWNLOAD_URL,
            "sha256": hashlib.sha256(archive_data).hexdigest(),
        }
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch.object(updater.requests, "get", return_value=response),
                patch.object(updater, "_safe_extract") as extract,
            ):
                with self.assertRaisesRegex(RuntimeError, "expected GitHub"):
                    updater.install_release(temporary, release)
            extract.assert_not_called()

    def test_interrupted_commit_is_detected_and_rolled_back_on_next_check(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = root / "install"
            old_release = root / "old-release"
            new_release = root / "new-release"
            for path in (install, old_release, new_release):
                path.mkdir()
            (install / "main.py").write_text("old\n", encoding="utf-8")
            (old_release / "main.py").write_text("old\n", encoding="utf-8")
            (new_release / "main.py").write_text("new\n", encoding="utf-8")
            (install / ".alyssa-version").write_text("v1.5.0\n", encoding="utf-8")
            updater._write_manifest(old_release, install)
            old_manifest = json.loads(
                (install / updater.MANIFEST_FILE).read_text(encoding="utf-8")
            )

            real_copy = updater._atomic_copy

            def interrupted_copy(source, destination):
                if destination.name == updater.MANIFEST_FILE:
                    raise KeyboardInterrupt("simulated process termination")
                return real_copy(source, destination)

            with patch.object(updater, "_atomic_copy", side_effect=interrupted_copy):
                with self.assertRaisesRegex(KeyboardInterrupt, "simulated"):
                    updater._apply_release(
                        new_release, install, {"main.py"}, version="v9.9.0"
                    )

            self.assertEqual(
                (install / "main.py").read_text(encoding="utf-8"), "new\n"
            )
            self.assertTrue((install / updater.TRANSACTION_DIR).is_dir())

            self.assertEqual(updater.current_version(install), "v1.5.0")
            self.assertEqual(
                (install / "main.py").read_text(encoding="utf-8"), "old\n"
            )
            self.assertEqual(
                json.loads((install / updater.MANIFEST_FILE).read_text(encoding="utf-8")),
                old_manifest,
            )
            self.assertFalse((install / updater.TRANSACTION_DIR).exists())

    def test_update_install_state_disables_and_reenables_restart(self):
        from overlay.settings_tabs.updates_tab import UpdatesTabMixin

        class Button:
            def __init__(self):
                self.enabled = True
                self.tooltip = ""

            def setEnabled(self, enabled):
                self.enabled = enabled

            def setToolTip(self, tooltip):
                self.tooltip = tooltip

        target = SimpleNamespace(
            check_update_btn=Button(), restart_alyssa_btn=Button(), uninstall_btn=Button()
        )
        UpdatesTabMixin._set_update_installing(target, True)
        self.assertTrue(target._update_installing)
        self.assertFalse(target.uninstall_btn.enabled)
        self.assertFalse(target.check_update_btn.enabled)
        self.assertFalse(target.restart_alyssa_btn.enabled)
        self.assertIn("do not close", target.restart_alyssa_btn.tooltip)

        UpdatesTabMixin._set_update_installing(target, False)
        self.assertFalse(target._update_installing)
        self.assertTrue(target.check_update_btn.enabled)
        self.assertTrue(target.restart_alyssa_btn.enabled)


    def test_safe_extract_supports_binary_standalone_bundle(self):
        archive_bytes = io.BytesIO()
        with zipfile.ZipFile(archive_bytes, "w") as zipped:
            zipped.writestr("Alyssa/Alyssa.exe", "MZ-stub-content")
            zipped.writestr("Alyssa/config.py", "TEST_KEY = 'val'\n")
            zipped.writestr("Alyssa/_internal/library.dll", "dll-content")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "Alyssa-Windows-x64.zip"
            archive.write_bytes(archive_bytes.getvalue())
            extracted_root = updater._safe_extract(archive, root / "extracted")
            self.assertTrue((extracted_root / "Alyssa.exe").is_file())
            self.assertTrue((extracted_root / "config.py").is_file())

    def test_release_asset_metadata_prefers_windows_zip_if_multiple(self):
        release = {
            "assets": [
                {
                    "name": "source-bundle.zip",
                    "browser_download_url": "https://github.com/r9risky/alyssa-assistant/releases/download/v9.9.0/source-bundle.zip",
                    "digest": "sha256:" + "a" * 64,
                },
                {
                    "name": "Alyssa-Windows-x64.zip",
                    "browser_download_url": "https://github.com/r9risky/alyssa-assistant/releases/download/v9.9.0/Alyssa-Windows-x64.zip",
                    "digest": "sha256:" + "b" * 64,
                },
            ]
        }
        metadata = updater._release_asset_metadata(release, "v9.9.0")
        self.assertEqual(metadata["archive_name"], "Alyssa-Windows-x64.zip")
        self.assertEqual(metadata["sha256"], "b" * 64)

    def _locked_exe_update(self, install, release):
        """Apply a release where Alyssa.exe is locked, leaving a pending_restart journal."""
        install.mkdir()
        release.mkdir()
        (install / "Alyssa.exe").write_bytes(b"old-exe")
        (release / "Alyssa.exe").write_bytes(b"new-exe")
        (install / "a.py").write_text("old\n", encoding="utf-8")
        (release / "a.py").write_text("new\n", encoding="utf-8")
        (release / "added.py").write_text("added\n", encoding="utf-8")
        (install / "config.py").write_text("API_KEY = 'mine'\n", encoding="utf-8")
        (release / "config.py").write_text("API_KEY = ''\n", encoding="utf-8")
        real_copy = updater._atomic_copy

        def simulated_sharing_violation(source, destination):
            if destination.name == "Alyssa.exe" and destination.read_bytes() == b"old-exe":
                err = PermissionError("being used by another process")
                err.winerror = 32
                raise err
            return real_copy(source, destination)

        with (
            patch.object(updater.sys, "platform", "win32"),
            patch.object(updater, "_atomic_copy", side_effect=simulated_sharing_violation),
        ):
            updater._apply_release(
                release, install, {"Alyssa.exe", "a.py", "added.py"}, version="v9.9.0"
            )

    def test_locked_file_defers_the_whole_update_without_mixing_versions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            install = root / "install"
            self._locked_exe_update(install, root / "release")

            state = updater._load_transaction_state(install / updater.TRANSACTION_DIR)
            self.assertEqual(state["phase"], "pending_restart")
            # Nothing is half-applied while the old exe keeps running.
            self.assertEqual((install / "Alyssa.exe").read_bytes(), b"old-exe")
            self.assertEqual((install / "a.py").read_text(encoding="utf-8"), "old\n")
            self.assertFalse((install / "added.py").exists())
            self.assertEqual(
                (install / "config.py").read_text(encoding="utf-8"), "API_KEY = 'mine'\n"
            )
            # A plain version check leaves the waiting update alone.
            self.assertFalse(updater._recover_incomplete_update(install))
            self.assertTrue((install / updater.TRANSACTION_DIR).is_dir())

    def test_frozen_windows_app_always_applies_after_exit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            install, release = root / "install", root / "release"
            install.mkdir()
            release.mkdir()
            (install / "a.py").write_text("old\n", encoding="utf-8")
            (release / "a.py").write_text("new\n", encoding="utf-8")
            with (
                patch.object(updater.sys, "platform", "win32"),
                patch.object(updater.sys, "frozen", True, create=True),
            ):
                updater._apply_release(release, install, {"a.py"}, version="v9.9.0")

            state = updater._load_transaction_state(install / updater.TRANSACTION_DIR)
            self.assertEqual(state["phase"], "pending_restart")
            self.assertEqual((install / "a.py").read_text(encoding="utf-8"), "old\n")

    def test_launch_pending_update_writes_helper_and_starts_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            install = root / "install"
            self._locked_exe_update(install, root / "release")
            with (
                patch.object(updater.sys, "platform", "win32"),
                patch.object(updater.subprocess, "Popen") as popen,
            ):
                self.assertTrue(updater.launch_pending_update(install, r"C:\A\Alyssa.exe"))

            command = popen.call_args.args[0]
            self.assertEqual(command[2], str(install / updater.RESTART_HELPER))
            self.assertEqual(command[4:], [str(install), r"C:\A\Alyssa.exe"])
            helper = (install / updater.RESTART_HELPER).read_text(encoding="utf-8")
            self.assertIn('robocopy "%TXN%\\new" "%INSTALL_ROOT%"', helper)
            self.assertIn("if errorlevel 8 goto failed", helper)
            # A file the update adds is removed again if applying fails.
            self.assertIn('del /f /q "%INSTALL_ROOT%\\added.py"', helper)
            state = updater._load_transaction_state(install / updater.TRANSACTION_DIR)
            self.assertEqual(state["helper_attempts"], 1)
            # Off Windows there is never a helper to start.
            self.assertFalse(updater.launch_pending_update(install))

    def test_helper_that_never_runs_is_rolled_back_instead_of_looping(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            install = root / "install"
            self._locked_exe_update(install, root / "release")
            with (
                patch.object(updater.sys, "platform", "win32"),
                patch.object(updater.subprocess, "Popen") as popen,
            ):
                for _ in range(updater.MAX_HELPER_ATTEMPTS):
                    self.assertTrue(updater.launch_pending_update(install))
                self.assertFalse(updater.launch_pending_update(install))

            self.assertEqual(popen.call_count, updater.MAX_HELPER_ATTEMPTS)
            self.assertFalse((install / updater.TRANSACTION_DIR).exists())
            self.assertEqual((install / "Alyssa.exe").read_bytes(), b"old-exe")

    def test_helper_markers_are_settled_on_next_launch(self):
        for marker in (updater.HELPER_APPLIED_MARKER, updater.HELPER_FAILED_MARKER):
            with self.subTest(marker=marker), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                install = root / "install"
                self._locked_exe_update(install, root / "release")
                transaction = install / updater.TRANSACTION_DIR
                if marker == updater.HELPER_APPLIED_MARKER:
                    shutil.copytree(transaction / "new", install, dirs_exist_ok=True)
                else:
                    # The helper copied a.py, failed on the exe, and put a.py back.
                    (install / "added.py").write_text("added\n", encoding="utf-8")
                (transaction / marker).write_text("x", encoding="utf-8")
                (install / updater.RESTART_HELPER).write_text("", encoding="utf-8")

                self.assertTrue(updater._recover_incomplete_update(install))

                self.assertFalse(transaction.exists())
                self.assertFalse((install / updater.RESTART_HELPER).exists())
                applied = marker == updater.HELPER_APPLIED_MARKER
                self.assertEqual(
                    (install / "Alyssa.exe").read_bytes(), b"new-exe" if applied else b"old-exe"
                )
                self.assertEqual((install / "added.py").exists(), applied)

    def _release_zip(self, version_text):
        archive_bytes = io.BytesIO()
        with zipfile.ZipFile(archive_bytes, "w") as zipped:
            zipped.writestr("alyssa-release/main.py", version_text)
            zipped.writestr("alyssa-release/overlay/__init__.py", "overlay\n")
            zipped.writestr("alyssa-release/config.py", "SETTING = 'default'\n")
        data = archive_bytes.getvalue()
        response = Mock()
        response.iter_content.return_value = [data]
        response.headers = {}
        response.url = FINAL_DOWNLOAD_URL
        release = {
            "latest_version": "v9.9.0",
            "archive_name": ARCHIVE_NAME,
            "download_url": DOWNLOAD_URL,
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        return response, release

    def test_stale_waiting_update_no_longer_blocks_a_new_one(self):
        # The WinError 183 bug: an older update stuck waiting for restart made
        # every later install fail on the leftover transaction folder.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            install = root / "install"
            self._locked_exe_update(install, root / "release")
            transaction = install / updater.TRANSACTION_DIR
            state = updater._load_transaction_state(transaction)
            state["version"] = "v9.8.0"
            updater._write_transaction_state(transaction, state)

            response, release = self._release_zip("new main\n")
            with patch.object(updater.requests, "get", return_value=response):
                self.assertEqual(updater.install_release(install, release), "v9.9.0")

            self.assertEqual((install / "main.py").read_text(encoding="utf-8"), "new main\n")
            self.assertEqual((install / "Alyssa.exe").read_bytes(), b"old-exe")
            self.assertEqual(updater.current_version(install), "v9.9.0")
            self.assertFalse(transaction.exists())

    def test_same_update_already_waiting_is_not_downloaded_again(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            install = root / "install"
            self._locked_exe_update(install, root / "release")
            response, release = self._release_zip("new main\n")
            with patch.object(updater.requests, "get", return_value=response) as get:
                self.assertEqual(updater.install_release(install, release), "v9.9.0")
            get.assert_not_called()
            self.assertTrue((install / updater.TRANSACTION_DIR).is_dir())

    def test_half_staged_or_committed_leftovers_are_cleaned_up(self):
        with tempfile.TemporaryDirectory() as temporary:
            install = Path(temporary).resolve()
            (install / updater.TRANSACTION_DIR / "new").mkdir(parents=True)
            (install / (updater.COMMITTED_TRANSACTION_DIR + "-1-2") / "x").mkdir(parents=True)
            self.assertTrue(updater._recover_incomplete_update(install))
            self.assertEqual(list(install.iterdir()), [])

if __name__ == "__main__":
    unittest.main()

