# ADR-011: hamachi 統合 — 残高・使用量の一元管理

**ステータス:** Proposed  
**作成日:** 2026-06-29  
**置き換え:** hamachi 独自の metrics.db → shizuka DB に統合

## 背景

hamachi (「How Much」CLI) は DeepSeek API の残高確認・使用量ログ・予測・監視を行う。
shizuka (model-manager) は全CLIのモデル選択エンジン。

現在の問題:
- hamachi が独自の `~/.hamachi/metrics.db` (SQLite) を持っている
- shizuka も独自の `data.db` を持っている (ADR-010)
- DBが2つある = 状況把握が分裂、「dbは一つ」の原則に反する
- shizuka がモデル選択時に残高情報を参照できない (残高0なのに DeepSeek を選び続ける)

## 決定

### 1. データ統合 — hamachi のテーブルを shizuka の DB に移す

hamachi の metrics.db にあるテーブル:

| テーブル | 移行先 (shizuka data.db) |
|---------|------------------------|
| `usage_log` | 既存の `usage_log` テーブルに統合 (`cli='hamachi'` で識別) |
| `balance_snapshots` | 新規 `balance_snapshots` テーブル |
| `forecast_log` | 新規 `forecast_log` テーブル |

```sql
-- shizuka の data.db に追加
CREATE TABLE IF NOT EXISTS balance_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    provider        TEXT NOT NULL,           -- 'deepseek', 'openai', ...
    total_balance   REAL NOT NULL,
    topped_up       REAL DEFAULT 0,
    granted         REAL DEFAULT 0,
    currency        TEXT DEFAULT 'USD',
    is_available    INTEGER DEFAULT 1,
    timestamp       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS forecast_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    provider          TEXT NOT NULL,
    balance           REAL NOT NULL,
    daily_avg         REAL DEFAULT 0,
    optimistic_daily  REAL DEFAULT 0,
    pessimistic_daily REAL DEFAULT 0,
    days_remaining    REAL DEFAULT 0,
    timestamp         TEXT NOT NULL
);
```

### 2. hamachi は shizuka の DB を読む

hamachi の CLI は shizuka と同じ `data.db` を参照する。
DB パスは環境変数 `SHIZUKA_DB` で渡す:

```bash
export SHIZUKA_DB="$APPDATA/model-manager/data.db"
hamachi balance       # → shizukaのDBに balance_snapshots をINSERT
hamachi forecast      # → shizukaのDBから usage_log を読み forecast_log にINSERT
```

フォールバック: `SHIZUKA_DB` 未設定時は `~/.hamachi/metrics.db` を使う (後方互換)。

### 3. shizuka が hamachi を呼ぶ

shizuka の MCP ツールとして `mm_balance` を追加:

```
mm_balance provider=deepseek
→ {"provider":"deepseek","total_balance":3.06,"currency":"USD","is_available":true}
```

実装方法 (疎結合、2案):

**案A: CLI 呼び出し (推奨)**
```python
# shizuka/mcp.go に追加
func toolBalance(args) {
    out, _ := exec.Command("hamachi", "balance", "--json").Output()
    return json.Parse(out)
}
```

**案B: MCP 経由**
hamachi が MCP サーバーとして起動 → shizuka が MCP クライアントとして呼ぶ

### 4. 自動切替ロジック

shizuka のモデル選択時に残高を考慮:

```
1. DeepSeek が最適モデルとして選ばれた
2. mm_balance provider=deepseek で残高確認
3. 残高 < $0.50 → DeepSeek をスキップ、次のプロバイダーに fallback
4. 残高 < $1.00 → forecast を確認、残日数 < 7日 → warning を出す
```

### 5. Provider 拡張

balance API のインターフェース:

```python
class BalanceProvider(ABC):
    @abstractmethod
    def get_balance(self) -> dict:  # {total, topped_up, granted, currency, is_available}
        pass

class DeepSeekBalance(BalanceProvider):
    def get_balance(self):
        # GET /user/balance

class OpenAIBalance(BalanceProvider):
    def get_balance(self):
        # GET /dashboard/billing/credit_grants
```

## トレードオフ

| 観点 | 分離 (現状) | 統合 (本ADR) |
|------|------------|-------------|
| DB 数 | 2 (shizuka + hamachi) | 1 (shizuka) |
| 依存 | hamachi が独立して動く | hamachi が shizuka の DB を参照 |
| 疎結合 | ◎ (完全独立) | ◯ (DBパスのみ共有) |
| 状況把握 | ✗ (分裂) | ◎ (一元) |
| 自動切替 | ✗ (残高見えない) | ◎ (選択時に考慮) |
| 移行工数 | — | 小 (テーブル追加 + パス変更) |

## アクションアイテム

- [ ] shizuka: `balance_snapshots`, `forecast_log` テーブル追加 DDL
- [ ] shizuka: `mm_balance` MCP ツール追加 (CLI呼び出し or 直接API)
- [ ] shizuka: モデル選択時に残高を考慮するロジック追加
- [ ] hamachi: DB パスを `SHIZUKA_DB` 優先に変更
- [ ] hamachi: `provider` カラム対応 (DeepSeek固定→拡張可能に)
- [ ] hamachi: `~/.hamachi/metrics.db` → shizuka DB への移行スクリプト
- [ ] hamachi: 旧 metrics.db の後方互換維持 (SHIZUKA_DB 未設定時)
