from pathlib import Path
import os
import subprocess
import sys


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
