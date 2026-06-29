"""Read models.json and sync to BigQuery providers/cli_priority tables.
Usage: python scripts/sync_models_to_bq.py | cmd.exe /c "bq query --nouse_legacy_sql --project_id=yok-ai-2026"
"""
import json, sys
from pathlib import Path

repo = Path(__file__).resolve().parent.parent
models = json.loads((repo / "models.json").read_text())

providers: dict[str, dict] = {}
priority_entries: list[dict] = []

# Collect all unique provider/model combos from priorities
for cli_entry in models.get("priorities", []):
    cli = cli_entry["cli"]
    for i, ent in enumerate(cli_entry.get("entries", [])):
        mk = ent.get("model", ent.get("provider", ""))
        pk = f"{ent['provider']}/{mk}" if mk else ent["provider"]
        if pk not in providers:
            providers[pk] = {
                "model_key": mk or ent["provider"],
                "provider": ent["provider"],
                "model_id": mk if mk else "",
                "tier": "free",
                "cost_in": 0.0,
                "quality_low": 5, "quality_mid": 5, "quality_high": 5, "quality_code": 5,
                "priority": i + 1,
                "note": ent.get("note", ""),
            }
        priority_entries.append({
            "cli": cli,
            "priority": i + 1,
            "model_key": mk or ent["provider"],
        })

# Overlay cost/quality from modelSpecs
for spec in models.get("modelSpecs", []):
    pk = f"{spec['provider']}/{spec['model']}" if spec['model'] else spec['provider']
    # Also try matching by model_key
    mk = spec["model"]
    if pk in providers:
        p = providers[pk]
        p["cost_in"] = spec.get("costIn", 0)
        p["tier"] = "paid" if spec.get("costIn", 0) > 0 else "free"
        q = spec.get("quality", {})
        p["quality_low"] = q.get("low", 5)
        p["quality_mid"] = q.get("mid", 5)
        p["quality_high"] = q.get("high", 5)
        p["quality_code"] = q.get("code", 5)

# Mark DeepSeek as paid
for p in providers.values():
    if p["provider"] in ("deepseek", "openai") and "deepseek" in p["model_key"]:
        p["tier"] = "paid"

# Also mark aihubmix paid models
for p in providers.values():
    if p["provider"] == "aihubmix" and p["cost_in"] > 0:
        p["tier"] = "paid"

# Generate REPLACE statement for providers
now = __import__("datetime").datetime.now().isoformat()
prov_rows = []
for pk, p in sorted(providers.items(), key=lambda x: x[1]["priority"]):
    mk = p["model_key"].replace("'", "\\'")
    prov = p["provider"].replace("'", "\\'")
    mid = p["model_id"].replace("'", "\\'")
    note = p["note"].replace("'", "\\'")
    prov_rows.append(
        f"('{mk}', '{prov}', '{mid}', '{p['tier']}', "
        f"{p['cost_in']}, {p['quality_low']}, {p['quality_mid']}, "
        f"{p['quality_high']}, {p['quality_code']}, "
        f"{p['priority']}, '{note}', TIMESTAMP('{now}'))"
    )

print("CREATE OR REPLACE TABLE model_status.providers")
print("(model_key STRING, provider STRING, model_id STRING, tier STRING,")
print(" cost_in FLOAT64, quality_low INT64, quality_mid INT64,")
print(" quality_high INT64, quality_code INT64,")
print(" priority INT64, note STRING, updated_at TIMESTAMP)")
print("CLUSTER BY priority, provider AS (")
print("  SELECT * FROM UNNEST([")
print(",\n".join(prov_rows))
print("  ])")
print(");")

# Separate: DeepSeek paid models from openai config
print()
print("CREATE OR REPLACE TABLE model_status.cli_priority")
print("(cli STRING, priority INT64, model_key STRING)")
print("CLUSTER BY cli, priority AS (")
print("  SELECT * FROM UNNEST([")
cli_rows = []
for pe in priority_entries:
    mk = pe["model_key"].replace("'", "\\'")
    cli_rows.append(f"('{pe['cli']}', {pe['priority']}, '{mk}')")
print(",\n".join(cli_rows))
print("  ])")
print(");")

print(f"-- Synced {len(providers)} providers, {len(priority_entries)} CLI priorities", file=sys.stderr)
