"""migration.py - DB: models.db + model-status.db -> unifed models.db

Usage:
  python migration.py          # preview
  python migration.py --apply  # execute merge
"""
import argparse
import os
import sqlite3
import sys
from pathlib import Path

APPDATA = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
DB_DIR = APPDATA / "model-manager"
OLD_GO_DB = DB_DIR / "models.db.go"    # Go shizuka DB (renamed before merge)
OLD_PY_DB = DB_DIR / "model-status.db" # Python selector DB
NEW_DB    = DB_DIR / "models.db"       # unified target
BACKUP    = DB_DIR / "models.db.bak"

# On first run, rename models.db -> models.db.go if it's the Go format
_GO_INITIAL = DB_DIR / "models.db"

MODELS_MD = Path(os.environ.get("MEGA", Path.home() / "Documents" / "MEGA")) / "models.agents.md"


def unified_schema(conn: sqlite3.Connection):
    conn.executescript("""
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
    """)
    conn.commit()


def migrate_from_go_db(go_conn: sqlite3.Connection, new_conn: sqlite3.Connection):
    """Migrate data from Go shizuka models.db into unified schema."""
    go_provs = go_conn.execute("""
        SELECT cli, provider, model, priority, note, use_rotation,
               cost_in, quality_low, quality_mid, quality_high, quality_code
        FROM providers ORDER BY cli, priority
    """).fetchall()

    model_key_map = {}  # (provider, model_id) -> model_key
    cli_pri_rows = []

    for row in go_provs:
        cli, prov, model, pri, note, use_rot, cost, ql, qm, qh, qc = row
        # Generate a stable model_key
        mid = model if model else "default"
        mk = f"{prov}-{mid}".replace("/", "-").replace(":", "-").replace(".", "-").lower()
        # Deduplicate
        counter = 1
        orig_mk = mk
        while mk in model_key_map and model_key_map[mk] != (prov, mid):
            mk = f"{orig_mk}-{counter}"
            counter += 1

        if mk not in model_key_map:
            model_key_map[mk] = (prov, mid)
            tier = "free" if cost == 0 else "paid"
            # Determine priority from model-status style: find matching entry
            pri_val = pri if pri else 999
            new_conn.execute(
                """INSERT OR IGNORE INTO providers
                   (model_key, provider, model_id, tier, cost_in,
                    quality_low, quality_mid, quality_high, quality_code, priority, note)
                   VALUES (?,?,?,?,?, ?,?,?,?, ?,?)""",
                (mk, prov, mid, tier, cost,
                 ql, qm, qh, qc, pri_val, note or ""),
            )

        cli_pri_rows.append((cli, pri, mk, use_rot))

    # Write cli_priority
    for cli, pri, mk, use_rot in cli_pri_rows:
        new_conn.execute(
            "INSERT OR IGNORE INTO cli_priority (cli, priority, model_key, use_rotation) VALUES (?,?,?,?)",
            (cli, pri, mk, use_rot),
        )

    # qwen_rotation -> rotation_state
    qwen_rot = go_conn.execute(
        "SELECT model_id, expires, exhausted, sort_order FROM qwen_rotation ORDER BY sort_order"
    ).fetchall()
    for mid, expires, exhausted, sort_order in qwen_rot:
        mk = f"dashscope-{mid}".replace("/", "-").replace(":", "-").lower()
        new_conn.execute(
            """INSERT OR IGNORE INTO rotation_state
               (model_key, sort_order, expired, expires_at) VALUES (?,?,?,?)""",
            (mk, sort_order, exhausted, expires or ""),
        )

    # quota
    go_quota = go_conn.execute(
        "SELECT provider, model_id, remaining, checked_at FROM quota"
    ).fetchall()
    for prov, mid, remaining, checked_at in go_quota:
        mk = f"{prov}-{mid}".replace("/", "-").replace(":", "-").lower() if mid else f"{prov}-default"
        new_conn.execute(
            """INSERT OR IGNORE INTO quota
               (provider, model_key, remaining, checked_at) VALUES (?,?,?,?)""",
            (prov, mk, remaining, checked_at),
        )

    # usage_log
    go_usage = go_conn.execute(
        "SELECT cli, provider, model, task, tokens_in, tokens_out, logged_at FROM usage_log"
    ).fetchall()
    for cli, prov, model, task, tin, tout, logged_at in go_usage:
        mid = model if model else "default"
        mk = f"{prov}-{mid}".replace("/", "-").replace(":", "-").lower()
        new_conn.execute(
            """INSERT INTO usage_log
               (cli, model_key, task_type, tokens_in, tokens_out, timestamp)
               VALUES (?,?,?,?,?,?)""",
            (cli, mk, task or "", tin, tout, logged_at),
        )

    new_conn.commit()


def migrate_from_py_db(py_conn: sqlite3.Connection, new_conn: sqlite3.Connection):
    """Migrate data from Python selector model-status.db into unified schema."""
    # providers
    py_provs = py_conn.execute("""
        SELECT model_key, provider, model_id, tier, cost_in,
               quality_low, quality_mid, quality_high, quality_code,
               priority, note
        FROM providers ORDER BY priority
    """).fetchall()
    for row in py_provs:
        new_conn.execute(
            """INSERT OR IGNORE INTO providers
               (model_key, provider, model_id, tier, cost_in,
                quality_low, quality_mid, quality_high, quality_code, priority, note)
               VALUES (?,?,?,?,?, ?,?,?,?, ?,?)""",
            row,
        )

    # cli_priority
    py_cli = py_conn.execute(
        "SELECT cli, priority, model_key FROM cli_priority ORDER BY cli, priority"
    ).fetchall()
    for cli, pri, mk in py_cli:
        new_conn.execute(
            "INSERT OR IGNORE INTO cli_priority (cli, priority, model_key) VALUES (?,?,?)",
            (cli, pri, mk),
        )

    # quota
    py_quota = py_conn.execute(
        "SELECT provider, model_key, remaining, tokens_remaining, tokens_limit, checked_at FROM quota"
    ).fetchall()
    for prov, mk, remaining, tr, tl, checked_at in py_quota:
        new_conn.execute(
            """INSERT OR IGNORE INTO quota
               (provider, model_key, remaining, tokens_remaining, tokens_limit, checked_at)
               VALUES (?,?,?,?,?,?)""",
            (prov, mk, remaining, tr, tl, checked_at),
        )

    # rotation_state
    py_rot = py_conn.execute(
        "SELECT model_key, sort_order, expired, expires_at, last_error, updated_at FROM rotation_state"
    ).fetchall()
    for mk, sort_order, expired, expires_at, last_error, updated_at in py_rot:
        new_conn.execute(
            """INSERT OR IGNORE INTO rotation_state
               (model_key, sort_order, expired, expires_at, last_error, updated_at)
               VALUES (?,?,?,?,?,?)""",
            (mk, sort_order, expired, expires_at, last_error, updated_at),
        )

    # usage_log
    py_usage = py_conn.execute(
        """SELECT cli, model_key, task_type, tokens_in, tokens_out,
                  cost, latency_ms, success, error_msg, timestamp
           FROM usage_log"""
    ).fetchall()
    for row in py_usage:
        new_conn.execute(
            """INSERT INTO usage_log
               (cli, model_key, task_type, tokens_in, tokens_out,
                cost, latency_ms, success, error_msg, timestamp)
               VALUES (?,?,?,?,?, ?,?,?,?,?)""",
            row,
        )

    # recovery_estimate
    py_rec = py_conn.execute(
        "SELECT model_key, estimated_at, confidence, based_on, cached_remaining, updated_at FROM recovery_estimate"
    ).fetchall()
    for row in py_rec:
        new_conn.execute(
            """INSERT OR IGNORE INTO recovery_estimate
               (model_key, estimated_at, confidence, based_on, cached_remaining, updated_at)
               VALUES (?,?,?,?,?,?)""",
            row,
        )

    # ml_params
    py_ml = py_conn.execute(
        "SELECT param_key, param_value, updated_at FROM ml_params"
    ).fetchall()
    for key, val, updated_at in py_ml:
        new_conn.execute(
            "INSERT OR IGNORE INTO ml_params (param_key, param_value, updated_at) VALUES (?,?,?)",
            (key, val, updated_at),
        )

    new_conn.commit()


def summary(db_path: Path) -> str:
    conn = sqlite3.connect(str(db_path))
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    lines = []
    for (tname,) in tables:
        count = conn.execute(f"SELECT COUNT(*) FROM \"{tname}\"").fetchone()[0]
        lines.append(f"  {tname}: {count} rows")
    conn.close()
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Unify models.db + model-status.db")
    parser.add_argument("--apply", action="store_true", help="Execute merge (default: dry-run)")
    args = parser.parse_args()

    print("=== DB Migration: models.db + model-status.db -> unified models.db ===\n")

    # Step 0: Detect old Go-format models.db and rename to .go
    if _GO_INITIAL.exists():
        try:
            probe = sqlite3.connect(str(_GO_INITIAL))
            cursor = probe.execute("PRAGMA table_info(providers)")
            cols = [r[1] for r in cursor.fetchall()]
            probe.close()
            is_go_format = "cli" in cols  # Go schema has `cli` column, unified doesn't
        except Exception:
            is_go_format = False

        if is_go_format:
            import shutil
            shutil.copy2(str(_GO_INITIAL), str(OLD_GO_DB))
            _GO_INITIAL.unlink()
            print(f"Renamed Go DB  {_GO_INITIAL.name} -> {OLD_GO_DB.name}")
        else:
            # Already unified or unknown format; backup existing
            if _GO_INITIAL.exists() and args.apply:
                import shutil
                shutil.copy2(str(_GO_INITIAL), str(BACKUP))
                print(f"Backed up existing {_GO_INITIAL} -> {BACKUP.name}")

    go_exists = OLD_GO_DB.exists()
    py_exists = OLD_PY_DB.exists()

    print(f"Go DB   ({OLD_GO_DB.name}): {'EXISTS' if go_exists else 'not found'}")
    if go_exists:
        print(summary(OLD_GO_DB))
    print()
    print(f"Py DB   ({OLD_PY_DB.name}): {'EXISTS' if py_exists else 'not found'}")
    if py_exists:
        print(summary(OLD_PY_DB))
    print()

    if not go_exists and not py_exists:
        print("No source DBs found. Nothing to migrate.")
        return

    # Target: unified models.db
    target = NEW_DB
    if target.exists() and args.apply:
        import shutil
        shutil.copy2(str(target), str(BACKUP))
        print(f"Backup saved -> {BACKUP.name}")
        target.unlink(missing_ok=True)

    if not args.apply:
        print("\n--- DRY RUN ---")
        print("Run with --apply to execute the merge.")
        return

    # --apply: execute
    print("\nCreating unified schema...")
    new_conn = sqlite3.connect(str(target))
    unified_schema(new_conn)

    total = 0
    if go_exists:
        go_conn = sqlite3.connect(str(OLD_GO_DB))
        migrate_from_go_db(go_conn, new_conn)
        go_cnt = go_conn.execute("SELECT COUNT(*) FROM providers").fetchone()[0]
        go_conn.close()
        total += go_cnt
        print(f"Migrated {go_cnt} providers from Go DB")

    if py_exists:
        py_conn = sqlite3.connect(str(OLD_PY_DB))
        migrate_from_py_db(py_conn, new_conn)
        py_cnt = py_conn.execute("SELECT COUNT(*) FROM providers").fetchone()[0]
        py_conn.close()
        total += py_cnt
        print(f"Migrated {py_cnt} providers from Python DB")

    new_conn.close()
    print(f"\nOK Unified DB created at {target}")
    print(summary(target))

    # Update selector.py to use the new path
    selector_path = Path(__file__).parent / "selector.py"
    if selector_path.exists():
        print(f"\nNOTE: selector.py still references model-status.db -- update DB_PATH to models.db")


if __name__ == "__main__":
    main()
