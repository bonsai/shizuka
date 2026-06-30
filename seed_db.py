"""seed_db.py — unified models.db に models.json からデータを投入"""
import json
import sqlite3
import sys
from pathlib import Path

MEGA = Path.home() / "Documents" / "MEGA"
MODELS_JSON = MEGA / "shizuka" / "models.json"
DB_DIR = Path.home() / "AppData" / "Roaming" / "model-manager"
DB_PATH = DB_DIR / "models.db"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def seed_from_models_json(conn: sqlite3.Connection, cfg: dict):
    entries = cfg.get("priorities", [])
    specs = {f"{s['provider']}|{s['model']}": s for s in cfg.get("modelSpecs", [])}
    rotation = cfg.get("qwenRotation", [])

    for cli_pri in entries:
        cli = cli_pri["cli"]
        for i, ent in enumerate(cli_pri.get("entries", [])):
            provider = ent["provider"]
            model = ent.get("model", "")
            use_rot = 1 if ent.get("useRotation") else 0
            note = ent.get("note", "")

            # Resolve rotation model
            if use_rot:
                # Find first non-exhausted rotation entry
                for r in rotation:
                    if not r.get("exhausted", False):
                        model = r["modelID"]
                        break
                if not model:
                    model = "qwen3.5-27b"

            # Build model_key
            mid = model if model else "default"
            mk = f"{provider}-{mid}".replace("/", "-").replace(":", "-").replace(".", "-").lower()

            # Look up spec
            spec = specs.get(f"{provider}|{model}") or specs.get(f"{provider}|")
            if spec:
                cost = spec.get("costIn", 0)
                q = spec.get("quality", {})
                ql = q.get("low", 5)
                qm = q.get("mid", 5)
                qh = q.get("high", 5)
                qc = q.get("code", 5)
                tier = "paid" if cost > 0 else "free"
            else:
                cost, ql, qm, qh, qc = 0, 5, 5, 5, 5
                tier = "free"

            # Upsert provider
            conn.execute(
                """INSERT OR REPLACE INTO providers
                   (model_key, provider, model_id, tier, cost_in,
                    quality_low, quality_mid, quality_high, quality_code, priority, note)
                   VALUES (?,?,?,?,?, ?,?,?,?, ?,?)""",
                (mk, provider, mid, tier, cost, ql, qm, qh, qc, i + 1, note),
            )

            # Upsert cli_priority
            conn.execute(
                "INSERT OR REPLACE INTO cli_priority (cli, priority, model_key, use_rotation) VALUES (?,?,?,?)",
                (cli, i + 1, mk, use_rot),
            )

    # Seed rotation_state from qwenRotation
    for i, r in enumerate(rotation):
        mk = f"dashscope-{r['modelID']}".replace("/", "-").replace(":", "-").lower()
        ex = 1 if r.get("exhausted") else 0
        expires = r.get("expires", "")
        conn.execute(
            """INSERT OR REPLACE INTO rotation_state
               (model_key, sort_order, expired, expires_at) VALUES (?,?,?,?)""",
            (mk, i, ex, expires),
        )

    conn.commit()


def summary(conn: sqlite3.Connection) -> str:
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    lines = []
    for (tname,) in tables:
        count = conn.execute(f'SELECT COUNT(*) FROM "{tname}"').fetchone()[0]
        lines.append(f"  {tname}: {count} rows")
    return "\n".join(lines)


def main():
    if not MODELS_JSON.exists():
        print(f"Error: {MODELS_JSON} not found")
        sys.exit(1)

    cfg = load_json(MODELS_JSON)
    print(f"Loaded {len(cfg.get('priorities', []))} CLIs from {MODELS_JSON}")

    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=OFF")  # OFF during seed

    # Import schema if not present
    schema_path = Path(__file__).parent / "db.py"
    if schema_path.exists():
        # Schema is created by db.py's SCHEMA_SQL on first use
        from db import get_db as _dummy
        _dummy().close()

    # Ensure schema exists by opening with db module
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS providers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_key TEXT NOT NULL UNIQUE, provider TEXT NOT NULL,
            model_id TEXT NOT NULL, tier TEXT DEFAULT 'free',
            cost_in REAL DEFAULT 0,
            quality_low INTEGER DEFAULT 5, quality_mid INTEGER DEFAULT 5,
            quality_high INTEGER DEFAULT 5, quality_code INTEGER DEFAULT 5,
            priority INTEGER DEFAULT 999, note TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS cli_priority (
            cli TEXT NOT NULL, priority INTEGER NOT NULL,
            model_key TEXT NOT NULL, use_rotation INTEGER DEFAULT 0,
            PRIMARY KEY (cli, priority)
        );
        CREATE TABLE IF NOT EXISTS rotation_state (
            model_key TEXT NOT NULL UNIQUE, sort_order INTEGER NOT NULL,
            expired INTEGER DEFAULT 0, expires_at TEXT DEFAULT '',
            last_error TEXT DEFAULT '', updated_at TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit()

    seed_from_models_json(conn, cfg)
    print("\nSeeded DB:")
    print(summary(conn))
    conn.close()

    print(f"\nOK unified DB ready at {DB_PATH}")


def main_clear_and_seed():
    """Clear all data and reseed from models.json (clean slate)."""
    if not MODELS_JSON.exists():
        print(f"Error: {MODELS_JSON} not found")
        sys.exit(1)

    cfg = load_json(MODELS_JSON)
    print(f"Loaded {len(cfg.get('priorities', []))} CLIs from {MODELS_JSON}")

    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=OFF")  # OFF during seed

    # Ensure schema
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS providers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_key TEXT NOT NULL UNIQUE, provider TEXT NOT NULL,
            model_id TEXT NOT NULL, tier TEXT DEFAULT 'free',
            cost_in REAL DEFAULT 0,
            quality_low INTEGER DEFAULT 5, quality_mid INTEGER DEFAULT 5,
            quality_high INTEGER DEFAULT 5, quality_code INTEGER DEFAULT 5,
            priority INTEGER DEFAULT 999, note TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS cli_priority (
            cli TEXT NOT NULL, priority INTEGER NOT NULL,
            model_key TEXT NOT NULL, use_rotation INTEGER DEFAULT 0,
            PRIMARY KEY (cli, priority)
        );
        CREATE TABLE IF NOT EXISTS quota (
            provider TEXT NOT NULL, model_key TEXT DEFAULT '',
            remaining REAL DEFAULT 100.0, tokens_remaining INTEGER DEFAULT -1,
            tokens_limit INTEGER DEFAULT -1, checked_at TEXT NOT NULL,
            PRIMARY KEY (provider, model_key)
        );
        CREATE TABLE IF NOT EXISTS rotation_state (
            model_key TEXT NOT NULL UNIQUE, sort_order INTEGER NOT NULL,
            expired INTEGER DEFAULT 0, expires_at TEXT DEFAULT '',
            last_error TEXT DEFAULT '', updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS usage_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cli TEXT NOT NULL, model_key TEXT NOT NULL,
            task_type TEXT DEFAULT '', tokens_in INTEGER DEFAULT 0,
            tokens_out INTEGER DEFAULT 0, cost REAL DEFAULT 0,
            latency_ms INTEGER DEFAULT 0, success INTEGER DEFAULT 1,
            error_msg TEXT DEFAULT '', timestamp TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS recovery_estimate (
            model_key TEXT NOT NULL UNIQUE, estimated_at TEXT,
            confidence REAL DEFAULT 0.5, based_on TEXT DEFAULT '',
            cached_remaining REAL DEFAULT 0, updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS ml_params (
            param_key TEXT PRIMARY KEY, param_value REAL NOT NULL,
            updated_at TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit()

    # Clear existing data (providers, cli_priority, rotation_state from models.json)
    conn.execute("DELETE FROM cli_priority")
    conn.execute("DELETE FROM rotation_state")
    conn.execute("DELETE FROM providers")
    conn.commit()

    seed_from_models_json(conn, cfg)
    print("\nSeeded DB:")
    print(summary(conn))
    conn.close()

    print(f"\nOK unified DB ready at {DB_PATH}")
    print("NOTE: usage_log/quota/recovery were preserved from previous migration.")


if __name__ == "__main__":
    main()
