"""collector — 各 CLI の使用量ログを収集し usage_log に取り込む"""

from .claude import collect_claude
from .qwen import collect_qwen
from .opencode import collect_opencode
from .hermes import collect_hermes

COLLECTORS = [
    collect_claude,
    collect_qwen,
    collect_opencode,
    collect_hermes,
]


def collect_all(conn, days=None):
    total = 0
    for fn in COLLECTORS:
        try:
            n = fn(conn, days=days)
            if n:
                total += n
        except Exception as e:
            print(f"  [WARN] {fn.__name__}: {e}")
    return total
