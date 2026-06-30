"""Run hamachi balance, parse output, push to BigQuery balance_snapshots."""
import json, subprocess, sys, os, tempfile
from datetime import datetime, timezone

BQ = r"C:\Program Files (x86)\Google\Cloud SDK\google-cloud-sdk\bin\bq.cmd"
PROJECT = "yok-ai-2026"
HAMACHI_DIR = r"C:\Users\dance\Documents\MEGA\hamachi\py"

def run_hamachi():
    result = subprocess.run(
        [sys.executable, "-m", "hamachi", "balance"],
        capture_output=True, text=True, timeout=30,
        cwd=HAMACHI_DIR,
    )
    return result.stdout

def parse_balance(text: str) -> dict:
    d = {}
    for line in text.strip().split("\n"):
        line = line.strip()
        if "通貨:" in line:
            d["currency"] = line.split(":")[-1].strip()
        elif "残高合計:" in line:
            d["total_balance"] = float(line.split(":")[-1].strip())
        elif "利用可能:" in line:
            d["is_available"] = "OK" in line
        elif "付与残高:" in line:
            d["granted"] = float(line.split(":")[-1].strip())
        elif "チャージ残高:" in line:
            d["topped_up"] = float(line.split(":")[-1].strip())
    return d

def push_to_bq(b: dict):
    now = datetime.now(timezone.utc).isoformat()
    sql = (
        f"INSERT INTO model_status.balance_snapshots "
        f"(provider, total_balance, currency, is_available, topped_up, granted, timestamp) "
        f"VALUES ('deepseek', {b.get('total_balance', 0)}, '{b.get('currency', 'USD')}', "
        f"{str(b.get('is_available', True)).upper()}, "
        f"{b.get('topped_up', 0)}, {b.get('granted', 0)}, "
        f"TIMESTAMP('{now}'))"
    )
    # Write SQL to temp file to avoid quoting issues
    tmp = os.path.join(tempfile.gettempdir(), "balance_insert.sql")
    with open(tmp, "w") as f:
        f.write(sql)
    subprocess.run(
        [BQ, "query", "--nouse_legacy_sql", f"--project_id={PROJECT}", f"--flagfile={tmp}"],
        check=False,
    )

if __name__ == "__main__":
    text = run_hamachi()
    print(f"hamachi output:\n{text}")
    b = parse_balance(text)
    print(f"parsed: {json.dumps(b, indent=2)}")
    push_to_bq(b)
    print("done")
