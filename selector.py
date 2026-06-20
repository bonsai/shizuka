#!/usr/bin/env python3
"""
Model Selector — 全CLI共有モデル選択エンジン

読み取り:
  - models.agents.md  (YAML frontmatter: 定義・優先順位)
  - model-status.db   (SQLite: 動的状態・使用実績)

出力:
  - 最適モデルの選択・切替指示
  - 選択理由（残量・コスト・品質スコア）
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

# ── paths ─────────────────────────────────────────────────────────────────────
MEGA = Path(os.environ.get("MEGA", Path.home() / "Documents" / "MEGA"))
MODELS_YAML = MEGA / "models.agents.md"
DB_DIR = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "model-manager"
DB_PATH = DB_DIR / "model-status.db"


# ── YAML parser ───────────────────────────────────────────────────────────────
def parse_models_yaml(path: Path) -> dict:
    """Return parsed YAML frontmatter from models.agents.md."""
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    if not m:
        raise ValueError(f"No YAML frontmatter in {path}")
    return yaml.safe_load(m.group(1))


# ── DB ────────────────────────────────────────────────────────────────────────
def get_db() -> sqlite3.Connection:
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def schema(conn: sqlite3.Connection):
    path = MEGA / "model-manager" / "schema.sql"
    if path.exists():
        conn.executescript(path.read_text(encoding="utf-8"))
    conn.commit()


def sync_yaml_to_db(conn: sqlite3.Connection, cfg: dict):
    """Sync providers & cli_priority from YAML config into DB."""
    reg = cfg.get("model_registry", cfg)  # support both flat and nested
    models = reg.get("models", {})
    priority_list = reg.get("priority", [])
    defaults = reg.get("defaults", {})
    task_recs = reg.get("task_recommendations", {})

    # 1. providers
    for i, mk in enumerate(priority_list):
        m = models.get(mk)
        if not m:
            continue
        prov = m.get("provider", "?")
        mid = m.get("model_id", mk)
        tier = m.get("tier", "free")
        cost = m.get("cost", 0.0)
        q = m.get("quality", {})
        note = m.get("note", "")
        conn.execute(
            """INSERT OR REPLACE INTO providers
               (model_key, provider, model_id, tier, cost_in,
                quality_low, quality_mid, quality_high, quality_code,
                priority, note)
               VALUES (?,?,?,?,?, ?,?,?,?, ?,?)""",
            (mk, prov, mid, tier, cost,
             q.get("l", 5), q.get("m", 5), q.get("h", 5), q.get("c", 5),
             i + 1, note),
        )

    # 2. cli_priority (for each CLI)
    for cli, dflt in defaults.items():
        conn.execute("DELETE FROM cli_priority WHERE cli=?", (cli,))
        for i, mk in enumerate(priority_list):
            conn.execute(
                "INSERT INTO cli_priority (cli, priority, model_key) VALUES (?,?,?)",
                (cli, i + 1, mk),
            )

    # 3. task recommendations → ml_params
    for task, info in task_recs.items():
        for role in ("primary", "secondary"):
            mk = info.get(role)
            if mk:
                conn.execute(
                    "INSERT OR REPLACE INTO ml_params (param_key, param_value) VALUES (?,?)",
                    (f"rec_{task}_{role}", hash(mk) % 1_000_000),
                )

    # 4. rotation_state from priority (qwen models with dashscope provider get rotation entries)
    rotation_models = [mk for mk in priority_list
                       if models.get(mk, {}).get("provider") == "dashscope"]
    for i, mk in enumerate(rotation_models):
        conn.execute(
            """INSERT OR REPLACE INTO rotation_state
               (model_key, sort_order, expired, expires_at)
               VALUES (?,?,?,?)""",
            (mk, i, 0, ""),
        )

    conn.commit()


# ── selection logic ───────────────────────────────────────────────────────────
def best_available(conn: sqlite3.Connection, task_type: str = "quick",
                   preferred_model: str = None) -> dict:
    """
    選択ロジック:
      1. preferred_model が指定されていて利用可能ならそれを返す
      2. ZEN (OpenRouter 無料枠, priority 1-3) を試す
      3. ZEN 切れ → Qwen rotation を試す
      4. Qwen 切れ → task_type で分岐:
           quick/short → DeepSeek (priority 4-5)
           code/long → Sakura (Kimi K2.6 / Qwen3-Coder-480B / Qwencoder)
    """
    rows = conn.execute(
        """SELECT p.*, q.remaining, r.expired, r.expires_at, rec.estimated_at, rec.confidence
           FROM providers p
           LEFT JOIN quota q ON q.model_key = p.model_key AND q.provider = p.provider
           LEFT JOIN rotation_state r ON r.model_key = p.model_key
           LEFT JOIN recovery_estimate rec ON rec.model_key = p.model_key
           ORDER BY p.priority"""
    ).fetchall()

    if not rows:
        return {"error": "no models in DB"}

    # preferred override
    if preferred_model:
        for r in rows:
            if r["model_key"] == preferred_model:
                expired = r["expired"]
                remaining = r["remaining"]
                if not expired and (remaining is None or remaining > 0):
                    return _to_result(r, "preferred")
                else:
                    return {"error": f"preferred {preferred_model} unavailable",
                            "expired": expired, "remaining": remaining}

    # 1. ZEN (OpenRouter free: priority 1-3)
    zen = [r for r in rows if r["priority"] in (1, 2, 3) and not r["expired"] and
           (r["remaining"] is None or r["remaining"] > 0)]
    if zen:
        return _to_result(zen[0], "zen")

    # 2. Qwen rotation
    qwen = [r for r in rows if r["provider"] == "dashscope" and not r["expired"] and
            (r["remaining"] is None or r["remaining"] > 0)]
    if qwen:
        return _to_result(qwen[0], "qwen_rotation")

    # 3. task-based fallback
    short_tasks = ("quick", "mid", "code")
    is_short = task_type in short_tasks
    fallback_providers = ["deepseek"] if is_short else ["sakura"]

    fallback = [r for r in rows if r["provider"] in fallback_providers and
                not r["expired"] and (r["remaining"] is None or r["remaining"] > 0)]
    if fallback:
        return _to_result(fallback[0], f"fallback_{task_type}")

    return {"error": "all models exhausted",
            "note": "try: model-selector --recovery to check estimates"}


def _to_result(row: sqlite3.Row, reason: str) -> dict:
    recovery = None
    if row["estimated_at"]:
        recovery = {"estimated_at": row["estimated_at"], "confidence": row["confidence"]}
    elif row["expires_at"]:
        recovery = {"estimated_at": row["expires_at"], "confidence": 0.5}

    return {
        "model_key": row["model_key"],
        "provider": row["provider"],
        "model_id": row["model_id"],
        "tier": row["tier"],
        "cost": row["cost_in"],
        "quality": {
            "l": row["quality_low"], "m": row["quality_mid"],
            "h": row["quality_high"], "c": row["quality_code"],
        },
        "priority": row["priority"],
        "remaining": row["remaining"],
        "expired": bool(row["expired"]),
        "reason": reason,
        "recovery": recovery,
    }


# ── quota / usage ─────────────────────────────────────────────────────────────
def log_usage(conn: sqlite3.Connection, cli: str, model_key: str,
              task_type: str, tokens_in: int, tokens_out: int,
              cost: float, latency_ms: int, success: bool = True,
              error_msg: str = ""):
    conn.execute(
        """INSERT INTO usage_log
           (cli, model_key, task_type, tokens_in, tokens_out,
            cost, latency_ms, success, error_msg)
           VALUES (?,?,?,?,?, ?,?,?,?)""",
        (cli, model_key, task_type, tokens_in, tokens_out,
         cost, latency_ms, 1 if success else 0, error_msg),
    )

    # update recovery estimate using exponential moving average
    _update_recovery_estimate(conn, model_key)
    conn.commit()


def _update_recovery_estimate(conn: sqlite3.Connection, model_key: str):
    """Simple recovery estimate based on usage pattern."""
    row = conn.execute(
        """SELECT COUNT(*) as cnt,
                  AVG(CASE WHEN success=1 THEN 1.0 ELSE 0 END) as success_rate,
                  MAX(timestamp) as last_ts
           FROM usage_log WHERE model_key=?
           AND timestamp > datetime('now', '-7 days')""",
        (model_key,),
    ).fetchone()

    if not row or row["cnt"] == 0:
        return

    # if success rate dropped -> check if model is exhausted
    if row["success_rate"] is not None and row["success_rate"] < 0.3 and row["cnt"] >= 3:
        # estimate 24h recovery for rate-limited models
        est = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
        conn.execute(
            """INSERT OR REPLACE INTO recovery_estimate
               (model_key, estimated_at, confidence, based_on, cached_remaining, updated_at)
               VALUES (?,?,?,?,?, datetime('now'))""",
            (model_key, est, 0.3, "pattern", 0),
        )


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


def mark_exhausted(conn: sqlite3.Connection, model_key: str, reason: str = "",
                   recovery_hours: int = 24):
    conn.execute(
        """INSERT OR REPLACE INTO rotation_state
           (model_key, sort_order, expired, expires_at, last_error, updated_at)
           VALUES (
               ?, COALESCE((SELECT sort_order FROM rotation_state WHERE model_key=?), 0),
               1, ?, ?, datetime('now')
           )""",
        (model_key, model_key,
         (datetime.now(timezone.utc) + timedelta(hours=recovery_hours)).isoformat(),
         reason),
    )
    conn.commit()


# ── CLI ───────────────────────────────────────────────────────────────────────
def cmd_status(conn: sqlite3.Connection):
    rows = conn.execute("""
        SELECT p.model_key, p.provider, p.model_id, p.tier, p.priority,
               p.cost_in, q.remaining, r.expired, r.expires_at,
               rec.estimated_at, rec.confidence
        FROM providers p
        LEFT JOIN quota q ON q.model_key = p.model_key
        LEFT JOIN rotation_state r ON r.model_key = p.model_key
        LEFT JOIN recovery_estimate rec ON rec.model_key = p.model_key
        ORDER BY p.priority
    """).fetchall()

    print(f"{'Model':<22} {'Provider':<12} {'Tier':<5} {'Pri':<4} {'Cost':<7} "
          f"{'Quota':<7} {'Status':<10} {'Recovery'}")
    print("-" * 100)
    for r in rows:
        status = "OK"
        if r["expired"]:
            status = "EXPIRED"
        elif r["remaining"] is not None and r["remaining"] <= 0:
            status = "EMPTY"
        remaining = f"{r['remaining']:.0f}%" if r["remaining"] is not None else "-"
        cost = f"${r['cost_in']:.2f}" if r["cost_in"] else "free"
        recovery = ""
        if r["estimated_at"]:
            est = r["estimated_at"][:16]
            rec = f"{r['confidence']:.0%}" if r["confidence"] else ""
            recovery = f"{est} ({rec})"
        elif r["expires_at"]:
            recovery = r["expires_at"][:16]
        print(f"{r['model_key']:<22} {r['provider']:<12} {r['tier']:<5} "
              f"P{r['priority']:<2} {cost:<7} {remaining:<7} {status:<10} {recovery}")


def cmd_select(conn: sqlite3.Connection, task: str = "quick", preferred: str = None):
    result = best_available(conn, task, preferred)
    if "error" in result:
        print(f"ERROR: {result['error']}")
        if "note" in result:
            print(f"NOTE: {result['note']}")
        sys.exit(1)
    print(_format_selection(result))


def cmd_usage(conn: sqlite3.Connection, days: int = 7, cli: str = None):
    where = "timestamp > datetime('now', ?)"
    params = [f"-{days} days"]
    if cli:
        where += " AND cli=?"
        params.append(cli)

    rows = conn.execute(f"""
        SELECT model_key, COUNT(*) as calls,
               SUM(tokens_in) as t_in, SUM(tokens_out) as t_out,
               SUM(cost) as total_cost,
               AVG(latency_ms) as avg_lat,
               SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) as errors
        FROM usage_log WHERE {where}
        GROUP BY model_key ORDER BY total_cost DESC
    """, params).fetchall()

    print(f"{'Model':<22} {'Calls':<7} {'Tok In':<10} {'Tok Out':<10} "
          f"{'Cost':<10} {'Lat(ms)':<8} {'Errors'}")
    print("-" * 80)
    for r in rows:
        c = f"${r['total_cost']:.4f}" if r['total_cost'] else "free"
        print(f"{r['model_key']:<22} {r['calls']:<7} {r['t_in'] or 0:<10} "
              f"{r['t_out'] or 0:<10} {c:<10} {r['avg_lat'] or 0:<8.0f} {r['errors']}")


def cmd_recovery(conn: sqlite3.Connection):
    rows = conn.execute("""
        SELECT re.model_key, re.estimated_at, re.confidence,
               re.cached_remaining, re.based_on, p.provider
        FROM recovery_estimate re
        JOIN providers p ON p.model_key = re.model_key
        WHERE re.estimated_at > datetime('now')
        ORDER BY re.estimated_at
    """).fetchall()
    if not rows:
        print("No pending recovery estimates.")
        return
    for r in rows:
        print(f"{r['model_key']:<22} → {r['estimated_at'][:16]}  "
              f"(confidence: {r['confidence']:.0%}, based: {r['based_on']})")


def cmd_sync(conn: sqlite3.Connection):
    cfg = parse_models_yaml(MODELS_YAML)
    sync_yaml_to_db(conn, cfg)
    reg = cfg.get("model_registry", cfg)
    count = len(reg.get("models", {})) or len(cfg.get("models", {}))
    print(f"Synced {count} models from {MODELS_YAML.name}")


def cmd_export(conn: sqlite3.Connection, fmt: str = "json"):
    rows = conn.execute("SELECT * FROM v_available").fetchall()
    data = [dict(r) for r in rows]
    if fmt == "json":
        print(json.dumps(data, indent=2, default=str))
    else:
        cmd_status(conn)


def _format_selection(r: dict) -> str:
    q = r.get("quality", {})
    parts = [
        f"→ {r['model_key']}",
        f"  provider: {r['provider']}",
        f"  model:    {r['model_id']}",
        f"  tier:     {r['tier']}",
        f"  cost:     ${r['cost']:.2f}" if r["cost"] else "  cost:     free",
        f"  quality:  L{q.get('l','?')}/M{q.get('m','?')}/H{q.get('h','?')}/C{q.get('c','?')}",
        f"  priority: P{r['priority']}",
        f"  quota:    {r['remaining']:.0f}%" if r["remaining"] is not None else "  quota:    -",
        f"  reason:   {r['reason']}",
    ]
    if r.get("recovery"):
        parts.append(f"  recovery: {r['recovery']['estimated_at'][:16]} "
                     f"(conf: {r['recovery']['confidence']:.0%})")
    return "\n".join(parts)


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Model Selector — 全CLI共有エンジン")
    parser.add_argument("action", nargs="?", default="status",
                        choices=["status", "select", "usage", "recovery",
                                 "sync", "export", "exhausted", "log", "quota"])
    parser.add_argument("--backend", default="local", choices=["local", "bq"],
                        help="backend: local SQLite (default) or BigQuery")
    parser.add_argument("--task", default="quick", help="task type: quick|code|long|mid")
    parser.add_argument("--preferred", help="force a specific model_key")
    parser.add_argument("--cli", default="kilo", help="CLI name for logging")
    parser.add_argument("--days", type=int, default=7, help="usage window in days")

    # for `log` action
    parser.add_argument("--tokens-in", type=int, default=0)
    parser.add_argument("--tokens-out", type=int, default=0)
    parser.add_argument("--cost", type=float, default=0.0)
    parser.add_argument("--latency", type=int, default=0)
    parser.add_argument("--success", type=int, default=1)
    parser.add_argument("--error", default="")

    # for `quota` action
    parser.add_argument("--provider", default="")
    parser.add_argument("--model-key", default="")
    parser.add_argument("--remaining", type=float, default=100.0)

    # for `exhausted` action
    parser.add_argument("--recovery-hours", type=int, default=24)
    parser.add_argument("--reason", default="")

    args = parser.parse_args()

    if args.backend == "bq":
        try:
            from bq_backend import bq_dispatch
            bq_dispatch(args)
            return
        except ImportError as e:
            print(f"BQ backend not available: {e}")
            print("Install: pip install google-cloud-bigquery")
            sys.exit(1)

    conn = get_db()
    schema(conn)
    # auto-sync if providers table is empty
    count = conn.execute("SELECT COUNT(*) FROM providers").fetchone()[0]
    if count == 0:
        cmd_sync(conn)

    if args.action == "status":
        cmd_status(conn)
    elif args.action == "select":
        cmd_select(conn, args.task, args.preferred)
    elif args.action == "usage":
        cmd_usage(conn, args.days, args.cli)
    elif args.action == "recovery":
        cmd_recovery(conn)
    elif args.action == "sync":
        cmd_sync(conn)
    elif args.action == "export":
        cmd_export(conn)
    elif args.action == "exhausted":
        mk = args.model_key or input("model_key: ")
        mark_exhausted(conn, mk, args.reason, args.recovery_hours)
        print(f"Marked {mk} exhausted (recovery in {args.recovery_hours}h)")
    elif args.action == "log":
        log_usage(conn, args.cli, args.model_key, args.task,
                  args.tokens_in, args.tokens_out, args.cost,
                  args.latency, bool(args.success), args.error)
        print("Logged.")
    elif args.action == "quota":
        set_quota(conn, args.provider, args.model_key, args.remaining)
        print(f"Quota set: {args.provider}/{args.model_key} = {args.remaining:.0f}%")


if __name__ == "__main__":
    main()
