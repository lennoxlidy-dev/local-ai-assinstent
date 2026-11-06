"""Start the real tray app (offscreen Qt) in a subprocess."""

import os
import subprocess
import sys
import time
from pathlib import Path

SRC = str(Path(__file__).resolve().parents[1] / "src")


def _env(home):
    env = dict(os.environ, ASSISTANT_HOME=str(home), QT_QPA_PLATFORM="offscreen")
    env["PYTHONPATH"] = SRC + os.pathsep + env.get("PYTHONPATH", "")
    return env


def test_tray_smoke(home):
    res = subprocess.run([sys.executable, "-m", "assistant", "--smoke-test", "1"], env=_env(home),
                         capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    log = (home / "logs" / "assistant.log").read_text(encoding="utf-8")
    assert "tray ready" in log and "exit code 0" in log
    assert "Traceback" not in log
    crash = home / "logs" / "crash.log"
    assert not crash.exists() or crash.read_text(encoding="utf-8") == ""


def test_second_instance_hands_over(home):
    first = subprocess.Popen([sys.executable, "-m", "assistant", "--smoke-test", "6"], env=_env(home),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        log = home / "logs" / "assistant.log"
        deadline = time.time() + 20
        while time.time() < deadline and not (log.exists() and "tray ready" in log.read_text(encoding="utf-8")):
            time.sleep(0.1)
        res = subprocess.run([sys.executable, "-m", "assistant"], env=_env(home),
                             capture_output=True, text=True, timeout=30)
        assert res.returncode == 0
        deadline = time.time() + 5
        while time.time() < deadline and "message from second instance: show" not in log.read_text(encoding="utf-8"):
            time.sleep(0.1)
        text = log.read_text(encoding="utf-8")
        assert "message from second instance: show" in text
        assert "asked it to show settings" in text
    finally:
        first.wait(timeout=30)
    assert first.returncode == 0
