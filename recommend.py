"""推荐エンジン (recommend)"""
from db import get_db, get_cli_priorities, list_providers


def spec_quality(spec: dict, task: str) -> int:
    return spec.get(f"quality_{task}", spec.get("quality_mid", 5))


def recommend(task: str = "mid", limit: int = 5) -> list[dict]:
    task_map = {"medium": "mid", "l": "low", "h": "high", "c": "code", "": "mid"}
    task = task_map.get(task.lower(), task.lower()) if task else "mid"

    conn = get_db()
    provs = {r["model_key"]: r for r in list_providers(conn)}
    conn.close()

    all_clis = ["opencode", "qwen", "cline", "codex", "gemini", "kilo", "kiro",
                 "claude", "qwencode", "goose", "crush-large", "crush-small"]

    conn = get_db()
    results = []
    seen = set()
    for cli in all_clis:
        entries = get_cli_priorities(conn, cli)
        for e in entries:
            mk = e["model_key"]
            key = f"{cli}|{e['provider']}|{e['model_id']}"
            if key in seen:
                continue
            seen.add(key)
            quality = spec_quality(dict(e), task)
            cost = e["cost_in"] or 0
            cost_penalty = cost / 1.5
            if cost_penalty > 10:
                cost_penalty = 10
            score = quality - cost_penalty
            expired = e["expired"]
            remaining = e["remaining"]
            available = not expired and (remaining is None or remaining > 0)
            results.append({
                "cli": cli,
                "provider": e["provider"],
                "model": e["model_id"],
                "score": round(score, 1),
                "quality": quality,
                "cost": cost,
                "available": available,
                "reason": f"quality={quality}/10  cost={'free' if cost == 0 else f'${cost:.1f}/1M'}",
            })
    conn.close()

    results.sort(key=lambda r: (-r["available"], -r["score"]))
    return results[:limit]
