"""Read models.json and sync to BigQuery providers/cli_priority tables."""
import json, subprocess, sys, os, tempfile
from pathlib import Path

BQ = r"C:\Program Files (x86)\Google\Cloud SDK\google-cloud-sdk\bin\bq.cmd"
PROJECT = "yok-ai-2026"

repo = Path(__file__).resolve().parent.parent
models = json.loads((repo / "models.json").read_text(encoding="utf-8"))

providers: dict[str, dict] = {}
priority_entries: list[dict] = []

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

for spec in models.get("modelSpecs", []):
    pk = f"{spec['provider']}/{spec['model']}" if spec['model'] else spec['provider']
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

for p in providers.values():
    if p["provider"] in ("deepseek", "openai") and "deepseek" in p["model_key"]:
        p["tier"] = "paid"

for p in providers.values():
    if p["provider"] == "aihubmix" and p["cost_in"] > 0:
        p["tier"] = "paid"

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

cli_rows = []
for pe in priority_entries:
    mk = pe["model_key"].replace("'", "\\'")
    cli_rows.append(f"('{pe['cli']}', {pe['priority']}, '{mk}')")

parts = [
    f"CREATE OR REPLACE TABLE model_status.providers "
    f"(model_key STRING, provider STRING, model_id STRING, tier STRING, "
    f"cost_in FLOAT64, quality_low INT64, quality_mid INT64, "
    f"quality_high INT64, quality_code INT64, "
    f"priority INT64, note STRING, updated_at TIMESTAMP) "
    f"CLUSTER BY priority, provider AS ("
    f"  SELECT * FROM UNNEST([\n" + ",\n".join(prov_rows) + "\n  ])"
    f");",
    f"CREATE OR REPLACE TABLE model_status.cli_priority "
    f"(cli STRING, priority INT64, model_key STRING) "
    f"CLUSTER BY cli, priority AS ("
    f"  SELECT * FROM UNNEST([\n" + ",\n".join(cli_rows) + "\n  ])"
    f");",
]

if __name__ == "__main__":
    sql = "\n".join(parts)
    tmp = os.path.join(tempfile.gettempdir(), "sync_models.sql")
    with open(tmp, "w") as f:
        f.write(sql)
    subprocess.run(
        [BQ, "query", "--nouse_legacy_sql", f"--project_id={PROJECT}", f"--flagfile={tmp}"],
        check=False,
    )
    print(f"Synced {len(providers)} providers, {len(priority_entries)} CLI priorities", file=sys.stderr)
    print("done")
