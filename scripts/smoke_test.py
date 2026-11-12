"""Startup smoke test: launch Alyssa, let her run, fail on a crash or traceback.

Runs with no API keys, so she boots the GUI, loads plugins and settles in
"needs a key" standby. That covers imports, plugin loading and the window
setup, which is where most "it broke everything" bugs show up.

Usage: python scripts/smoke_test.py [seconds]   (default 60)
"""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEY_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ELEVENLABS_API_KEY")


def main() -> int:
    seconds = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    log_dir = Path(tempfile.mkdtemp(prefix="alyssa-smoke-"))
    env = {k: v for k, v in os.environ.items() if k not in KEY_VARS}
    env.update(ALYSSA_LOG_DIR=str(log_dir), QT_QPA_PLATFORM="offscreen", PYTHONUNBUFFERED="1")

    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "main.py")],
        cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace",
    )
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and proc.poll() is None:
        time.sleep(1)
    exited = proc.poll()
    if exited is None:
        proc.kill()
    output = proc.communicate()[0]
    log_file = log_dir / "alyssa.log"
    log = log_file.read_text(encoding="utf-8", errors="replace") if log_file.exists() else ""

    print(output)
    problems = []
    if exited is not None:
        problems.append(f"Alyssa exited after less than {seconds}s (code {exited}).")
    if "Traceback (most recent call last)" in output + log:
        problems.append("A Python traceback was printed during startup.")
    if "Entering Alyssa application startup." not in output + log:
        problems.append("Startup never reached main().")
    for problem in problems:
        print(f"SMOKE TEST FAILED: {problem}")
    if not problems:
        print(f"Smoke test passed: Alyssa ran {seconds}s without crashing.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
