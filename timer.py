"""timer.py — mito 連携レイヤー（yotei.sqlite を直接参照）"""

import subprocess
from pathlib import Path

MITO_DIR = Path.home() / ".hermes" / "mito"
MITO_DB = MITO_DIR / "yotei.sqlite"
MITO_CLI = MITO_DIR / "timer.py"


def _mito(*args):
    try:
        r = subprocess.run(
            ["python3", str(MITO_CLI), *args],
            capture_output=True, text=True, timeout=30,
        )
        return r.stdout.strip()
    except FileNotFoundError:
        return "mito not found"
    except subprocess.TimeoutExpired:
        return "timeout"


def plan(task: str, minutes: int):
    return _mito(task, str(minutes))


def status():
    return _mito()


def done():
    return _mito("done")


def cancel():
    return _mito("cancel")


def history(days=7):
    return _mito(str(days))


def pomodoro(task: str = ""):
    return _mito("pomodoro", task)


def pbreak():
    return _mito("pbreak")
