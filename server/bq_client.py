"""BigQuery client for shizuka MCP server.
Uses google-cloud-bigquery library (NOT bq CLI)."""
import os
import json
from datetime import datetime, timezone, timedelta
from google.cloud import bigquery

PROJECT = os.environ.get("GCP_PROJECT", "yok-ai-2026")
DATASET = os.environ.get("BQ_DATASET", "model_status")


def _client() -> bigquery.Client:
    return bigquery.Client(project=PROJECT)


def _fmt(sql: str) -> str:
    return sql.replace("{dataset}", f"`{PROJECT}.{DATASET}`")


def _rows_to_dicts(rows) -> list[dict]:
    return [dict(row.items()) for row in rows]


def query(sql: str) -> list[dict]:
    rows = _client().query(_fmt(sql)).result()
    return _rows_to_dicts(rows)


# ── Public API ────────────────────────────────────────────────────────────────

def status() -> list[dict]:
    return query("""
        SELECT p.model_key, p.provider, p.model_id, p.tier, p.priority,
               p.cost_in, q.remaining, r.expired, r.expires_at,
               rec.estimated_at, rec.confidence
        FROM {dataset}.providers p
        LEFT JOIN {dataset}.quota q ON q.model_key = p.model_key
        LEFT JOIN {dataset}.rotation_state r ON r.model_key = p.model_key
        LEFT JOIN {dataset}.recovery_estimate rec ON rec.model_key = p.model_key
        ORDER BY p.priority
    """)


def select(task_type: str = "quick", preferred: str = None) -> dict:
    rows = query("""
        SELECT p.*, q.remaining, r.expired, r.expires_at,
               rec.estimated_at, rec.confidence
        FROM {dataset}.providers p
        LEFT JOIN {dataset}.quota q ON q.model_key = p.model_key
        LEFT JOIN {dataset}.rotation_state r ON r.model_key = p.model_key
        LEFT JOIN {dataset}.recovery_estimate rec ON rec.model_key = p.model_key
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
    q = {"l": r.get("quality_low"), "m": r.get("quality_mid"),
         "h": r.get("quality_high"), "c": r.get("quality_code")}
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


def log_usage(cli: str, model_key: str, task_type: str = "",
              tokens_in: int = 0, tokens_out: int = 0, cost: float = 0,
              latency_ms: int = 0, success: bool = True, error_msg: str = "") -> str:
    _client().query(_fmt(f"""
        INSERT INTO {{dataset}}.usage_log
        (id, cli, model_key, task_type, tokens_in, tokens_out,
         cost, latency_ms, success, error_msg, logged_at)
        VALUES
        (UNIX_MILLIS(CURRENT_TIMESTAMP()), @cli, @mk, @tt,
         @ti, @to, @c, @lat, @suc, @err,
         CURRENT_TIMESTAMP())
    """), job_config=bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("cli", "STRING", cli),
            bigquery.ScalarQueryParameter("mk", "STRING", model_key),
            bigquery.ScalarQueryParameter("tt", "STRING", task_type),
            bigquery.ScalarQueryParameter("ti", "INT64", tokens_in),
            bigquery.ScalarQueryParameter("to", "INT64", tokens_out),
            bigquery.ScalarQueryParameter("c", "FLOAT64", cost),
            bigquery.ScalarQueryParameter("lat", "INT64", latency_ms),
            bigquery.ScalarQueryParameter("suc", "BOOL", success),
            bigquery.ScalarQueryParameter("err", "STRING", error_msg),
        ]
    )).result()
    return f"logged {cli}/{model_key}  in:{_si(tokens_in)} out:{_si(tokens_out)}"


def _si(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1e6:.1f}M"
    if n >= 1_000:
        return f"{n/1e3:.1f}K"
    return str(n)


def mark_exhausted(model_key: str, reason: str = "", recovery_hours: int = 24):
    now = datetime.now(timezone.utc)
    expires = (now + timedelta(hours=recovery_hours)).isoformat()
    _client().query(_fmt(f"""
        MERGE INTO {{dataset}}.rotation_state t
        USING (SELECT @mk AS model_key) s
        ON t.model_key = s.model_key
        WHEN MATCHED THEN UPDATE SET
            expired = TRUE, expires_at = TIMESTAMP(@exp),
            last_error = @reason, updated_at = CURRENT_TIMESTAMP()
        WHEN NOT MATCHED THEN INSERT
            (model_key, sort_order, expired, expires_at, last_error, updated_at)
            VALUES (@mk, 0, TRUE, TIMESTAMP(@exp), @reason, CURRENT_TIMESTAMP())
    """), job_config=bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("mk", "STRING", model_key),
            bigquery.ScalarQueryParameter("exp", "STRING", expires),
            bigquery.ScalarQueryParameter("reason", "STRING", reason),
        ]
    )).result()


def update_quota(provider: str, model_key: str, remaining_pct: float):
    _client().query(_fmt(f"""
        MERGE INTO {{dataset}}.quota t
        USING (SELECT @prov AS provider, @mk AS model_key) s
        ON t.provider = s.provider AND t.model_key = s.model_key
        WHEN MATCHED THEN UPDATE SET
            remaining = @pct, checked_at = CURRENT_TIMESTAMP()
        WHEN NOT MATCHED THEN INSERT
            (provider, model_key, remaining, checked_at)
            VALUES (@prov, @mk, @pct, CURRENT_TIMESTAMP())
    """), job_config=bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("prov", "STRING", provider),
            bigquery.ScalarQueryParameter("mk", "STRING", model_key),
            bigquery.ScalarQueryParameter("pct", "FLOAT64", remaining_pct),
        ]
    )).result()


def usage_summary(days: int = 7, cli: str = None) -> list[dict]:
    where = f"logged_at > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days} DAY)"
    params = []
    if cli:
        where += " AND cli = @cli"
        params.append(bigquery.ScalarQueryParameter("cli", "STRING", cli))
    return query_with_params(f"""
        SELECT model_key, COUNT(*) as calls,
               SUM(tokens_in) as t_in, SUM(tokens_out) as t_out,
               SUM(cost) as total_cost,
               AVG(latency_ms) as avg_lat,
               SUM(CASE WHEN success = FALSE THEN 1 ELSE 0 END) as errors
        FROM {{dataset}}.usage_log
        WHERE {where}
        GROUP BY model_key ORDER BY total_cost DESC
    """, params)


def query_with_params(sql: str, params: list) -> list[dict]:
    rows = _client().query(
        _fmt(sql),
        job_config=bigquery.QueryJobConfig(query_parameters=params)
    ).result()
    return _rows_to_dicts(rows)


def recovery_estimates() -> list[dict]:
    return query("""
        SELECT re.model_key, re.estimated_at, re.confidence,
               re.cached_remaining, re.based_on, p.provider
        FROM {dataset}.recovery_estimate re
        JOIN {dataset}.providers p ON p.model_key = re.model_key
        WHERE re.estimated_at > CURRENT_TIMESTAMP()
        ORDER BY re.estimated_at
    """)


def balance(provider: str = "") -> list[dict]:
    where = ""
    params = []
    if provider:
        where = " WHERE provider = @prov"
        params.append(bigquery.ScalarQueryParameter("prov", "STRING", provider))
    return query_with_params(f"""
        SELECT provider, total_balance, currency, is_available, topped_up, granted, timestamp
        FROM {{dataset}}.balance_snapshots
        {where}
        ORDER BY timestamp DESC
    """, params)


def quota(provider: str = "", model_key: str = "") -> list[dict]:
    wheres = []
    params = []
    if provider:
        wheres.append("provider = @prov")
        params.append(bigquery.ScalarQueryParameter("prov", "STRING", provider))
    if model_key:
        wheres.append("model_key = @mk")
        params.append(bigquery.ScalarQueryParameter("mk", "STRING", model_key))
    where_sql = " WHERE " + " AND ".join(wheres) if wheres else ""
    return query_with_params(f"""
        SELECT provider, model_key, remaining, tokens_remaining, tokens_limit, checked_at
        FROM {{dataset}}.quota
        {where_sql}
        ORDER BY checked_at DESC
    """, params)


def check_health() -> dict:
    try:
        _client().query("SELECT 1").result()
        return {"status": "ok", "project": PROJECT, "dataset": DATASET}
    except Exception as e:
        return {"status": "error", "message": str(e)}
