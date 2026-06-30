"""BQ GCP モニタリング CLI — usage/cost/balance/quota/trend を BigQuery から集計"""
import argparse
import csv
import io
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

BQ = r"C:\Program Files (x86)\Google\Cloud SDK\google-cloud-sdk\bin\bq.cmd"
PROJECT = "yok-ai-2026"
DATASET = "model_status"
CONFIG_DIR = Path.home() / ".bq_monitor"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = CONFIG_DIR / "config.json"


def _config() -> dict:
    default = {
        "alert_daily_cost": 0.50,
        "alert_cost_spike_factor": 2.0,
        "alert_low_balance": 1.00,
        "notify_cmd": "",
    }
    if CONFIG_FILE.exists():
        try:
            return {**default, **json.loads(CONFIG_FILE.read_text())}
        except Exception:
            return default
    return default


def _save_config(cfg: dict):
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


def _bq(sql: str) -> list[dict]:
    """Execute BQ query, return list of dicts."""
    flat = " ".join(sql.split())
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".sql", delete=False, dir=CONFIG_DIR)
    tmp.write(flat)
    tmp_path = tmp.name
    tmp.close()
    try:
        r = subprocess.run(
            [BQ, "query", "--nouse_legacy_sql", "--format=json",
             f"--project_id={PROJECT}", f"--flagfile={tmp_path}"],
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode != 0:
            if "BigQuery error" in r.stderr:
                print(f"BQ error: {r.stderr}", file=sys.stderr)
            return []
        out = r.stdout.strip()
        if not out:
            return []
        return json.loads(out)
    except json.JSONDecodeError:
        start = out.find("[")
        end = out.rfind("]") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(out[start:end])
            except json.JSONDecodeError:
                return []
        return []
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def _fmt_dt(ts):
    if not ts:
        return "-"
    s = str(ts)[:19]
    if "T" in s:
        return s.replace("T", " ")
    return s


def cmd_balance(args):
    """Show balance history for a provider."""
    days = args.days or 30
    provider = args.provider or "%"
    op = "=" if provider != "%" else "LIKE"
    sql = f"""
        SELECT provider, total_balance, currency, is_available, topped_up, granted, timestamp
        FROM {DATASET}.balance_snapshots
        WHERE provider {op} '{provider}'
          AND timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days} DAY)
        ORDER BY timestamp DESC
        LIMIT {args.limit or 30}
    """
    rows = _bq(sql)
    if not rows:
        print("No balance data.")
        return
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False, default=str))
        return
    print(f"{'Provider':<14} {'Balance':>10} {'Cur':>4} {'TopUp':>8} {'Granted':>8} {'Available':<6} {'Timestamp'}")
    print("-" * 80)
    for r in rows:
        avail = "YES" if r.get("is_available") else "NO"
        print(f"{r['provider']:<14} {r.get('total_balance', 0):>10.4f} "
              f"{r.get('currency', 'USD'):>4} "
              f"{r.get('topped_up', 0):>8.4f} {r.get('granted', 0):>8.4f} "
              f"{avail:<6} {_fmt_dt(r.get('timestamp'))}")


def cmd_cost(args):
    """Cost breakdown by model/provider/cli."""
    days = args.days or 7
    group = args.group or "model_key"
    valid = ("model_key", "provider", "cli")
    if group not in valid:
        print(f"Invalid group: {group}. Choose from: {', '.join(valid)}", file=sys.stderr)
        return
    sql = f"""
        SELECT {group}, COUNT(*) as calls,
               ROUND(SUM(cost), 6) as total_cost,
               ROUND(AVG(cost), 6) as avg_cost,
               ROUND(SUM(tokens_in + tokens_out)) as total_tokens,
               SUM(CASE WHEN success = FALSE THEN 1 ELSE 0 END) as errors
        FROM {DATASET}.usage_log
        WHERE logged_at > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days} DAY)
        GROUP BY {group}
        ORDER BY total_cost DESC
        LIMIT {args.limit or 20}
    """
    rows = _bq(sql)
    if not rows:
        print("No cost data.")
        return
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False, default=str))
        return
    print(f"{group:<20} {'Calls':<7} {'Cost($)':>12} {'Avg Cost':>10} {'Tokens':>10} {'Errors'}")
    print("-" * 70)
    for r in rows:
        print(f"{r[group]:<20} {r['calls']:<7} {r.get('total_cost', 0):>12.6f} "
              f"{r.get('avg_cost', 0):>10.6f} {r.get('total_tokens', 0):>10} {r.get('errors', 0)}")


def cmd_trend(args):
    """Daily cost trend as ASCII bar chart."""
    days = args.days or 30
    sql = f"""
        SELECT DATE(logged_at) as day,
               ROUND(SUM(cost), 6) as daily_cost,
               COUNT(*) as calls
        FROM {DATASET}.usage_log
        WHERE logged_at > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days} DAY)
        GROUP BY day
        ORDER BY day
    """
    rows = _bq(sql)
    if not rows:
        print("No trend data.")
        return
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False, default=str))
        return
    costs = [float(r.get("daily_cost", 0)) for r in rows]
    mx = max(costs) if costs else 1
    bar_w = 40
    print(f"{'Date':<14} {'Cost($)':>12} {'Calls':<7} {'Bar'}")
    print("-" * 80)
    for r in rows:
        c = float(r.get("daily_cost", 0))
        bar_len = int((c / mx) * bar_w) if mx > 0 else 0
        bar = "█" * bar_len
        print(f"{str(r['day'])[:10]:<14} {c:>12.6f} {r.get('calls', 0):<7} {bar}")


def cmd_usage(args):
    """Usage stats: model-level aggregation."""
    days = args.days or 7
    sql = f"""
        SELECT model_key, provider,
               COUNT(*) as calls,
               ROUND(SUM(cost), 6) as total_cost,
               SUM(tokens_in) as t_in, SUM(tokens_out) as t_out,
               ROUND(AVG(latency_ms)) as avg_lat,
               SUM(CASE WHEN success = FALSE THEN 1 ELSE 0 END) as errors
        FROM {DATASET}.usage_log
        WHERE logged_at > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days} DAY)
        GROUP BY model_key, provider
        ORDER BY total_cost DESC
        LIMIT {args.limit or 20}
    """
    rows = _bq(sql)
    if not rows:
        print("No usage data.")
        return
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False, default=str))
        return
    print(f"{'Model':<22} {'Prov':<10} {'Calls':<7} {'Cost($)':>10} "
          f"{'TokIn':>8} {'TokOut':>8} {'Lat':>6} {'Err'}")
    print("-" * 85)
    for r in rows:
        print(f"{r['model_key']:<22} {r.get('provider','?'):<10} {r['calls']:<7} "
              f"{r.get('total_cost', 0):>10.6f} {r.get('t_in', 0):>8} "
              f"{r.get('t_out', 0):>8} {r.get('avg_lat', 0):>5}ms {r.get('errors', 0)}")


def cmd_quota(args):
    """Show quota remaining across providers."""
    sql = f"""
        SELECT provider, model_key, remaining, tokens_remaining, tokens_limit, checked_at
        FROM {DATASET}.quota
        ORDER BY remaining ASC
    """
    rows = _bq(sql)
    if not rows:
        print("No quota data.")
        return
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False, default=str))
        return
    print(f"{'Provider':<14} {'Model':<22} {'Remain%':>8} {'Tokens':>10} {'Limit':>10} {'Checked'}")
    print("-" * 80)
    for r in rows:
        rem = f"{r.get('remaining', 0):.1f}%" if r.get("remaining") is not None else "-"
        print(f"{r['provider']:<14} {r.get('model_key', ''):<22} {rem:>8} "
              f"{r.get('tokens_remaining', '-')!s:>10} {r.get('tokens_limit', '-')!s:>10} "
              f"{_fmt_dt(r.get('checked_at'))}")


def cmd_status(args):
    """Overall health summary of all providers."""
    sql = f"""
        SELECT p.model_key, p.provider, p.model_id, p.tier, p.priority,
               p.cost_in, q.remaining, r.expired, r.expires_at, r.last_error,
               rec.estimated_at, rec.confidence,
               (SELECT COUNT(*) FROM {DATASET}.usage_log u
                WHERE u.model_key = p.model_key
                  AND u.logged_at > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 DAY)
               ) as calls_24h
        FROM {DATASET}.providers p
        LEFT JOIN {DATASET}.quota q ON q.model_key = p.model_key
        LEFT JOIN {DATASET}.rotation_state r ON r.model_key = p.model_key
        LEFT JOIN {DATASET}.recovery_estimate rec ON rec.model_key = p.model_key
        ORDER BY p.priority
    """
    rows = _bq(sql)
    if not rows:
        print("No provider data.")
        return
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False, default=str))
        return
    total_cost_sql = f"""
        SELECT ROUND(SUM(cost), 4) as total_24h
        FROM {DATASET}.usage_log
        WHERE logged_at > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 DAY)
    """
    cost_rows = _bq(total_cost_sql)
    total_24h = cost_rows[0]["total_24h"] if cost_rows and cost_rows[0].get("total_24h") else 0
    active = sum(1 for r in rows if not r.get("expired"))
    expired = sum(1 for r in rows if r.get("expired"))
    print(f"{'Status Summary':─^60}")
    print(f"  Active: {active}  |  Expired: {expired}  |  Total: {len(rows)}")
    print(f"  24h Cost: ${float(total_24h):.4f}")
    print()
    print(f"{'Model':<22} {'Prov':<10} {'Pri':>3} {'Tier':<5} {'Quota':>6} {'Status':<10} {'24hCalls':>9} {'Recovery'}")
    print("-" * 90)
    for r in rows:
        status = "OK"
        if r.get("expired"):
            status = "EXPIRED"
        elif r.get("remaining") is not None and float(r.get("remaining", 0)) <= 0:
            status = "EMPTY"
        remaining = f"{float(r.get('remaining', 0)):.0f}%" if r.get("remaining") is not None else "-"
        recovery = ""
        est = r.get("estimated_at") or r.get("expires_at")
        if est:
            recovery = str(est)[:16]
        cost_in = float(r.get("cost_in", 0))
        tier = r.get("tier", "free")
        print(f"{r['model_key']:<22} {r.get('provider','?'):<10} "
              f"P{r['priority']:<1} {tier:<5} {remaining:>6} {status:<10} "
              f"{r.get('calls_24h', 0):>9} {recovery}")


def cmd_alert_config(args):
    """View or set alert thresholds."""
    cfg = _config()
    if args.set:
        for kv in args.set:
            if "=" in kv:
                k, v = kv.split("=", 1)
                try:
                    v_parsed = float(v)
                except ValueError:
                    v_parsed = v
                cfg[k] = v_parsed
        _save_config(cfg)
        print(f"Config saved: {CONFIG_FILE}")
    print(json.dumps(cfg, indent=2, ensure_ascii=False))
    print(f"\n  Current config file: {CONFIG_FILE}")
    print("  Set: --set key=value (e.g. --set alert_daily_cost=0.25)")


def cmd_monitor(args):
    """Monitoring agent: check thresholds and alert."""
    cfg = _config()
    if args.json:
        print(json.dumps(cfg, indent=2, ensure_ascii=False))
        return

    alerts = []

    # 1. Check daily cost
    sql_24h = f"""
        SELECT ROUND(SUM(cost), 6) as daily_cost
        FROM {DATASET}.usage_log
        WHERE logged_at > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 DAY)
    """
    rows_24h = _bq(sql_24h)
    daily_cost = float(rows_24h[0]["daily_cost"]) if rows_24h and rows_24h[0].get("daily_cost") else 0
    threshold_cost = float(cfg.get("alert_daily_cost", 0.50))
    if daily_cost > threshold_cost:
        alerts.append(f"DAILY COST ${daily_cost:.4f} exceeds threshold ${threshold_cost:.4f}")

    # 2. Check 7d average vs today spike
    sql_7d = f"""
        SELECT ROUND(SUM(cost), 6) as total_7d
        FROM {DATASET}.usage_log
        WHERE logged_at > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)
    """
    rows_7d = _bq(sql_7d)
    total_7d = float(rows_7d[0]["total_7d"]) if rows_7d and rows_7d[0].get("total_7d") else 0
    avg_daily_7d = total_7d / 7 if total_7d > 0 else 0
    spike_factor = float(cfg.get("alert_cost_spike_factor", 2.0))
    if avg_daily_7d > 0 and daily_cost > avg_daily_7d * spike_factor:
        alerts.append(f"COST SPIKE today=${daily_cost:.4f} vs 7d-avg=${avg_daily_7d:.4f} (x{daily_cost/avg_daily_7d:.1f})")

    # 3. Check balance
    sql_bal = f"""
        SELECT provider, total_balance, timestamp
        FROM {DATASET}.balance_snapshots
        QUALIFY ROW_NUMBER() OVER (PARTITION BY provider ORDER BY timestamp DESC) = 1
    """
    rows_bal = _bq(sql_bal)
    low_bal = float(cfg.get("alert_low_balance", 1.00))
    for r in rows_bal:
        bal = float(r.get("total_balance", 0))
        if bal < low_bal:
            alerts.append(f"LOW BALANCE {r['provider']}: ${bal:.4f} < ${low_bal:.4f}")

    # 4. Check expired models
    sql_exp = f"""
        SELECT model_key, provider, expires_at, last_error
        FROM {DATASET}.rotation_state
        WHERE expired = TRUE
    """
    rows_exp = _bq(sql_exp)
    for r in rows_exp:
        alerts.append(f"EXPIRED {r['provider']}/{r['model_key']}: {r.get('last_error', 'no reason')}")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    summary = {
        "checked_at": now,
        "daily_cost": daily_cost,
        "avg_daily_7d": avg_daily_7d,
        "alerts": alerts,
        "alert_count": len(alerts),
    }

    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return

    print(f"{'BQ Monitor Check':─^50}")
    print(f"  Checked:      {now}")
    print(f"  24h cost:     ${daily_cost:.6f}")
    print(f"  7d avg cost:  ${avg_daily_7d:.6f}")
    print(f"  Alerts:       {len(alerts)}")
    print()
    if alerts:
        print(f"{'ALERTS':─^50}")
        for a in alerts:
            ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
            print(f"  [{ts}] {a}")
        notify_cmd = cfg.get("notify_cmd", "")
        if notify_cmd:
            msg = "; ".join(alerts)
            subprocess.run(notify_cmd.format(msg=msg), shell=True, timeout=10)
    else:
        print("  All clear. ✓")

    return summary


def cmd_export(args):
    """Export data to CSV."""
    days = args.days or 30
    sql = f"""
        SELECT u.*, p.provider
        FROM {DATASET}.usage_log u
        LEFT JOIN {DATASET}.providers p ON p.model_key = u.model_key
        WHERE u.logged_at > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days} DAY)
        ORDER BY u.logged_at DESC
    """
    if args.limit:
        sql = sql.replace("ORDER BY u.logged_at DESC", f"ORDER BY u.logged_at DESC LIMIT {args.limit}")
    rows = _bq(sql)
    if not rows:
        print("No data to export.")
        return
    if args.format == "json":
        print(json.dumps(rows, indent=2, ensure_ascii=False, default=str))
        return
    out = io.StringIO()
    if not rows:
        return
    w = csv.DictWriter(out, fieldnames=rows[0].keys())
    w.writeheader()
    w.writerows(rows)
    print(out.getvalue().rstrip())


def cmd_recovery(args):
    """Show pending recovery estimates."""
    sql = f"""
        SELECT re.model_key, re.estimated_at, re.confidence,
               re.cached_remaining, re.based_on, p.provider
        FROM {DATASET}.recovery_estimate re
        JOIN {DATASET}.providers p ON p.model_key = re.model_key
        WHERE re.estimated_at > CURRENT_TIMESTAMP()
        ORDER BY re.estimated_at
    """
    rows = _bq(sql)
    if not rows:
        print("No pending recovery estimates.")
        return
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False, default=str))
        return
    print(f"{'Model':<22} {'Provider':<12} {'Estimated':<20} {'Conf':<6} {'Based On'}")
    print("-" * 70)
    for r in rows:
        print(f"{r['model_key']:<22} {r.get('provider','?'):<12} "
              f"{_fmt_dt(r.get('estimated_at')):<20} "
              f"{r.get('confidence', 0):.0%}   {r.get('based_on', '-')}")


def main():
    p = argparse.ArgumentParser(description="BQ GCP Monitor CLI")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("balance", help="Show balance history")
    sp.add_argument("--provider", "-p", default=None, help="Provider filter")
    sp.add_argument("--days", type=int, default=30, help="Days of history")
    sp.add_argument("--limit", type=int, default=30, help="Max rows")
    sp.add_argument("--json", action="store_true", help="JSON output")

    sp = sub.add_parser("cost", help="Cost breakdown by group")
    sp.add_argument("--group", default="model_key", choices=["model_key", "provider", "cli"])
    sp.add_argument("--days", type=int, default=7)
    sp.add_argument("--limit", type=int, default=20)
    sp.add_argument("--json", action="store_true")

    sp = sub.add_parser("trend", help="Daily cost trend (ASCII chart)")
    sp.add_argument("--days", type=int, default=30)
    sp.add_argument("--json", action="store_true")

    sp = sub.add_parser("usage", help="Usage stats by model")
    sp.add_argument("--days", type=int, default=7)
    sp.add_argument("--limit", type=int, default=20)
    sp.add_argument("--json", action="store_true")

    sp = sub.add_parser("quota", help="Show quota remaining")
    sp.add_argument("--json", action="store_true")

    sp = sub.add_parser("status", help="Overall health summary")
    sp.add_argument("--json", action="store_true")

    sp = sub.add_parser("alert-config", help="Configure alert thresholds")
    sp.add_argument("--set", action="append", default=[], help="Set key=value")

    sp = sub.add_parser("monitor", help="Monitoring agent")
    sp.add_argument("--once", action="store_true", help="Run once and exit")
    sp.add_argument("--loop", action="store_true", help="Run continuously")
    sp.add_argument("--interval", type=int, default=60, help="Check interval (min)")
    sp.add_argument("--json", action="store_true")

    sp = sub.add_parser("export", help="Export usage data")
    sp.add_argument("--days", type=int, default=30)
    sp.add_argument("--format", choices=["csv", "json"], default="csv")
    sp.add_argument("--limit", type=int, help="Max rows")

    sp = sub.add_parser("recovery", help="Show recovery estimates")
    sp.add_argument("--json", action="store_true")

    args = p.parse_args()

    if args.command == "balance":
        cmd_balance(args)
    elif args.command == "cost":
        cmd_cost(args)
    elif args.command == "trend":
        cmd_trend(args)
    elif args.command == "usage":
        cmd_usage(args)
    elif args.command == "quota":
        cmd_quota(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "alert-config":
        cmd_alert_config(args)
    elif args.command == "monitor":
        if args.loop:
            import time
            interval_sec = args.interval * 60
            while True:
                cmd_monitor(args)
                print(f"\nNext check in {args.interval} min...\n")
                time.sleep(interval_sec)
        else:
            cmd_monitor(args)
    elif args.command == "export":
        cmd_export(args)
    elif args.command == "recovery":
        cmd_recovery(args)


if __name__ == "__main__":
    main()
