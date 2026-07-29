"""Qwen CLI 使用量ログ (~/.qwen/usage_record.jsonl) を収集"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from db import ensure_model_key, log_usage


def collect_qwen(conn, days=None):
    path = Path.home() / ".qwen" / "usage_record.jsonl"
    if not path.exists():
        return 0

    inserted = 0
    cutoff = None
    if days:
        cutoff = datetime.now(timezone.utc).timestamp() - days * 86400

    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                ts_epoch_ms = record.get("timestamp", 0)
                if not ts_epoch_ms:
                    continue

                if cutoff and ts_epoch_ms / 1000 < cutoff:
                    continue

                ts_dt = datetime.fromtimestamp(ts_epoch_ms / 1000, tz=timezone.utc)
                ts = ts_dt.strftime("%Y-%m-%dT%H:%M:%S")

                models = record.get("models", {})
                if not models:
                    continue

                session_id = record.get("sessionId", "")

                for model_name, data in models.items():
                    t_in = data.get("inputTokens", 0) or 0
                    t_out = data.get("outputTokens", 0) or 0
                    t_cache = data.get("cachedTokens", 0) or 0

                    if t_in == 0 and t_out == 0 and t_cache == 0:
                        continue

                    mk = ensure_model_key(conn, "dashscope", model_name)
                    n = log_usage(
                        conn,
                        cli="qwen",
                        model_key=mk,
                        task_type="",
                        tokens_in=t_in,
                        tokens_out=t_out,
                        cache_read_tokens=t_cache,
                        timestamp=ts,
                        source_id=f"{session_id}-{model_name}",
                    )
                    inserted += n
    except OSError as e:
        print(f"  [WARN] qwen: {e}", file=sys.stderr)

    if inserted:
        print(f"  qwen: {inserted} records inserted", file=sys.stderr)
    return inserted
