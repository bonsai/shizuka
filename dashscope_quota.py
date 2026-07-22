"""DashScope 無料枠 残高確認
Usage:
  python shizuka\dashscope_quota.py          # 一覧表示
  python shizuka\dashscope_quota.py --watch   # 5分おき監視
"""
import sqlite3, os, sys, json
from datetime import datetime
from urllib.request import Request, urlopen

DB = os.path.join(os.environ.get('APPDATA', ''), 'model-manager', 'models.db')

def show_db():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("""SELECT q.model_key, q.remaining, q.tokens_remaining, q.tokens_limit, q.checked_at,
                        r.expired, r.expires_at
                 FROM quota q
                 LEFT JOIN rotation_state r ON q.model_key = r.model_key
                 WHERE q.provider = 'dashscope'
                 ORDER BY q.model_key""")
    rows = c.fetchall()
    conn.close()
    if not rows:
        print("No dashscope quota records found.")
        return
    print(f"{'Model':<35} {'Remain%':<8} {'Tokens':<14} {'Expired':<8} {'Expires'}")
    print("-"*80)
    for r in rows:
        remain = f"{r[1]:.0f}%" if r[1] is not None else "-"
        tokens = f"{r[2]}/{r[3]}" if r[3] else "-"
        expired = "YES" if r[4] else "no"
        expires = str(r[5])[:10] if r[5] else "-"
        model = r[0].replace("dashscope-","")[:34]
        print(f"{model:<35} {remain:<8} {tokens:<14} {expired:<8} {expires}")

def try_api_check():
    """Quick DashScope API quota check (requires API key)"""
    key = os.environ.get('DASHSCOPE_API_KEY') or "sk-7593d84f2ab94e268784819f609c8e07"
    model = "qwen3.7-max-2026-05-17"
    # This is a simple ping to check if quota remains
    req = Request(
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions",
        data=json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1
        }).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}"
        }
    )
    try:
        resp = urlopen(req, timeout=10)
        data = json.loads(resp.read())
        if "error" in data:
            print(f"API error: {data['error']}")
            return None
        return "OK (quota available)" if data.get("choices") else "unexpected response"
    except Exception as e:
        return f"Failed: {e}"

if __name__ == '__main__':
    show_db()
    if "--api" in sys.argv:
        print(f"\nAPI ping ({new_models[0][0]}): {try_api_check()}")
    if "--watch" in sys.argv:
        import time
        try:
            while True:
                os.system('cls' if os.name == 'nt' else 'clear')
                print(f"DashScope Quota Monitor ({datetime.now().strftime('%H:%M:%S')})")
                show_db()
                time.sleep(300)
        except KeyboardInterrupt:
            print("\nStopped.")
