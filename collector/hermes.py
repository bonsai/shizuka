"""Hermes CLI 使用量ログ (~/.hermes/state.db) を収集"""

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from db import ensure_model_key, log_usage


def _db_path():
    return Path.home() / ".hermes" / "state.db"


def _parse_model(model_raw):
    if not model_raw:
        return "hermes", "unknown"
    if "/" in model_raw:
        parts = model_raw.split("/", 1)
        return parts[0], parts[1]
    return "hermes", model_raw


def collect_hermes(conn, days=None):
    db_path = _db_path()
    if not db_path.exists():
        return 0

    cutoff = None
    if days:
        cutoff = datetime.now(timezone.utc).timestamp() - days * 86400

    try:
        src = sqlite3.connect(str(db_path))
        src.row_factory = sqlite3.Row
    except sqlite3.Error as e:
        print(f"  [WARN] hermes db: {e}", file=sys.stderr)
        return 0

    query = "SELECT * FROM sessions WHERE input_tokens > 0 OR output_tokens > 0"
    params = []
    if cutoff:
        query += " AND started_at >= ?"
        params.append(cutoff)
    query += " ORDER BY started_at"

    inserted = 0
    try:
        for row in src.execute(query, params):
            ts_epoch = row["started_at"]
            ts = datetime.fromtimestamp(ts_epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
            model_raw = row["model"] or ""
            provider, model_id = _parse_model(model_raw)
            mk = ensure_model_key(conn, provider, model_id)

            cost = row["actual_cost_usd"] if row["actual_cost_usd"] else (row["estimated_cost_usd"] or 0)

            n = log_usage(
                conn,
                cli="hermes",
                model_key=mk,
                task_type="",
                tokens_in=row["input_tokens"] or 0,
                tokens_out=row["output_tokens"] or 0,
                cache_create_tokens=row["cache_write_tokens"] or 0,
                cache_read_tokens=row["cache_read_tokens"] or 0,
                cost=cost or 0,
                timestamp=ts,
                source_id=row["id"],
            )
            inserted += n
    except sqlite3.Error as e:
        print(f"  [WARN] hermes query: {e}", file=sys.stderr)
    finally:
        src.close()

    if inserted:
        print(f"  hermes: {inserted} records inserted", file=sys.stderr)
    return inserted
