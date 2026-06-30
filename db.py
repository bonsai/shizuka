"""db.py — 統合 DB 操作 (unified models.db schema)"""
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

APPDATA = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
DB_DIR = APPDATA / "model-manager"
DB_PATH = DB_DIR / "models.db"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS providers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    model_key   TEXT NOT NULL UNIQUE,
    provider    TEXT NOT NULL,
    model_id    TEXT NOT NULL,
    tier        TEXT NOT NULL DEFAULT 'free',
    cost_in     REAL DEFAULT 0,
    quality_low  INTEGER DEFAULT 5,
    quality_mid  INTEGER DEFAULT 5,
    quality_high INTEGER DEFAULT 5,
    quality_code INTEGER DEFAULT 5,
    priority    INTEGER DEFAULT 999,
    note        TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS cli_priority (
    cli         TEXT NOT NULL,
    priority    INTEGER NOT NULL,
    model_key   TEXT NOT NULL REFERENCES providers(model_key),
    use_rotation INTEGER DEFAULT 0,
    PRIMARY KEY (cli, priority)
);

CREATE TABLE IF NOT EXISTS quota (
    provider    TEXT NOT NULL,
    model_key   TEXT DEFAULT '',
    remaining   REAL DEFAULT 100.0,
    tokens_remaining INTEGER DEFAULT -1,
    tokens_limit     INTEGER DEFAULT -1,
    checked_at  TEXT NOT NULL,
    PRIMARY KEY (provider, model_key)
);

CREATE TABLE IF NOT EXISTS rotation_state (
    model_key   TEXT NOT NULL UNIQUE REFERENCES providers(model_key),
    sort_order  INTEGER NOT NULL,
    expired     INTEGER DEFAULT 0,
    expires_at  TEXT DEFAULT '',
    last_error  TEXT DEFAULT '',
    updated_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS usage_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    cli         TEXT NOT NULL,
    model_key   TEXT NOT NULL REFERENCES providers(model_key),
    task_type   TEXT DEFAULT '',
    tokens_in   INTEGER DEFAULT 0,
    tokens_out  INTEGER DEFAULT 0,
    cost        REAL DEFAULT 0,
    latency_ms  INTEGER DEFAULT 0,
    success     INTEGER DEFAULT 1,
    error_msg   TEXT DEFAULT '',
    timestamp   TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS recovery_estimate (
    model_key   TEXT NOT NULL UNIQUE REFERENCES providers(model_key),
    estimated_at   TEXT,
    confidence     REAL DEFAULT 0.5,
    based_on       TEXT DEFAULT '',
    cached_remaining REAL DEFAULT 0,
    updated_at     TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ml_params (
    param_key   TEXT PRIMARY KEY,
    param_value REAL NOT NULL,
    updated_at  TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_usage_model ON usage_log(model_key, timestamp);
CREATE INDEX IF NOT EXISTS idx_usage_cli ON usage_log(cli, timestamp);
CREATE INDEX IF NOT EXISTS idx_usage_task ON usage_log(task_type);
"""


def get_db() -> sqlite3.Connection:
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    # auto-schema
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


# ── providers ──────────────────────────────────────────────────────────────

def list_providers(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("""
        SELECT p.*, q.remaining, r.expired, r.expires_at,
               rec.estimated_at, rec.confidence
        FROM providers p
        LEFT JOIN quota q ON q.model_key = p.model_key
        LEFT JOIN rotation_state r ON r.model_key = p.model_key
        LEFT JOIN recovery_estimate rec ON rec.model_key = p.model_key
        ORDER BY p.priority
    """).fetchall()


def get_provider(conn: sqlite3.Connection, model_key: str) -> Optional[sqlite3.Row]:
    rows = conn.execute(
        "SELECT * FROM providers WHERE model_key=?", (model_key,)
    ).fetchone()
    return rows


def upsert_provider(conn: sqlite3.Connection, model_key: str, provider: str,
                    model_id: str, tier: str = "free", cost_in: float = 0,
                    quality_low: int = 5, quality_mid: int = 5,
                    quality_high: int = 5, quality_code: int = 5,
                    priority: int = 999, note: str = ""):
    conn.execute(
        """INSERT OR REPLACE INTO providers
           (model_key, provider, model_id, tier, cost_in,
            quality_low, quality_mid, quality_high, quality_code, priority, note)
           VALUES (?,?,?,?,?, ?,?,?,?, ?,?)""",
        (model_key, provider, model_id, tier, cost_in,
         quality_low, quality_mid, quality_high, quality_code, priority, note),
    )
    conn.commit()


def delete_provider(conn: sqlite3.Connection, model_key: str):
    conn.execute("DELETE FROM providers WHERE model_key=?", (model_key,))
    conn.commit()


# ── cli_priority ───────────────────────────────────────────────────────────

def get_cli_priorities(conn: sqlite3.Connection, cli: str) -> list[sqlite3.Row]:
    return conn.execute("""
        SELECT cp.priority, cp.model_key, cp.use_rotation,
               p.provider, p.model_id, p.cost_in, p.tier,
               p.quality_low, p.quality_mid, p.quality_high, p.quality_code,
               q.remaining, r.expired
        FROM cli_priority cp
        JOIN providers p ON p.model_key = cp.model_key
        LEFT JOIN quota q ON q.model_key = cp.model_key
        LEFT JOIN rotation_state r ON r.model_key = cp.model_key
        WHERE cp.cli = ?
        ORDER BY cp.priority
    """, (cli,)).fetchall()


def set_cli_priority(conn: sqlite3.Connection, cli: str, priority: int,
                     model_key: str, use_rotation: int = 0):
    conn.execute(
        "INSERT OR REPLACE INTO cli_priority (cli, priority, model_key, use_rotation) VALUES (?,?,?,?)",
        (cli, priority, model_key, use_rotation),
    )
    conn.commit()


def delete_cli_priority(conn: sqlite3.Connection, cli: str, priority: int):
    conn.execute(
        "DELETE FROM cli_priority WHERE cli=? AND priority=?",
        (cli, priority),
    )
    conn.commit()


# ── usage_log ──────────────────────────────────────────────────────────────

def log_usage(conn: sqlite3.Connection, cli: str, model_key: str,
              task_type: str = "", tokens_in: int = 0, tokens_out: int = 0,
              cost: float = 0, latency_ms: int = 0, success: bool = True,
              error_msg: str = ""):
    conn.execute(
        """INSERT INTO usage_log
           (cli, model_key, task_type, tokens_in, tokens_out,
            cost, latency_ms, success, error_msg)
           VALUES (?,?,?,?,?, ?,?,?,?)""",
        (cli, model_key, task_type, tokens_in, tokens_out,
         cost, latency_ms, 1 if success else 0, error_msg),
    )
    conn.commit()
    _update_recovery_estimate(conn, model_key)


def get_usage(conn: sqlite3.Connection, days: int = 7, cli: str = None) -> list[sqlite3.Row]:
    where = "timestamp > datetime('now', ?)"
    params = [f"-{days} days"]
    if cli:
        where += " AND cli=?"
        params.append(cli)
    return conn.execute(f"""
        SELECT model_key, COUNT(*) as calls,
               SUM(tokens_in) as t_in, SUM(tokens_out) as t_out,
               SUM(cost) as total_cost,
               AVG(latency_ms) as avg_lat,
               SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) as errors
        FROM usage_log WHERE {where}
        GROUP BY model_key ORDER BY total_cost DESC
    """, params).fetchall()


def get_daily_usage(conn: sqlite3.Connection, days: int = 14) -> list[sqlite3.Row]:
    return conn.execute("""
        SELECT date(timestamp) AS day, SUM(tokens_in) as t_in,
               SUM(tokens_out) as t_out
        FROM usage_log
        WHERE date(timestamp) >= date('now', ?)
        GROUP BY day ORDER BY day
    """, [f"-{days} days"]).fetchall()


def get_cli_totals(conn: sqlite3.Connection, days: int = 14) -> list[sqlite3.Row]:
    return conn.execute("""
        SELECT cli, SUM(tokens_in) as t_in, SUM(tokens_out) as t_out
        FROM usage_log
        WHERE date(timestamp) >= date('now', ?)
        GROUP BY cli ORDER BY SUM(tokens_in + tokens_out) DESC
    """, [f"-{days} days"]).fetchall()


def get_provider_totals(conn: sqlite3.Connection, days: int = 14) -> list[sqlite3.Row]:
    return conn.execute("""
        SELECT p.provider, SUM(u.tokens_in) as t_in, SUM(u.tokens_out) as t_out
        FROM usage_log u
        JOIN providers p ON p.model_key = u.model_key
        WHERE date(u.timestamp) >= date('now', ?)
        GROUP BY p.provider ORDER BY SUM(u.tokens_in + u.tokens_out) DESC
    """, [f"-{days} days"]).fetchall()


# ── rotation_state ─────────────────────────────────────────────────────────

def get_rotation_order(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("""
        SELECT r.*, p.provider, p.model_id
        FROM rotation_state r
        JOIN providers p ON p.model_key = r.model_key
        ORDER BY r.sort_order
    """).fetchall()


def current_rotation(conn: sqlite3.Connection, provider: str = "dashscope") -> Optional[str]:
    row = conn.execute("""
        SELECT r.model_key, p.model_id FROM rotation_state r
        JOIN providers p ON p.model_key = r.model_key
        WHERE r.expired = 0 AND p.provider = ?
        ORDER BY r.sort_order LIMIT 1
    """, (provider,)).fetchone()
    if row:
        return row["model_id"]
    return None


def next_rotation(conn: sqlite3.Connection, current_model_id: str) -> Optional[str]:
    row = conn.execute("""
        SELECT r2.model_id FROM rotation_state r1
        JOIN rotation_state r2 ON r2.sort_order > r1.sort_order AND r2.expired = 0
        JOIN providers p1 ON p1.model_key = r1.model_key
        JOIN providers p2 ON p2.model_key = r2.model_key AND p2.provider = p1.provider
        WHERE r1.model_id = ?
        ORDER BY r2.sort_order LIMIT 1
    """, (current_model_id,)).fetchone()
    if row:
        return row["model_id"]
    return current_rotation(conn)


def mark_exhausted(conn: sqlite3.Connection, model_key: str,
                   reason: str = "", recovery_hours: int = 24):
    expires = (datetime.now(timezone.utc) + timedelta(hours=recovery_hours)).isoformat()
    conn.execute(
        """INSERT OR REPLACE INTO rotation_state
           (model_key, sort_order, expired, expires_at, last_error, updated_at)
           VALUES (
               ?, COALESCE((SELECT sort_order FROM rotation_state WHERE model_key=?), 0),
               1, ?, ?, datetime('now')
           )""",
        (model_key, model_key, expires, reason),
    )
    conn.commit()


def set_quota(conn: sqlite3.Connection, provider: str, model_key: str,
              remaining_pct: float, tokens_remaining: int = -1,
              tokens_limit: int = -1):
    conn.execute(
        """INSERT OR REPLACE INTO quota
           (provider, model_key, remaining, tokens_remaining, tokens_limit, checked_at)
           VALUES (?,?,?,?,?, datetime('now'))""",
        (provider, model_key, remaining_pct, tokens_remaining, tokens_limit),
    )
    conn.commit()


# ── recovery_estimate ──────────────────────────────────────────────────────

def _update_recovery_estimate(conn: sqlite3.Connection, model_key: str):
    row = conn.execute("""
        SELECT COUNT(*) as cnt,
               AVG(CASE WHEN success=1 THEN 1.0 ELSE 0 END) as success_rate,
               MAX(timestamp) as last_ts
        FROM usage_log WHERE model_key=?
        AND timestamp > datetime('now', '-7 days')
    """, (model_key,)).fetchone()
    if not row or row["cnt"] == 0:
        return
    if row["success_rate"] is not None and row["success_rate"] < 0.3 and row["cnt"] >= 3:
        est = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
        conn.execute(
            """INSERT OR REPLACE INTO recovery_estimate
               (model_key, estimated_at, confidence, based_on, cached_remaining, updated_at)
               VALUES (?,?,?,?,?, datetime('now'))""",
            (model_key, est, 0.3, "pattern", 0),
        )


def get_recovery_estimates(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("""
        SELECT re.*, p.provider FROM recovery_estimate re
        JOIN providers p ON p.model_key = re.model_key
        WHERE re.estimated_at > datetime('now')
        ORDER BY re.estimated_at
    """).fetchall()


# ── ml_params ──────────────────────────────────────────────────────────────

def get_ml_param(conn: sqlite3.Connection, key: str) -> Optional[float]:
    row = conn.execute(
        "SELECT param_value FROM ml_params WHERE param_key=?", (key,)
    ).fetchone()
    return row["param_value"] if row else None


def set_ml_param(conn: sqlite3.Connection, key: str, value: float):
    conn.execute(
        "INSERT OR REPLACE INTO ml_params (param_key, param_value, updated_at) VALUES (?,?,datetime('now'))",
        (key, value),
    )
    conn.commit()


# ── helpers ────────────────────────────────────────────────────────────────

def ensure_model_key(conn: sqlite3.Connection, provider: str, model_id: str) -> str:
    mk = f"{provider}-{model_id}".replace("/", "-").replace(":", "-").replace(".", "-").lower()
    existing = conn.execute("SELECT model_key FROM providers WHERE model_key=?", (mk,)).fetchone()
    if existing:
        return mk
    tier = "free"
    upsert_provider(conn, mk, provider, model_id, tier)
    return mk
