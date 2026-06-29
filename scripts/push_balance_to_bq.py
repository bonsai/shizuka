"""Run hamachi balance, parse output, push to BigQuery balance_snapshots."""
import json, subprocess, sys
from datetime import datetime, timezone

def run_hamachi():
    result = subprocess.run(
        [sys.executable, "-m", "hamachi", "balance"],
        capture_output=True, text=True, timeout=30,
        cwd="/mnt/c/Users/dance/Documents/MEGA/hamachi/py",
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
            d["is_available"] = "✓" in line
        elif "付与残高:" in line:
            d["granted"] = float(line.split(":")[-1].strip())
        elif "チャージ残高:" in line:
            d["topped_up"] = float(line.split(":")[-1].strip())
    return d

def push_to_bq(b: dict):
    now = datetime.now(timezone.utc).isoformat()
    sql = (
        "INSERT INTO model_status.balance_snapshots "
        "(provider, total_balance, currency, is_available, topped_up, granted, timestamp) "
        f"VALUES ('deepseek', {b.get('total_balance', 0)}, '{b.get('currency', 'USD')}', "
        f"{str(b.get('is_available', True)).upper()}, "
        f"{b.get('topped_up', 0)}, {b.get('granted', 0)}, "
        f"TIMESTAMP('{now}'))"
    )
    # Write SQL to temp file for bq flagfile
    with open("/tmp/shizuka/schema/balance_insert.sql", "w") as f:
        f.write(sql)
    print(f"SQL: {sql}")
    # Run via cmd.exe
    subprocess.run(
        ["cmd.exe", "/c",
         "copy \\\\wsl.localhost\\Ubuntu-24.04\\tmp\\shizuka\\schema\\balance_insert.sql "
         "C:\\Users\\dance\\AppData\\Local\\Temp\\balance_insert.sql /Y >nul 2>&1 && "
         "bq query --nouse_legacy_sql --project_id=yok-ai-2026 "
         "--flagfile=C:\\Users\\dance\\AppData\\Local\\Temp\\balance_insert.sql"],
        check=False,
    )

if __name__ == "__main__":
    text = run_hamachi()
    print(f"hamachi output:\n{text}")
    b = parse_balance(text)
    print(f"parsed: {json.dumps(b, indent=2)}")
    push_to_bq(b)
    print("done")
