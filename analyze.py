"""analyze.py — CLI・モデル・コスト・生成成果の相関分析"""

from db import get_db
from usage import _fmt_si


def _fmt_cost(c):
    if c == 0:
        return "free"
    if c < 0.01:
        return f"${c:.4f}"
    return f"${c:.2f}"


def run_analyze():
    conn = get_db()
    rows = conn.execute("""
        SELECT u.cli, u.model_key, p.provider,
               COUNT(*) as sessions,
               SUM(u.tokens_in) as t_in,
               SUM(u.tokens_out) as t_out,
               SUM(u.cache_read_tokens + u.cache_create_tokens) as t_cache,
               SUM(u.cost) as total_cost,
               ROUND(CAST(SUM(u.tokens_out) AS REAL) / MAX(SUM(u.tokens_in), 1) * 100, 1) as out_in_ratio
        FROM usage_log u
        JOIN providers p ON p.model_key = u.model_key
        GROUP BY u.cli, u.model_key
        ORDER BY sessions DESC
    """).fetchall()

    cli_stats = conn.execute("""
        SELECT cli, COUNT(*) as sessions,
               SUM(tokens_in) as t_in, SUM(tokens_out) as t_out,
               SUM(cache_read_tokens + cache_create_tokens) as t_cache,
               SUM(cost) as total_cost
        FROM usage_log GROUP BY cli
    """).fetchall()
    conn.close()

    lines = []

    grand_sessions = sum(r["sessions"] for r in cli_stats)

    lines.append(f"Analyze — {grand_sessions} sessions across {len(cli_stats)} CLIs")
    lines.append("")

    # Per-CLI summary
    lines.append("Per CLI:")
    lines.append(f"  {'CLI':<12} {'Sessions':>8} {'In':>10} {'Out':>8} {'Cache':>10} {'Cost':>10} {'Out/In':>7}")
    lines.append(f"  {'─'*12} {'─'*8} {'─'*10} {'─'*8} {'─'*10} {'─'*10} {'─'*7}")
    for r in cli_stats:
        out_ratio = r["t_out"] / (r["t_in"] or 1) * 100
        lines.append(f"  {r['cli']:<12} {r['sessions']:>8} {_fmt_si(r['t_in']):>10} {_fmt_si(r['t_out']):>8} {_fmt_si(r['t_cache']):>10} {_fmt_cost(r['total_cost']):>10} {out_ratio:>6.1f}%")
    lines.append("")

    # Per-model detail
    lines.append("Per Model (sorted by sessions):")
    lines.append(f"  {'CLI':<10} {'Model':<34} {'Provider':<12} {'Sessions':>6} {'In':>10} {'Out':>8} {'Cache':>10} {'Cost':>10} {'Out/In':>7}")
    lines.append(f"  {'─'*10} {'─'*34} {'─'*12} {'─'*6} {'─'*10} {'─'*8} {'─'*10} {'─'*10} {'─'*7}")
    for r in rows:
        model = r["model_key"][:34]
        cache = r["t_cache"]
        lines.append(f"  {r['cli']:<10} {model:<34} {r['provider']:<12} {r['sessions']:>6} {_fmt_si(r['t_in']):>10} {_fmt_si(r['t_out']):>8} {_fmt_si(cache):>10} {_fmt_cost(r['total_cost']):>10} {r['out_in_ratio']:>6}%")
    lines.append("")

    # Efficiency ranking
    lines.append("Efficiency (output per session):")
    sorted_rows = sorted(rows, key=lambda r: r["t_out"] // (r["sessions"] or 1), reverse=True)
    lines.append(f"  {'Model':<34} {'Out/session':>12} {'In/session':>10} {'Cache/session':>12} {'Cost/session':>12}")
    lines.append(f"  {'─'*34} {'─'*12} {'─'*10} {'─'*12} {'─'*12}")
    for r in sorted_rows:
        s = r["sessions"] or 1
        out_s = r["t_out"] // s
        in_s = r["t_in"] // s
        cache_s = r["t_cache"] // s
        cost_s = r["total_cost"] / s
        lines.append(f"  {r['model_key']:<34} {_fmt_si(out_s):>12} {_fmt_si(in_s):>10} {_fmt_si(cache_s):>12} {_fmt_cost(cost_s):>12}")

    return "\n".join(lines)
