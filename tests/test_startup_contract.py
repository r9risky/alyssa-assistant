from pathlib import Path
import os
import subprocess
import sys

import startup_logging


ROOT = Path(__file__).resolve().parents[1]


def test_startup_launch_has_console_working_directory_and_delay():
    installer = (ROOT / "scripts" / "install_startup.bat").read_text(encoding="utf-8")
    config = (ROOT / "config.py").read_text(encoding="utf-8")
    main = (ROOT / "main.py").read_text(encoding="utf-8")

    assert r".venv\Scripts\python.exe" in installer
    assert "pythonw.exe" not in installer
    assert "-WorkingDirectory '%PROJECT_DIR%'" in installer
    assert "Delay = 'PT15S'" in installer
    assert "HIDE_CONSOLE_WINDOW = False" in config
    assert "os.chdir(_config_dir)" in main

def test_log_redaction_scrubs_key_and_token_shapes(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "literal-config-secret")
    sample = (
        "api_key=sk-test-1234567890 "
        "token=token_fake_abcdefghijk "
        "configured=literal-config-secret"
    )

    redacted = startup_logging.redact_sensitive_text(sample)

    assert "sk-test-1234567890" not in redacted
    assert "token_fake_abcdefghijk" not in redacted
    assert "literal-config-secret" not in redacted
    assert redacted.count("[REDACTED]") == 3


def test_content_logging_is_metadata_only_without_explicit_opt_in(monkeypatch):
    monkeypatch.delenv(startup_logging.FULL_CONTENT_LOG_ENV, raising=False)
    content = "dictated-password"

    metadata = startup_logging.format_content_log("response received", content)

    assert content not in metadata
    assert metadata == f"response received, {len(content)} chars"

    monkeypatch.setenv(startup_logging.FULL_CONTENT_LOG_ENV, "true")
    assert startup_logging.format_content_log(
        "response received", content
    ) == content



def test_startup_output_is_written_to_a_file(tmp_path):
    env = os.environ.copy()
    env["ALYSSA_LOG_DIR"] = str(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import startup_logging, sys; startup_logging.configure(r'%s'); "
            "print('startup-log-test'); sys.stdout.flush()" % ROOT,
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "startup-log-test" in (tmp_path / "alyssa.log").read_text(encoding="utf-8")
