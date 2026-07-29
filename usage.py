"""usage.py — 使用量グラフ (ASCII sparklines + bars)"""
from db import get_db, get_daily_usage, get_cli_totals, get_provider_totals

BAR_WIDTH = 30


def _fmt_si(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _sparkline(values: list[int], max_val: int = None) -> str:
    if not values:
        return ""
    if max_val is None:
        max_val = max(values)
    if max_val == 0:
        max_val = 1
    chars = ["▁", "▂", "▃", "▄", "▅", "▆", "▇", "█"]
    return "".join(chars[min(int(v / max_val * (len(chars) - 1)), len(chars) - 1)] for v in values)


def _hbar(value: int, max_val: int, width: int = BAR_WIDTH) -> str:
    if max_val == 0:
        max_val = 1
    filled = int(value / max_val * width)
    return "█" * filled + "░" * (width - filled)


def format_usage(days: int = 14) -> str:
    conn = get_db()
    daily = get_daily_usage(conn, days)
    cli_stats = get_cli_totals(conn, days)
    prov_stats = get_provider_totals(conn, days)
    conn.close()

    if not daily and not cli_stats:
        return (
            "No usage data yet.\n\n"
            "Log usage:\n"
            "  mm_log_usage (MCP tool)\n"
        )

    grand = sum((d["t_in"] or 0) + (d["t_out"] or 0) + (d["t_cache_create"] or 0) + (d["t_cache_read"] or 0) for d in daily)
    lines = [f"Token consumption — last {days} days  (total: {_fmt_si(grand)})"]

    # Daily sparkline
    if daily:
        totals = [(d["t_in"] or 0) + (d["t_out"] or 0) + (d["t_cache_create"] or 0) + (d["t_cache_read"] or 0) for d in daily]
        lines.append("")
        lines.append("Daily trend:")
        max_day = max(totals) if totals else 1
        lines.append(f"  {_sparkline(totals, max_day)}  {daily[0]['day']} → {daily[-1]['day']}")

        # Daily bars
        lines.append("")
        lines.append("Per day (in + out + cache):")
        for d in daily:
            tot = (d["t_in"] or 0) + (d["t_out"] or 0) + (d["t_cache_create"] or 0) + (d["t_cache_read"] or 0)
            cache = (d["t_cache_create"] or 0) + (d["t_cache_read"] or 0)
            date = d["day"][5:] if len(d["day"]) == 10 else d["day"]
            label = f"  {date}  {_hbar(tot, max_day)}  {_fmt_si(tot)}"
            if cache:
                label += f"  (cache {_fmt_si(cache)})"
            lines.append(label)

    # Per CLI
    if cli_stats:
        max_cli = (cli_stats[0]["t_in"] or 0) + (cli_stats[0]["t_out"] or 0) + (cli_stats[0]["t_cache_create"] or 0) + (cli_stats[0]["t_cache_read"] or 0) if cli_stats else 1
        lines.append("")
        lines.append("Per CLI:")
        for c in cli_stats:
            tot = (c["t_in"] or 0) + (c["t_out"] or 0) + (c["t_cache_create"] or 0) + (c["t_cache_read"] or 0)
            pct = tot / grand * 100 if grand else 0
            cache = (c["t_cache_create"] or 0) + (c["t_cache_read"] or 0)
            label = f"  {c['cli']:<13}  {_hbar(tot, max_cli)}  {pct:5.1f}%  in:{_fmt_si(c['t_in'] or 0)}  out:{_fmt_si(c['t_out'] or 0)}"
            if cache:
                label += f"  cache:{_fmt_si(cache)}"
            lines.append(label)

    # Per provider
    if prov_stats:
        max_prov = (prov_stats[0]["t_in"] or 0) + (prov_stats[0]["t_out"] or 0) + (prov_stats[0]["t_cache_create"] or 0) + (prov_stats[0]["t_cache_read"] or 0) if prov_stats else 1
        lines.append("")
        lines.append("Per provider:")
        for p in prov_stats:
            tot = (p["t_in"] or 0) + (p["t_out"] or 0) + (p["t_cache_create"] or 0) + (p["t_cache_read"] or 0)
            pct = tot / grand * 100 if grand else 0
            cache = (p["t_cache_create"] or 0) + (p["t_cache_read"] or 0)
            label = f"  {p['provider']:<22}  {_hbar(tot, max_prov)}  {pct:5.1f}%  {_fmt_si(tot)}"
            if cache:
                label += f"  cache:{_fmt_si(cache)}"
            lines.append(label)

    return "\n".join(lines)
