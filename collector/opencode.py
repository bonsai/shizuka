"""opencode 使用量ログ (~/.local/share/opencode/opencode.db) を収集"""

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from db import ensure_model_key, log_usage


def _db_path():
    return Path.home() / ".local" / "share" / "opencode" / "opencode.db"


def _parse_model(model_raw):
    if not model_raw:
        return "opencode", "unknown"
    if isinstance(model_raw, str) and model_raw.startswith("{"):
        try:
            obj = json.loads(model_raw)
            provider = obj.get("providerID", "opencode")
            model_id = obj.get("id", "unknown")
            return provider, model_id
        except json.JSONDecodeError:
            pass
    return "opencode", str(model_raw)


def collect_opencode(conn, days=None):
    db_path = _db_path()
    if not db_path.exists():
        return 0

    cutoff_ms = None
    if days:
        cutoff_ms = int((datetime.now(timezone.utc).timestamp() - days * 86400) * 1000)

    try:
        src = sqlite3.connect(str(db_path))
        src.row_factory = sqlite3.Row
    except sqlite3.Error as e:
        print(f"  [WARN] opencode db: {e}", file=sys.stderr)
        return 0

    query = "SELECT * FROM session WHERE tokens_input > 0 OR tokens_output > 0"
    params = []
    if cutoff_ms:
        query += " AND time_created >= ?"
        params.append(cutoff_ms)
    query += " ORDER BY time_created"

    inserted = 0
    try:
        for row in src.execute(query, params):
            ts_epoch_ms = row["time_created"]
            ts = datetime.fromtimestamp(ts_epoch_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
            model_raw = row["model"]
            provider, model_id = _parse_model(model_raw)
            mk = ensure_model_key(conn, provider, model_id)

            n = log_usage(
                conn,
                cli="opencode",
                model_key=mk,
                task_type="",
                tokens_in=row["tokens_input"] or 0,
                tokens_out=row["tokens_output"] or 0,
                cache_create_tokens=row["tokens_cache_write"] or 0,
                cache_read_tokens=row["tokens_cache_read"] or 0,
                cost=row["cost"] or 0,
                timestamp=ts,
                source_id=row["id"],
            )
            inserted += n
    except sqlite3.Error as e:
        print(f"  [WARN] opencode query: {e}", file=sys.stderr)
    finally:
        src.close()

    if inserted:
        print(f"  opencode: {inserted} records inserted", file=sys.stderr)
    return inserted
