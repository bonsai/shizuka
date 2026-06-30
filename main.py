#!/usr/bin/env python3
"""shizuka — unified CLI model switcher + MCP server (Python edition)

Usage:
  python main.py                    list current state
  python main.py --mcp              MCP server (stdio JSON-RPC)
  python main.py status             show full DB status
  python main.py csv                output CSV
  python main.py priority           show priority table
  python main.py recommend [task]   recommend model for task
  python main.py usage [--days N]   show token consumption
  python main.py rotate             rotate qwen dashscope model
  python main.py <cli> <provider> [model]  set CLI to provider+model
  python main.py <cli> next         advance to next priority
  python main.py <cli> p<N>         set to priority N
"""
import argparse
import sys
import os

# Ensure the shizuka directory is on sys.path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db import get_db, list_providers, get_cli_priorities
from db import get_usage, get_daily_usage, get_cli_totals, get_provider_totals
from db import current_rotation, next_rotation, mark_exhausted, set_quota
from state_reader import read_all, read_qwen
from state_writer import apply
from recommend import recommend
from usage import format_usage
from quota import check_anthropic_quota, format_quota, quota_history


def cmd_status():
    conn = get_db()
    rows = list_providers(conn)
    conn.close()
    if not rows:
        print("No models in DB. Run migration.py --apply first.")
        return
    print(f"{'Model Key':<22}  {'Provider':<12}  {'Tier':<5}  {'Pri':<4}  {'Cost':<7}  "
          f"{'Quota':<7}  {'Status':<10}  {'Recovery'}")
    print("─" * 100)
    for r in rows:
        status = "OK"
        if r["expired"]:
            status = "EXPIRED"
        elif r["remaining"] is not None and r["remaining"] <= 0:
            status = "EMPTY"
        remaining = f"{r['remaining']:.0f}%" if r["remaining"] is not None else "-"
        cost = f"${r['cost_in']:.2f}" if r["cost_in"] else "free"
        recovery = ""
        est = r["estimated_at"] or r["expires_at"]
        if est:
            recovery = str(est)[:16]
        print(f"{r['model_key']:<22}  {r['provider']:<12}  {r['tier']:<5}  "
              f"P{r['priority']:<2}  {cost:<7}  {remaining:<7}  {status:<10}  {recovery}")


def cmd_list():
    states = read_all()
    print(f"{'CLI':<14}  {'PROVIDER':<20}  {'MODEL':<45}  PRI")
    print("─" * 14 + "  " + "─" * 20 + "  " + "─" * 45 + "  " + "─" * 3)
    for s in states:
        p = f"P{s.priority}" if s.priority > 0 else ""
        model = (s.model[:42] + "...") if len(s.model) > 45 else s.model
        print(f"{s.cli:<14}  {s.provider:<20}  {model:<45}  {p}")


def cmd_csv():
    states = read_all()
    print("cli,provider,model,priority")
    for s in states:
        p = f"P{s.priority}" if s.priority > 0 else ""
        print(f"{s.cli},{s.provider},{s.model},{p}")


def cmd_priority():
    conn = get_db()
    clis = sorted(set(
        r["cli"] for r in conn.execute("SELECT DISTINCT cli FROM cli_priority").fetchall()
    ))
    print(f"{'CLI':<14}  Priority list")
    print("─" * 80)
    for cli in clis:
        entries = get_cli_priorities(conn, cli)
        for i, e in enumerate(entries):
            rot = " <rotation>" if e["use_rotation"] else ""
            note = f" ({e.get('note', '')})" if e.get("note") else ""
            if i == 0:
                print(f"{cli:<14}  P{e['priority']}  {e['provider']:<20}  {e['model_id']}{rot}{note}")
            else:
                print(f"{'':<14}  P{e['priority']}  {e['provider']:<20}  {e['model_id']}{rot}{note}")
    conn.close()


def cmd_rotate():
    cur = read_qwen()
    conn = get_db()
    next_m = next_rotation(conn, cur.model)
    conn.close()
    if not next_m:
        print("Error: no rotation models available")
        sys.exit(1)
    apply("qwen", "dashscope", next_m)
    print(f"✓ qwen rotated → {next_m}")


def cmd_quota(target="anthropic"):
    if target == "history":
        print(quota_history())
        return
    try:
        info = check_anthropic_quota()
        print(format_quota(info))
    except ValueError as e:
        print(f"Error: {e}")
        print()
        print(quota_history())
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="shizuka — unified CLI model switcher")
    parser.add_argument("action", nargs="?", default="list",
                        help="status|list|csv|priority|recommend|usage|rotate|quota|--mcp")
    parser.add_argument("args", nargs="*", help="arguments")
    parser.add_argument("--days", type=int, default=14, help="days for usage graph")
    parser.add_argument("--task", default="mid", help="task type: low|mid|high|code")

    args = parser.parse_args()

    if args.action == "--mcp":
        from mcp_server import run_mcp
        run_mcp()
        return

    action = args.action

    # Commands without subcommands
    if action == "status":
        cmd_status()
    elif action == "list":
        cmd_list()
    elif action == "csv":
        cmd_csv()
    elif action == "priority":
        cmd_priority()
    elif action == "rotate":
        cmd_rotate()
    elif action == "usage":
        print(format_usage(args.days))
    elif action == "recommend":
        recs = recommend(args.task)
        label_map = {"low": "低負荷 (low)", "mid": "中負荷 (mid)",
                      "high": "高負荷 (high)", "code": "コーディング (code)"}
        label = label_map.get(args.task, args.task)
        print(f"Best options for {label}:\n")
        print(f"{'#':<3}  {'CLI':<13}  {'Provider':<22}  {'Model':<36}  {'Score':<7}  {'Note'}")
        print("─" * 100)
        for i, r in enumerate(recs, 1):
            model = (r["model"][:34] + "...") if len(r["model"]) > 36 else r["model"]
            avail = "✓" if r["available"] else "✗"
            print(f"{i:<3}  {r['cli']:<13}  {r['provider']:<22}  {model:<36}  {r['score']:<7.1f}  [{avail}] {r['reason']}")
    elif action == "quota":
        target = args.args[0] if args.args else "anthropic"
        cmd_quota(target)
    elif action == "help":
        parser.print_help()
    elif len(args.args) >= 1 and action != "help":
        cli = action
        sub = args.args[0]
        rest = args.args[1:]

        if sub == "next":
            states = read_all()
            cur_pri = 0
            for s in states:
                if s.cli == cli:
                    cur_pri = s.priority
                    break
            conn = get_db()
            entries = get_cli_priorities(conn, cli)
            conn.close()
            if not entries:
                print(f"Error: no priority entries for {cli}")
                sys.exit(1)
            next_p = cur_pri + 1 if cur_pri > 0 else 1
            if next_p > len(entries):
                next_p = 1
            target = entries[next_p - 1]
            apply(cli, target["provider"], target["model_id"])
            print(f"✓ {cli} → P{next_p} {target['provider']}/{target['model_id']}")

        elif sub.lower().startswith("p") and sub[1:].isdigit():
            n = int(sub[1:])
            conn = get_db()
            entries = get_cli_priorities(conn, cli)
            conn.close()
            if not entries:
                print(f"Error: no priority entries for {cli}")
                sys.exit(1)
            if n < 1 or n > len(entries):
                print(f"Error: priority {n} out of range (1-{len(entries)})")
                sys.exit(1)
            target = entries[n - 1]
            apply(cli, target["provider"], target["model_id"])
            print(f"✓ {cli} → P{n}")

        else:
            provider = sub
            model = rest[0] if rest else ""
            if not model:
                conn = get_db()
                entries = get_cli_priorities(conn, cli)
                conn.close()
                for e in entries:
                    if e["provider"] == provider:
                        model = e["model_id"]
                        break
            apply(cli, provider, model)
            print(f"✓ {cli} → {provider}/{model}")
    else:
        cmd_list()


if __name__ == "__main__":
    main()
