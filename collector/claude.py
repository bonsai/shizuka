"""Claude Code セッションログ (~/.claude/projects/**/*.jsonl) を収集"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from db import ensure_model_key, log_usage


def _projects_dir():
    return Path.home() / ".claude" / "projects"


def collect_claude(conn, days=None):
    root = _projects_dir()
    if not root.exists():
        return 0

    files = sorted(root.glob("**/*.jsonl"))
    if not files:
        return 0

    inserted = 0
    cutoff = None
    if days:
        cutoff = datetime.now(timezone.utc).timestamp() - days * 86400

    for fp in files:
        try:
            with fp.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if record.get("type") != "assistant":
                        continue

                    message = record.get("message", {})
                    usage = message.get("usage")
                    ts = record.get("timestamp", "")
                    if not usage or not ts:
                        continue

                    if cutoff:
                        try:
                            ts_epoch = datetime.fromisoformat(ts).timestamp()
                            if ts_epoch < cutoff:
                                continue
                        except ValueError:
                            pass

                    date = ts[:10]
                    model_id = message.get("model", "unknown")
                    mk = ensure_model_key(conn, "anthropic", model_id)

                    n = log_usage(
                        conn,
                        cli="claude",
                        model_key=mk,
                        task_type="",
                        tokens_in=usage.get("input_tokens", 0) or 0,
                        tokens_out=usage.get("output_tokens", 0) or 0,
                        cache_create_tokens=usage.get("cache_creation_input_tokens", 0) or 0,
                        cache_read_tokens=usage.get("cache_read_input_tokens", 0) or 0,
                        timestamp=ts,
                        source_id=f"{fp.stem}-{date}",
                    )
                    inserted += n
        except OSError as e:
            print(f"  [WARN] {fp}: {e}", file=sys.stderr)

    if inserted:
        print(f"  claude: {inserted} records inserted", file=sys.stderr)
    return inserted
