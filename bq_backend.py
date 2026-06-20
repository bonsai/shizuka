"""BigQuery backend for selector.py"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

MEGA = Path(os.environ.get("MEGA", Path.home() / "Documents" / "MEGA"))
MODELS_YAML = MEGA / "models.agents.md"
PROJECT = "yok-ai-2026"
DATASET = "model_status"


_NUMERIC_KEYS = {
    "priority", "cost_in", "quality_low", "quality_mid", "quality_high", "quality_code",
    "remaining", "confidence", "cached_remaining", "sort_order",
    "tokens_in", "tokens_out", "tokens_remaining", "tokens_limit",
    "cost", "latency_ms", "calls", "t_in", "t_out", "total_cost", "avg_lat", "errors",
    "id",
}


def _convert_row(r: dict) -> dict:
    """Convert numeric string fields from BQ JSON to proper Python types."""
    out = {}
    for k, v in r.items():
        if v is None:
            out[k] = None
        elif k in _NUMERIC_KEYS:
            try:
                out[k] = int(v) if "." not in str(v) else float(v)
            except (ValueError, TypeError):
                out[k] = v
        else:
            out[k] = v
    return out


def _bq(sql: str) -> list[dict]:
    """Execute a BQ query and return rows as dicts via `bq query --format=json`."""
    flat = " ".join(sql.split())
    cmd = ["cmd.exe", "/c", "bq", "query", "--use_legacy_sql=false", "--format=json",
           "--project_id", PROJECT, flat]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    # bq writes progress to stderr; only real errors start with "BigQuery error"
    if result.returncode != 0:
        if "BigQuery error" in result.stderr:
            print(f"BQ error: {result.stderr}", file=sys.stderr)
        return []
    out = result.stdout.strip()
    if not out:
        return []
    try:
        raw = json.loads(out)
        return [_convert_row(r) for r in raw]
    except json.JSONDecodeError:
        start = out.find("[")
        end = out.rfind("]") + 1
        if start >= 0 and end > start:
            try:
                raw = json.loads(out[start:end])
                return [_convert_row(r) for r in raw]
            except json.JSONDecodeError:
                return []
        return []


def bq_status() -> list[dict]:
    sql = """
        SELECT p.model_key, p.provider, p.model_id, p.tier, p.priority,
               p.cost_in, q.remaining, r.expired, r.expires_at,
               rec.estimated_at, rec.confidence
        FROM model_status.providers p
        LEFT JOIN model_status.quota q ON q.model_key = p.model_key
        LEFT JOIN model_status.rotation_state r ON r.model_key = p.model_key
        LEFT JOIN model_status.recovery_estimate rec ON rec.model_key = p.model_key
        ORDER BY p.priority
    """
    return _bq(sql)


def bq_select(task_type: str = "quick", preferred: str = None) -> dict:
    """Best available model selection via BQ."""
    rows = _bq("""
        SELECT p.*, q.remaining, r.expired, r.expires_at, rec.estimated_at, rec.confidence
        FROM model_status.providers p
        LEFT JOIN model_status.quota q ON q.model_key = p.model_key
        LEFT JOIN model_status.rotation_state r ON r.model_key = p.model_key
        LEFT JOIN model_status.recovery_estimate rec ON rec.model_key = p.model_key
        ORDER BY p.priority
    """)
    if not rows:
        return {"error": "no models in BQ"}

    if preferred:
        for r in rows:
            if r.get("model_key") == preferred:
                ex = r.get("expired")
                rem = r.get("remaining")
                if not ex and (rem is None or rem > 0):
                    return _to_result(r, "preferred")
                return {"error": f"preferred {preferred} unavailable", "expired": ex, "remaining": rem}

    zen = [r for r in rows if r.get("priority") in (1, 2, 3)
           and not r.get("expired") and (r.get("remaining") is None or r.get("remaining", 0) > 0)]
    if zen:
        return _to_result(zen[0], "zen")

    qwen = [r for r in rows if r.get("provider") == "dashscope"
            and not r.get("expired") and (r.get("remaining") is None or r.get("remaining", 0) > 0)]
    if qwen:
        return _to_result(qwen[0], "qwen_rotation")

    short_tasks = ("quick", "mid", "code")
    fb_provs = ["deepseek"] if task_type in short_tasks else ["sakura"]
    fb = [r for r in rows if r.get("provider") in fb_provs
          and not r.get("expired") and (r.get("remaining") is None or r.get("remaining", 0) > 0)]
    if fb:
        return _to_result(fb[0], f"fallback_{task_type}")

    return {"error": "all models exhausted"}


def _to_result(r: dict, reason: str) -> dict:
    q = {
        "l": r.get("quality_low"),
        "m": r.get("quality_mid"),
        "h": r.get("quality_high"),
        "c": r.get("quality_code"),
    }
    recovery = None
    est = r.get("estimated_at") or r.get("expires_at")
    if est:
        recovery = {"estimated_at": str(est)[:19], "confidence": r.get("confidence", 0.5)}
    return {
        "model_key": r.get("model_key"),
        "provider": r.get("provider"),
        "model_id": r.get("model_id"),
        "tier": r.get("tier"),
        "cost": r.get("cost_in", 0),
        "quality": q,
        "priority": r.get("priority"),
        "remaining": r.get("remaining"),
        "expired": bool(r.get("expired")),
        "reason": reason,
        "recovery": recovery,
    }


def bq_log(cli: str, model_key: str, task_type: str,
           tokens_in: int, tokens_out: int, cost: float,
           latency_ms: int, success: bool = True, error_msg: str = ""):
    safe_err = error_msg.replace("'", "")
    success_str = "TRUE" if success else "FALSE"
    sql = f"""
        INSERT INTO model_status.usage_log
        (id, cli, model_key, task_type, tokens_in, tokens_out,
         cost, latency_ms, success, error_msg, logged_at)
        VALUES
        (UNIX_MILLIS(CURRENT_TIMESTAMP()), '{cli}', '{model_key}', '{task_type}',
         {tokens_in}, {tokens_out}, {cost}, {latency_ms},
         {success_str}, '{safe_err}',
         CURRENT_TIMESTAMP())
    """
    _bq(sql)


def bq_exhausted(model_key: str, reason: str = "", recovery_hours: int = 24):
    now = datetime.now(timezone.utc)
    expires = (now + timedelta(hours=recovery_hours)).isoformat()
    safe_reason = reason.replace("'", "")
    _bq(f"""
        MERGE INTO model_status.rotation_state t
        USING (SELECT '{model_key}' AS model_key) s
        ON t.model_key = s.model_key
        WHEN MATCHED THEN UPDATE SET
            expired = TRUE, expires_at = TIMESTAMP('{expires}'),
            last_error = '{safe_reason}',
            updated_at = CURRENT_TIMESTAMP()
        WHEN NOT MATCHED THEN INSERT
            (model_key, sort_order, expired, expires_at, last_error, updated_at)
            VALUES ('{model_key}', 0, TRUE, TIMESTAMP('{expires}'),
                    '{safe_reason}', CURRENT_TIMESTAMP())
    """)


def bq_quota(provider: str, model_key: str, remaining_pct: float):
    _bq(f"""
        MERGE INTO model_status.quota t
        USING (SELECT '{provider}' AS provider, '{model_key}' AS model_key) s
        ON t.provider = s.provider AND t.model_key = s.model_key
        WHEN MATCHED THEN UPDATE SET
            remaining = {remaining_pct}, checked_at = CURRENT_TIMESTAMP()
        WHEN NOT MATCHED THEN INSERT
            (provider, model_key, remaining, checked_at)
            VALUES ('{provider}', '{model_key}', {remaining_pct}, CURRENT_TIMESTAMP())
    """)


def bq_usage(days: int = 7, cli: str = None) -> list[dict]:
    where = f"logged_at > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days} DAY)"
    if cli:
        where += f" AND cli = '{cli}'"
    return _bq(f"""
        SELECT model_key, COUNT(*) as calls,
               SUM(tokens_in) as t_in, SUM(tokens_out) as t_out,
               SUM(cost) as total_cost,
               AVG(latency_ms) as avg_lat,
               SUM(CASE WHEN success = FALSE THEN 1 ELSE 0 END) as errors
        FROM model_status.usage_log
        WHERE {where}
        GROUP BY model_key ORDER BY total_cost DESC
    """)


def bq_recovery() -> list[dict]:
    return _bq("""
        SELECT re.model_key, re.estimated_at, re.confidence,
               re.cached_remaining, re.based_on, p.provider
        FROM model_status.recovery_estimate re
        JOIN model_status.providers p ON p.model_key = re.model_key
        WHERE re.estimated_at > CURRENT_TIMESTAMP()
        ORDER BY re.estimated_at
    """)


def bq_status_text():
    rows = bq_status()
    if not rows:
        print("No models in BQ. Run `python selector.py sync --backend bq` first.")
        return
    print(f"{'Model':<22} {'Provider':<12} {'Tier':<5} {'Pri':<4} {'Cost':<7} "
          f"{'Quota':<7} {'Status':<10} {'Recovery'}")
    print("-" * 100)
    for r in rows:
        status = "OK"
        if r.get("expired"):
            status = "EXPIRED"
        elif r.get("remaining") is not None and float(r["remaining"]) <= 0:
            status = "EMPTY"
        remaining = f"{float(r.get('remaining', 0)):.0f}%" if r.get("remaining") is not None else "-"
        cost_in = float(r["cost_in"]) if r.get("cost_in") else 0
        cost = f"${cost_in:.2f}" if cost_in else "free"
        recovery = ""
        est = r.get("estimated_at") or r.get("expires_at")
        if est:
            recovery = str(est)[:16]
        print(f"{r['model_key']:<22} {r['provider']:<12} {r['tier']:<5} "
              f"P{r['priority']:<2} {cost:<7} {remaining:<7} {status:<10} {recovery}")


def bq_select_text(task: str, preferred: str = None):
    r = bq_select(task, preferred)
    if "error" in r:
        print(f"ERROR: {r['error']}")
        return
    print(f"-> {r['model_key']}")
    print(f"  provider: {r['provider']}")
    print(f"  model:    {r['model_id']}")
    q = r.get("quality", {})
    print(f"  quality:  L{q.get('l','?')}/M{q.get('m','?')}/H{q.get('h','?')}/C{q.get('c','?')}")
    print(f"  priority: P{r['priority']}")
    print(f"  tier:     {r['tier']}")
    print(f"  cost:     ${r['cost']:.2f}" if r.get("cost") else "  cost:     free")
    print(f"  quota:    {r.get('remaining', 0):.0f}%" if r.get("remaining") is not None else "  quota:    -")
    print(f"  reason:   {r['reason']}")
    if r.get("recovery"):
        print(f"  recovery: {r['recovery']['estimated_at'][:16]}")


def bq_sync():
    """Sync YAML frontmatter to BQ providers table."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("selector", MEGA / "model-manager" / "selector.py")
    sel = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sel)
    cfg = sel.parse_models_yaml(MODELS_YAML)
    reg = cfg.get("model_registry", cfg)
    models = reg.get("models", {})
    priority = reg.get("priority", [])
    now = datetime.now(timezone.utc).isoformat()

    rows = []
    for i, mk in enumerate(priority):
        mo = models.get(mk)
        if not mo:
            continue
        q = mo.get("quality", {})
        note = mo.get("note", "").replace("'", "")
        mk_s = mk.replace("'", "")
        prov = mo.get("provider", "").replace("'", "")
        mid = mo.get("model_id", mk).replace("'", "")
        tier = mo.get("tier", "free").replace("'", "")
        rows.append(f"('{mk_s}', '{prov}', '{mid}', '{tier}', "
                    f"{mo.get('cost', 0)}, "
                    f"{q.get('l',5)}, {q.get('m',5)}, {q.get('h',5)}, {q.get('c',5)}, "
                    f"{i+1}, '{note}', TIMESTAMP('{now}'))")

    if not rows:
        print("No models to sync.")
        return

    values = ",\n".join(rows)
    sql = f"""
        CREATE OR REPLACE TABLE model_status.providers
        (model_key STRING, provider STRING, model_id STRING, tier STRING,
         cost_in FLOAT64, quality_low INT64, quality_mid INT64,
         quality_high INT64, quality_code INT64,
         priority INT64, note STRING, updated_at TIMESTAMP)
        CLUSTER BY priority, provider
        AS (
            SELECT * FROM UNNEST([
                {values}
            ])
        )
    """
    _bq(sql)
    print(f"Synced {len(rows)} models to BQ")


def bq_usage_text(days: int = 7, cli: str = None):
    rows = bq_usage(days, cli)
    if not rows:
        print("No usage data.")
        return
    print(f"{'Model':<22} {'Calls':<7} {'Tok In':<10} {'Tok Out':<10} "
          f"{'Cost':<10} {'Lat(ms)':<8} {'Errors'}")
    print("-" * 80)
    for r in rows:
        c = f"${r['total_cost']:.4f}" if r.get("total_cost") else "free"
        print(f"{r['model_key']:<22} {r['calls']:<7} {r.get('t_in',0):<10} "
              f"{r.get('t_out',0):<10} {c:<10} {r.get('avg_lat',0):<8.0f} {r.get('errors',0)}")


def bq_recovery_text():
    rows = bq_recovery()
    if not rows:
        print("No pending recovery estimates.")
        return
    for r in rows:
        print(f"{r['model_key']:<22} -> {str(r['estimated_at'])[:16]}  "
              f"(confidence: {r.get('confidence', 0):.0%}, based: {r.get('based_on', '')})")


# CLI dispatch
def bq_dispatch(args):
    action = args.action
    if action == "status":
        bq_status_text()
    elif action == "select":
        bq_select_text(args.task, args.preferred)
    elif action == "usage":
        bq_usage_text(args.days, args.cli)
    elif action == "recovery":
        bq_recovery_text()
    elif action == "sync":
        bq_sync()
    elif action == "exhausted":
        mk = args.model_key or input("model_key: ")
        bq_exhausted(mk, args.reason, args.recovery_hours)
        print(f"Marked {mk} exhausted (recovery in {args.recovery_hours}h)")
    elif action == "log":
        bq_log(args.cli, args.model_key, args.task,
               args.tokens_in, args.tokens_out, args.cost,
               args.latency, bool(args.success), args.error)
        print("Logged to BQ.")
    elif action == "quota":
        bq_quota(args.provider, args.model_key, args.remaining)
        print(f"Quota set in BQ: {args.provider}/{args.model_key} = {args.remaining:.0f}%")
    else:
        print(f"Unknown action: {action} for BQ backend")
