"""mcp_server.py — Python MCP stdio server (replaces Go mcp.go)"""
import json
import sys
import traceback
from typing import Any

from db import get_db, list_providers, get_cli_priorities, ensure_model_key
from db import log_usage, get_usage, set_quota, mark_exhausted
from db import current_rotation, next_rotation
from state_reader import read_all, CLIState
from state_writer import apply
from recommend import recommend
from usage import format_usage
from quota import check_anthropic_quota, format_quota, quota_history


# ── Tool definitions ───────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "mm_list",
        "description": "Get current model/provider state for all CLIs",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "mm_set",
        "description": "Set a CLI to a specific provider and model",
        "inputSchema": {
            "type": "object",
            "required": ["cli", "provider", "model"],
            "properties": {
                "cli": {"type": "string", "description": "CLI name"},
                "provider": {"type": "string"},
                "model": {"type": "string"},
            },
        },
    },
    {
        "name": "mm_priority",
        "description": "Set a CLI to priority level N (1=highest)",
        "inputSchema": {
            "type": "object",
            "required": ["cli", "n"],
            "properties": {
                "cli": {"type": "string"},
                "n": {"type": "integer", "description": "Priority number (1-based)"},
            },
        },
    },
    {
        "name": "mm_next",
        "description": "Advance a CLI to its next priority level",
        "inputSchema": {
            "type": "object",
            "required": ["cli"],
            "properties": {"cli": {"type": "string"}},
        },
    },
    {
        "name": "mm_rotate",
        "description": "Rotate Qwen CLI to the next dashscope free-quota model",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "mm_exhausted",
        "description": "Mark a model as exhausted and rotate",
        "inputSchema": {
            "type": "object",
            "required": ["model_key"],
            "properties": {
                "model_key": {"type": "string", "description": "model_key to mark exhausted"},
            },
        },
    },
    {
        "name": "mm_csv",
        "description": "Get current state as CSV",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "mm_recommend",
        "description": "Recommend best CLI+model for a task",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Task: low|mid|high|code (default: mid)"},
            },
        },
    },
    {
        "name": "mm_quota",
        "description": "Check Anthropic rate-limit usage",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "mm_log_usage",
        "description": "Log token usage for a CLI session",
        "inputSchema": {
            "type": "object",
            "required": ["cli", "tokens_in", "tokens_out"],
            "properties": {
                "cli": {"type": "string"},
                "tokens_in": {"type": "integer"},
                "tokens_out": {"type": "integer"},
                "model_key": {"type": "string", "description": "model_key (auto-detected if omitted)"},
                "task": {"type": "string", "description": "Task level: low|mid|high|code (default: mid)"},
            },
        },
    },
    {
        "name": "mm_usage",
        "description": "Show token consumption graph for the last N days",
        "inputSchema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Number of days (default: 14)"},
            },
        },
    },
    {
        "name": "mm_status",
        "description": "Show full model status table from DB",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


# ── Tool handlers ──────────────────────────────────────────────────────────

def _find_next_priority(cli: str, current_pri: int) -> dict | None:
    conn = get_db()
    entries = get_cli_priorities(conn, cli)
    conn.close()
    if not entries:
        return None
    next_p = current_pri + 1 if current_pri > 0 else 1
    if next_p > len(entries):
        next_p = 1
    for e in entries:
        if e["priority"] == next_p:
            return dict(e)
    return None


def _model_key_for_cli(cli: str) -> str | None:
    conn = get_db()
    entries = get_cli_priorities(conn, cli)
    conn.close()
    if entries:
        return entries[0]["model_key"]
    return None


def handle_tool(name: str, args: dict[str, Any]) -> str:
    # ── mm_list ─────────────────────────────────────────────────────────
    if name == "mm_list":
        states = read_all()
        lines = [f"{'CLI':<14}  {'PROVIDER':<20}  {'MODEL':<45}  PRI",
                 "─" * 14 + "  " + "─" * 20 + "  " + "─" * 45 + "  " + "─" * 3]
        for s in states:
            p = f"P{s.priority}" if s.priority > 0 else ""
            model = (s.model[:42] + "...") if len(s.model) > 45 else s.model
            lines.append(f"{s.cli:<14}  {s.provider:<20}  {model:<45}  {p}")
        return "\n".join(lines)

    # ── mm_set ──────────────────────────────────────────────────────────
    if name == "mm_set":
        cli = args.get("cli", "")
        provider = args.get("provider", "")
        model = args.get("model", "")
        if not all([cli, provider, model]):
            return "Error: cli, provider, model are required"
        apply(cli, provider, model)
        return f"✓ {cli} → {provider}/{model}"

    # ── mm_priority ─────────────────────────────────────────────────────
    if name == "mm_priority":
        cli = args.get("cli", "")
        n = int(args.get("n", 1))
        conn = get_db()
        entries = get_cli_priorities(conn, cli)
        conn.close()
        if not entries:
            return f"Error: no priority list for {cli}"
        if n < 1 or n > len(entries):
            return f"Error: priority {n} out of range (1-{len(entries)}) for {cli}"
        target = entries[n - 1]
        apply(cli, target["provider"], target["model_id"])
        return f"✓ {cli} → P{n} {target['provider']}/{target['model_id']}"

    # ── mm_next ─────────────────────────────────────────────────────────
    if name == "mm_next":
        cli = args.get("cli", "")
        states = read_all()
        cur_pri = 0
        for s in states:
            if s.cli == cli:
                cur_pri = s.priority
                break
        target = _find_next_priority(cli, cur_pri)
        if not target:
            return f"Error: no priority entries for {cli}"
        apply(cli, target["provider"], target["model_id"])
        return f"✓ {cli} advanced to P{target['priority']} {target['provider']}/{target['model_id']}"

    # ── mm_rotate ───────────────────────────────────────────────────────
    if name == "mm_rotate":
        from state_reader import read_qwen
        cur = read_qwen()
        conn = get_db()
        next_m = next_rotation(conn, cur.model)
        conn.close()
        if not next_m:
            return "Error: no rotation models available"
        apply("qwen", "dashscope", next_m)
        return f"✓ qwen rotated → {next_m}"

    # ── mm_exhausted ────────────────────────────────────────────────────
    if name == "mm_exhausted":
        mk = args.get("model_key", "")
        if not mk:
            return "Error: model_key is required"
        conn = get_db()
        mark_exhausted(conn, mk, "marked exhausted via MCP")
        conn.close()
        return f"✓ {mk} marked exhausted"

    # ── mm_csv ──────────────────────────────────────────────────────────
    if name == "mm_csv":
        states = read_all()
        lines = ["cli,provider,model,priority"]
        for s in states:
            p = f"P{s.priority}" if s.priority > 0 else ""
            lines.append(f"{s.cli},{s.provider},{s.model},{p}")
        return "\n".join(lines)

    # ── mm_recommend ────────────────────────────────────────────────────
    if name == "mm_recommend":
        task = args.get("task", "mid")
        recs = recommend(task, limit=5)
        label_map = {"low": "低負荷 (low)", "mid": "中負荷 (mid)",
                      "high": "高負荷 (high)", "code": "コーディング (code)"}
        label = label_map.get(task, task)
        lines = [f"Best options for {label}:", "",
                 f"{'#':<3}  {'CLI':<13}  {'Provider':<22}  {'Model':<36}  {'Score':<7}  {'Note'}",
                 "─" * 100]
        for i, r in enumerate(recs, 1):
            model = (r["model"][:34] + "...") if len(r["model"]) > 36 else r["model"]
            avail = "✓" if r["available"] else "✗"
            lines.append(f"{i:<3}  {r['cli']:<13}  {r['provider']:<22}  {model:<36}  {r['score']:<7.1f}  [{avail}] {r['reason']}")
        if recs:
            top = recs[0]
            lines.append(f"\n→ Recommended: {top['cli']} {top['provider']} {top['model']}")
        return "\n".join(lines)

    # ── mm_quota ────────────────────────────────────────────────────────
    if name == "mm_quota":
        try:
            info = check_anthropic_quota()
            return format_quota(info)
        except ValueError as e:
            hist = quota_history()
            return f"Error: {e}\n\n{hist}"

    # ── mm_log_usage ────────────────────────────────────────────────────
    if name == "mm_log_usage":
        cli = args.get("cli", "")
        tok_in = int(args.get("tokens_in", 0))
        tok_out = int(args.get("tokens_out", 0))
        task = args.get("task", "mid")
        mk = args.get("model_key", "")
        if not mk:
            mk = _model_key_for_cli(cli) or f"{cli}-unknown"
        conn = get_db()
        log_usage(conn, cli, mk, task, tok_in, tok_out)
        conn.close()
        return f"✓ logged {cli}  in:{_fmt_si(tok_in)}  out:{_fmt_si(tok_out)}"

    # ── mm_usage ────────────────────────────────────────────────────────
    if name == "mm_usage":
        days = int(args.get("days", 14))
        return format_usage(days)

    # ── mm_status ───────────────────────────────────────────────────────
    if name == "mm_status":
        conn = get_db()
        rows = list_providers(conn)
        conn.close()
        if not rows:
            return "No models in DB. Run migration.py --apply first."
        lines = [
            f"{'Model Key':<22}  {'Provider':<12}  {'Tier':<5}  {'Pri':<4}  {'Cost':<7}  {'Quota':<7}  {'Status':<10}  {'Recovery'}",
            "─" * 100,
        ]
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
            lines.append(f"{r['model_key']:<22}  {r['provider']:<12}  {r['tier']:<5}  "
                         f"P{r['priority']:<2}  {cost:<7}  {remaining:<7}  {status:<10}  {recovery}")
        return "\n".join(lines)

    return f"Error: unknown tool: {name}"


def _fmt_si(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


# ── MCP protocol ───────────────────────────────────────────────────────────

def run_mcp():
    """MCP stdio JSON-RPC 2.0 server (replaces Go --mcp mode)."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue

        req_id = req.get("id")
        method = req.get("method", "")
        params = req.get("params", {}) or {}

        if method == "initialize":
            _respond(req_id, {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "model-manager-py", "version": "2.0.0"},
                "capabilities": {"tools": {}},
            })
        elif method == "notifications/initialized":
            _respond(req_id, None)
        elif method == "tools/list":
            _respond(req_id, {"tools": TOOLS})
        elif method == "tools/call":
            tool_name = params.get("name", "")
            tool_args = params.get("arguments", {})
            try:
                result = handle_tool(tool_name, tool_args)
                _respond(req_id, {
                    "content": [{"type": "text", "text": result}],
                })
            except Exception as e:
                _respond(req_id, {
                    "content": [{"type": "text", "text": f"Error: {e}\n{traceback.format_exc()}"}],
                    "isError": True,
                })
        elif method == "shutdown":
            break
        else:
            _respond(req_id, None, {"code": -32601, "message": f"method not found: {method}"})


def _respond(req_id, result, error=None):
    resp = {"jsonrpc": "2.0", "id": req_id}
    if error:
        resp["error"] = error
    else:
        resp["result"] = result
    sys.stdout.write(json.dumps(resp) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    run_mcp()
