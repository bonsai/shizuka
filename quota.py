"""quota.py — Anthropic rate-limit checker"""
import os
import time
from datetime import datetime, timezone
from typing import Optional

import httpx


class RateLimitInfo:
    def __init__(self):
        self.provider = "anthropic"
        self.requests_limit = 0
        self.requests_remaining = 0
        self.requests_reset: Optional[datetime] = None
        self.tokens_limit = 0
        self.tokens_remaining = 0
        self.tokens_reset: Optional[datetime] = None
        self.input_tokens_limit = 0
        self.input_tokens_remaining = 0
        self.input_tokens_reset: Optional[datetime] = None
        self.checked_at = datetime.now(timezone.utc)
        self.model = "claude-haiku-4-5-20251001"


def check_anthropic_quota() -> RateLimitInfo:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise ValueError(
            "ANTHROPIC_API_KEY not found. Set with: setx ANTHROPIC_API_KEY sk-ant-..."
        )

    model = "claude-haiku-4-5-20251001"
    body = {
        "model": model,
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "0"}],
    }

    with httpx.Client(timeout=15) as client:
        resp = client.post(
            "https://api.anthropic.com/v1/messages",
            json=body,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        )

    if resp.status_code == 401:
        raise ValueError(
            "401 Unauthorized\n"
            f"  Key: {api_key[:20]}...\n"
            "  The key may be expired or revoked.\n"
            "  Get a new key at https://console.anthropic.com/settings/keys"
        )
    if resp.status_code != 200:
        raise ValueError(f"API error {resp.status_code} {resp.reason_phrase}")

    info = RateLimitInfo()
    h = resp.headers
    info.requests_limit = _parse_int(h.get("anthropic-ratelimit-requests-limit", "0"))
    info.requests_remaining = _parse_int(h.get("anthropic-ratelimit-requests-remaining", "0"))
    info.tokens_limit = _parse_int(h.get("anthropic-ratelimit-tokens-limit", "0"))
    info.tokens_remaining = _parse_int(h.get("anthropic-ratelimit-tokens-remaining", "0"))
    info.input_tokens_limit = _parse_int(h.get("anthropic-ratelimit-input-tokens-limit", "0"))
    info.input_tokens_remaining = _parse_int(h.get("anthropic-ratelimit-input-tokens-remaining", "0"))
    info.requests_reset = _parse_rfc3339(h.get("anthropic-ratelimit-requests-reset", ""))
    info.tokens_reset = _parse_rfc3339(h.get("anthropic-ratelimit-tokens-reset", ""))
    info.input_tokens_reset = _parse_rfc3339(h.get("anthropic-ratelimit-input-tokens-reset", ""))

    # Save to DB
    try:
        from db import get_db, ensure_model_key, set_quota
        conn = get_db()
        mk = ensure_model_key(conn, "anthropic", model)
        pct = (info.tokens_remaining / info.tokens_limit * 100) if info.tokens_limit > 0 else 100
        set_quota(conn, "anthropic", mk, pct)
        conn.close()
    except Exception:
        pass

    return info


def format_quota(info: RateLimitInfo) -> str:
    now = info.checked_at
    lines = [f"Anthropic rate limits  (checked {now.strftime('%Y-%m-%d %H:%M:%S')})", ""]

    rows = [
        ("Requests ", info.requests_remaining, info.requests_limit, info.requests_reset),
        ("Tokens   ", info.tokens_remaining, info.tokens_limit, info.tokens_reset),
    ]
    if info.input_tokens_limit > 0:
        rows.append(("Input tok", info.input_tokens_remaining, info.input_tokens_limit, info.input_tokens_reset))

    for label, remaining, limit, reset in rows:
        if limit == 0:
            continue
        pct = remaining / limit * 100 if limit else 0
        bar = _quota_bar(pct, 28)
        reset_str = ""
        if reset:
            d = (reset - datetime.now(timezone.utc)).total_seconds()
            if d < 0:
                d = 0
            h, m = int(d // 3600), int((d % 3600) // 60)
            reset_str = f"  reset in {h}h{m:02d}m"
        lines.append(f"  {label}  {remaining:>8,} / {limit:<8,}  {bar}  {pct:5.1f}%{reset_str}")

    lines.append(f"\n  Model used for check: {info.model}")
    return "\n".join(lines)


def quota_history(days: int = 7) -> str:
    try:
        from db import get_db
        conn = get_db()
        rows = conn.execute(
            "SELECT provider, model_key, remaining, checked_at FROM quota ORDER BY checked_at DESC LIMIT 10"
        ).fetchall()
        conn.close()
    except Exception:
        return "DB unavailable\n"

    if not rows:
        return "  (no records yet — run: mm_quota)\n"

    lines = ["Cached quota (from DB):",
             f"{'Provider':<12}  {'Model':<22}  {'Remaining':>8}  {'Checked at':<20}",
             "─" * 70]
    for r in rows:
        lines.append(f"{r['provider']:<12}  {(r['model_key'] or '(all)'):<22}  {r['remaining']:>7.1f}%  {r['checked_at'][:19]:<20}")
    return "\n".join(lines)


def _parse_int(s: str) -> int:
    try:
        return int(s)
    except (ValueError, TypeError):
        return 0


def _parse_rfc3339(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


_QUOTA_CHARS = ["▓", "█", "░"]


def _quota_bar(pct: float, width: int = 28) -> str:
    if pct < 0:
        pct = 0
    if pct > 100:
        pct = 100
    filled = int(pct / 100 * width)
    color = "▓" if pct < 20 else "█"
    return "[" + color * filled + "░" * (width - filled) + "]"
