"""Uninstall button backend and the release-size trim (v1.10.0)."""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import updater

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_exe  # noqa: E402


def test_uninstall_refuses_outside_the_installed_app(tmp_path):
    with pytest.raises(RuntimeError, match="installed Windows app"):
        updater.uninstall(tmp_path)  # tests run from source, not frozen


def test_uninstall_refuses_a_folder_that_isnt_alyssa(tmp_path):
    with patch.object(updater.sys, "platform", "win32"), patch.object(updater.sys, "frozen", True, create=True):
        with pytest.raises(RuntimeError, match="doesn't look like"):
            updater.uninstall(tmp_path)


def test_uninstall_script_opens_the_uninstaller_then_removes_everything(tmp_path):
    (tmp_path / "unins000.exe").write_bytes(b"")
    script = updater._uninstall_script(4242, tmp_path)
    assert 'PID eq 4242' in script
    assert "timeout" not in script  # fails instantly without a console window
    assert "taskkill /f /t /im Alyssa.exe" in script
    # The normal uninstall window, not a silent one, and leftovers go only after it finished.
    assert f'start "" "{tmp_path / "unins000.exe"}"' in script
    assert "/VERYSILENT" not in script
    assert script.index('goto remove_rest') < script.index(f'rmdir /s /q "{tmp_path}"')


def test_portable_uninstall_removes_the_folder_without_an_uninstaller(tmp_path):
    script = updater._uninstall_script(4242, tmp_path)
    assert "start" not in script.split(":remove_rest")[0].split(":closed")[1]
    assert f'rmdir /s /q "{tmp_path}"' in script


def test_uninstall_cleanup_clears_secrets_and_speech_models(tmp_path):
    cache = tmp_path / "models--Systran--faster-whisper-base.en"
    cache.mkdir()
    with (
        patch.object(updater, "_whisper_cache_dirs", return_value=[cache]),
        patch("credential_store.clear_secret") as clear,
    ):
        updater.uninstall_cleanup()
    assert not cache.exists()
    cleared = {call.args[0] for call in clear.call_args_list}
    assert "GOOGLE_CALENDAR_GMAIL_OAUTH_TOKEN" in cleared and "OPENAI_API_KEY" in cleared


def test_installer_uninstall_removes_her_whole_folder():
    iss = (Path(__file__).resolve().parents[1] / "installer" / "alyssa.iss").read_text(encoding="utf-8")
    assert 'Type: filesandordirs; Name: "{app}"' in iss
    assert "--uninstall-cleanup" in iss


def test_trim_bundle_removes_only_optional_files(tmp_path):
    keep = tmp_path / "_internal/PySide6/Qt6Core.dll"
    drop = tmp_path / "_internal/PySide6/opengl32sw.dll"
    drop_glob = tmp_path / "_internal/cv2/opencv_videoio_ffmpeg500_64.dll"
    for f in (keep, drop, drop_glob):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"x")
    with patch.object(build_exe, "OUTPUT_FOLDER", tmp_path):
        build_exe._trim_bundle()
    assert keep.exists() and not drop.exists() and not drop_glob.exists()


def test_update_limits_fit_the_current_release():
    assert updater.MAX_EXTRACTED_BYTES > 560 * 1024 * 1024


def test_build_bundles_whisper_vad_model():
    spec = (Path(__file__).resolve().parents[1] / "alyssa.spec").read_text(encoding="utf-8")
    assert '"faster_whisper"' in spec


def test_local_whisper_finds_its_vad_model_from_source():
    import transcribe
    assert transcribe._WHISPER_VAD_AVAILABLE
