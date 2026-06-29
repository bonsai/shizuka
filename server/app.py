"""shizuka MCP Server — always-on model selection engine.

MCP over SSE transport via FastAPI + sse-starlette.
Backed by BigQuery (yok-ai-2026.model_status).
Designed for Cloud Run (free tier, min=0, max=1).

Run locally:
    uvicorn server.app:app --reload

Deploy to Cloud Run:
    gcloud run deploy shizuka-mcp --source .
"""
import asyncio
import json
import time
import uuid
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from sse_starlette.sse import EventSourceResponse

from server import bq_client

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="shizuka-mcp", version="1.0.0")

# Session store: session_id -> asyncio.Queue for SSE responses
_sessions: dict[str, asyncio.Queue] = {}
_TOOL_TIMEOUT = float(os.environ.get("TOOL_TIMEOUT", "60"))


# ── MCP Tool Definitions ─────────────────────────────────────────────────────

MCP_TOOLS = [
    {
        "name": "mm_recommend",
        "description": "Recommend best model for a task. Scores by quality + cost + quota. task: low|mid|high|code",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Task difficulty: low | mid | high | code", "default": "mid"},
            },
        },
    },
    {
        "name": "mm_recommend_json",
        "description": "Recommend best model as JSON (for programmatic use)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Task difficulty: low | mid | high | code", "default": "mid"},
            },
        },
    },
    {
        "name": "mm_status",
        "description": "List all models with current state (provider, tier, priority, quota, recovery)",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "mm_status_json",
        "description": "List all models as JSON (for programmatic use)",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "mm_usage",
        "description": "Show token consumption summary for the last N days",
        "inputSchema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Number of days", "default": 7},
                "cli": {"type": "string", "description": "Filter by CLI name (optional)", "default": ""},
            },
        },
    },
    {
        "name": "mm_log_usage",
        "description": "Log token usage for a session. Call after each AI response.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "cli": {"type": "string", "description": "CLI name"},
                "model_key": {"type": "string", "description": "Model key (e.g. qwen-max)"},
                "tokens_in": {"type": "integer", "description": "Input tokens"},
                "tokens_out": {"type": "integer", "description": "Output tokens"},
                "task_type": {"type": "string", "description": "Task type: low|mid|high|code", "default": ""},
                "cost": {"type": "number", "description": "Cost in USD", "default": 0},
                "latency_ms": {"type": "integer", "description": "Latency in ms", "default": 0},
                "success": {"type": "boolean", "description": "Success", "default": True},
                "error_msg": {"type": "string", "description": "Error message", "default": ""},
            },
            "required": ["cli", "model_key", "tokens_in", "tokens_out"],
        },
    },
    {
        "name": "mm_exhausted",
        "description": "Mark a model as exhausted (quota depleted) with optional recovery time",
        "inputSchema": {
            "type": "object",
            "properties": {
                "model_key": {"type": "string", "description": "Model key"},
                "reason": {"type": "string", "description": "Reason for exhaustion", "default": ""},
                "recovery_hours": {"type": "integer", "description": "Hours until expected recovery", "default": 24},
            },
            "required": ["model_key"],
        },
    },
    {
        "name": "mm_quota",
        "description": "Get quota remaining for a provider/model",
        "inputSchema": {
            "type": "object",
            "properties": {
                "provider": {"type": "string", "description": "Filter by provider (optional)", "default": ""},
                "model_key": {"type": "string", "description": "Filter by model_key (optional)", "default": ""},
            },
        },
    },
    {
        "name": "mm_quota_set",
        "description": "Set quota remaining percentage for a provider/model",
        "inputSchema": {
            "type": "object",
            "properties": {
                "provider": {"type": "string", "description": "Provider name"},
                "model_key": {"type": "string", "description": "Model key"},
                "remaining_pct": {"type": "number", "description": "Remaining percentage (0-100)"},
            },
            "required": ["provider", "model_key", "remaining_pct"],
        },
    },
    {
        "name": "mm_balance",
        "description": "Check provider balance from BQ snapshots (pushed periodically by hamachi)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "provider": {"type": "string", "description": "Filter by provider (optional)", "default": ""},
            },
        },
    },
    {
        "name": "mm_recovery",
        "description": "Show pending recovery estimates (when exhausted models are expected to recover)",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "mm_health",
        "description": "Check MCP server health and BigQuery connectivity",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


# ── Tool dispatch ─────────────────────────────────────────────────────────────

def _call_tool(name: str, args: dict) -> str:
    task = args.get("task", "mid")
    days = int(args.get("days", 7))
    cli = args.get("cli", "")
    provider = args.get("provider", "")
    model_key = args.get("model_key", "")

    if name == "mm_recommend":
        r = bq_client.select(task)
        if "error" in r:
            return f"ERROR: {r['error']}"
        q = r.get("quality", {})
        lines = [
            f"-> {r['model_key']}",
            f"  provider: {r['provider']}",
            f"  model:    {r['model_id']}",
            f"  quality:  L{q.get('l','?')}/M{q.get('m','?')}/H{q.get('h','?')}/C{q.get('c','?')}",
            f"  priority: P{r['priority']}",
            f"  tier:     {r['tier']}",
        ]
        lines.append(f"  cost:     ${r['cost']:.2f}" if r.get("cost") else "  cost:     free")
        lines.append(f"  quota:    {r.get('remaining', 0):.0f}%" if r.get("remaining") is not None else "  quota:    -")
        lines.append(f"  reason:   {r['reason']}")
        if r.get("recovery"):
            lines.append(f"  recovery: {r['recovery']['estimated_at'][:16]}")
        return "\n".join(lines)

    elif name == "mm_recommend_json":
        return json.dumps(bq_client.select(task), ensure_ascii=False, default=str)

    elif name == "mm_status":
        rows = bq_client.status()
        if not rows:
            return "No models in BQ. Run `selector.py sync --backend bq` first."
        lines = [
            f"{'Model':<22} {'Provider':<12} {'Tier':<5} {'Pri':<4} {'Cost':<7} "
            f"{'Quota':<7} {'Status':<10} {'Recovery'}"
        ]
        lines.append("-" * 100)
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
            lines.append(
                f"{r['model_key']:<22} {r['provider']:<12} {r['tier']:<5} "
                f"P{r['priority']:<2} {cost:<7} {remaining:<7} {status:<10} {recovery}"
            )
        return "\n".join(lines)

    elif name == "mm_status_json":
        return json.dumps(bq_client.status(), ensure_ascii=False, default=str)

    elif name == "mm_usage":
        cli_param = cli if cli else None
        rows = bq_client.usage_summary(days, cli_param)
        if not rows:
            return "No usage data."
        lines = [
            f"{'Model':<22} {'Calls':<7} {'Tok In':<10} {'Tok Out':<10} "
            f"{'Cost':<10} {'Lat(ms)':<8} {'Errors'}"
        ]
        lines.append("-" * 80)
        for r in rows:
            c = f"${r['total_cost']:.4f}" if r.get("total_cost") else "free"
            lines.append(
                f"{r['model_key']:<22} {r['calls']:<7} {r.get('t_in',0):<10} "
                f"{r.get('t_out',0):<10} {c:<10} {r.get('avg_lat',0):<8.0f} {r.get('errors',0)}"
            )
        return "\n".join(lines)

    elif name == "mm_log_usage":
        return bq_client.log_usage(
            cli=args.get("cli", ""),
            model_key=args.get("model_key", ""),
            task_type=args.get("task_type", ""),
            tokens_in=int(args.get("tokens_in", 0)),
            tokens_out=int(args.get("tokens_out", 0)),
            cost=float(args.get("cost", 0)),
            latency_ms=int(args.get("latency_ms", 0)),
            success=bool(args.get("success", True)),
            error_msg=args.get("error_msg", ""),
        )

    elif name == "mm_exhausted":
        bq_client.mark_exhausted(
            model_key=args.get("model_key", ""),
            reason=args.get("reason", ""),
            recovery_hours=int(args.get("recovery_hours", 24)),
        )
        return f"✓ {model_key} marked exhausted (recovery in {args.get('recovery_hours', 24)}h)"

    elif name == "mm_quota":
        rows = bq_client.quota(provider, model_key)
        if not rows:
            return "No quota data."
        lines = [f"{'Provider':<14} {'Model':<22} {'Remaining':<10} {'Tokens Rem':<12} {'Checked'}"]
        lines.append("-" * 70)
        for r in rows:
            rem = f"{float(r['remaining']):.0f}%" if r.get("remaining") is not None else "-"
            lines.append(
                f"{r['provider']:<14} {r['model_key']:<22} {rem:<10} "
                f"{r.get('tokens_remaining', '-'):<12} {str(r.get('checked_at', ''))[:16]}"
            )
        return "\n".join(lines)

    elif name == "mm_quota_set":
        bq_client.update_quota(provider, model_key, float(args.get("remaining_pct", 0)))
        return f"✓ quota set: {provider}/{model_key} = {args.get('remaining_pct', 0):.0f}%"

    elif name == "mm_balance":
        rows = bq_client.balance(provider)
        if not rows:
            return "No balance data. Run `hamachi balance` locally to push snapshots to BQ."
        lines = [f"{'Provider':<14} {'Balance':<10} {'Currency':<10} {'Available':<10} {'Updated'}"]
        lines.append("-" * 60)
        for r in rows:
            lines.append(
                f"{r['provider']:<14} ${float(r['total_balance']):<7.2f} "
                f"{r.get('currency', 'USD'):<10} "
                f"{'✓' if r.get('is_available') else '✗':<10} "
                f"{str(r.get('timestamp', ''))[:16]}"
            )
        return "\n".join(lines)

    elif name == "mm_recovery":
        rows = bq_client.recovery_estimates()
        if not rows:
            return "No pending recovery estimates."
        lines = []
        for r in rows:
            lines.append(
                f"{r['model_key']:<22} -> {str(r['estimated_at'])[:16]}  "
                f"(confidence: {r.get('confidence', 0):.0%}, based: {r.get('based_on', '')})"
            )
        return "\n".join(lines)

    elif name == "mm_health":
        return json.dumps(bq_client.check_health(), ensure_ascii=False)

    return f"Unknown tool: {name}"


# ── SSE Endpoint ──────────────────────────────────────────────────────────────

@app.get("/sse")
async def sse_endpoint(request: Request):
    session_id = str(uuid.uuid4())
    queue: asyncio.Queue = asyncio.Queue(maxsize=128)
    _sessions[session_id] = queue

    async def event_generator():
        try:
            yield {"event": "endpoint", "data": f"/message?session_id={session_id}"}
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=30)
                    yield {"data": json.dumps(data, ensure_ascii=False)}
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": ""}
        finally:
            _sessions.pop(session_id, None)

    return EventSourceResponse(event_generator())


# ── Message Handler ───────────────────────────────────────────────────────────

@app.post("/message")
async def handle_message(request: Request, session_id: str = ""):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}},
            status_code=400,
        )

    method = body.get("method", "")
    msg_id = body.get("id")

    # Build response based on method
    if method == "initialize":
        resp = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "shizuka-mcp", "version": "1.0.0"},
                "capabilities": {"tools": {}},
            },
        }
    elif method == "notifications/initialized":
        resp = {"jsonrpc": "2.0", "id": msg_id}
    elif method == "tools/list":
        resp = {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": MCP_TOOLS}}
    elif method == "tools/call":
        params = body.get("params", {})
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        try:
            result_text = await asyncio.wait_for(
                asyncio.to_thread(_call_tool, tool_name, arguments),
                timeout=_TOOL_TIMEOUT,
            )
            resp = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"content": [{"type": "text", "text": result_text}]},
            }
        except asyncio.TimeoutError:
            resp = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32000, "message": f"Tool {tool_name} timed out after {_TOOL_TIMEOUT}s"},
            }
        except Exception as e:
            resp = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"content": [{"type": "text", "text": f"ERROR: {e}"}], "isError": True},
            }
    else:
        resp = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }

    # Send response via SSE
    queue = _sessions.get(session_id)
    if queue:
        await queue.put(resp)

    return JSONResponse({"ok": True})


# ── Health Check ──────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    h = bq_client.check_health()
    status = 200 if h["status"] == "ok" else 503
    return JSONResponse(h, status_code=status)


@app.get("/")
async def root():
    return PlainTextResponse("shizuka-mcp: always-on model selection engine\n")
