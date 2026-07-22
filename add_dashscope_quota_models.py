"""Add new DashScope free-quota models to rotation DB + settings.json
Usage: python shizuka\add_dashscope_quota_models.py
"""
import json, sqlite3, os
from datetime import datetime, timezone

DB_PATH = os.path.join(os.environ.get('APPDATA', ''), 'model-manager', 'models.db')
SETTINGS_PATH = os.path.join(os.environ['USERPROFILE'], '.qwen', 'settings.json')

# From DashScope dashboard (2026-06-30): all have 1,000,000 remaining
new_models = [
    ("qwen3.7-max-2026-05-17", "2026-08-24T00:00:00", 9, 9, 9, 9),
    ("qwen3.7-max-2026-06-08", "2026-09-08T00:00:00", 9, 9, 9, 9),
    ("glm-5.1",               "2026-08-26T00:00:00", 8, 8, 8, 8),
    ("qwen3.7-max-preview",   "2026-08-24T00:00:00", 9, 9, 9, 9),
    ("qwen3.7-plus",          "2026-09-01T00:00:00", 8, 8, 8, 8),
    ("glm-5.2",               "2026-09-24T00:00:00", 8, 8, 8, 8),
    ("kimi-k2.7-code",        "2026-09-24T00:00:00", 8, 8, 9, 9),
    ("qwen3.7-plus-2026-05-26","2026-09-01T00:00:00", 8, 8, 8, 8),
]

def add_to_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COALESCE(MAX(sort_order), -1) FROM rotation_state WHERE model_key LIKE 'dashscope-%'")
    max_order = c.fetchone()[0]
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    inserted = 0
    for model_id, expires, q_low, q_mid, q_high, q_code in new_models:
        model_key = f"dashscope-{model_id}"
        c.execute("SELECT id FROM providers WHERE model_key=? AND provider='dashscope'", (model_key,))
        if c.fetchone():
            print(f"  SKIP (exists): {model_key}")
            continue
        max_order += 1
        c.execute("""INSERT INTO providers (model_key, provider, model_id, tier, cost_in, quality_low, quality_mid, quality_high, quality_code, priority, note) VALUES (?, 'dashscope', ?, 'free', 0.0, ?, ?, ?, ?, 8, '')""", (model_key, model_id, q_low, q_mid, q_high, q_code))
        c.execute("""INSERT INTO rotation_state (model_key, sort_order, expired, expires_at, last_error, updated_at) VALUES (?, ?, 0, ?, '', ?)""", (model_key, max_order, expires, now))
        c.execute("""INSERT INTO quota (provider, model_key, remaining, tokens_remaining, tokens_limit, checked_at) VALUES ('dashscope', ?, 100.0, 1000000, 1000000, ?)""", (model_key, now))
        c.execute("""INSERT INTO cli_priority (cli, priority, model_key) VALUES ('qwen', ?, ?)""", (max_order + 10, model_key))
        inserted += 1
        print(f"  ADDED: {model_key}")
    conn.commit()
    conn.close()
    print(f"\nDB: {inserted} models added")
    return inserted

def update_settings_json():
    with open(SETTINGS_PATH, 'r', encoding='utf-8') as f:
        cfg = json.load(f)
    providers = cfg.setdefault('modelProviders', {}).setdefault('openai', [])
    existing_ids = {p.get('id') for p in providers if isinstance(p, dict)}
    added = 0
    for model_id, expires, _, _, _, _ in new_models:
        if model_id in existing_ids:
            continue
        providers.append({
            "id": model_id,
            "name": f"[Free Quota] {model_id}",
            "baseUrl": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            "envKey": "DASHSCOPE_API_KEY"
        })
        added += 1
    cfg['model'] = {'name': new_models[0][0]}
    with open(SETTINGS_PATH, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print(f"settings.json: active → {new_models[0][0]}, {added} models added")

if __name__ == '__main__':
    add_to_db()
    update_settings_json()
    print("\nDone. Run 'qwen' to test.")
