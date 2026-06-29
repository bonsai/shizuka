# shizuka 用語オントロジ (Term Ontology)

三層アーキテクチャ (soubi → hamachi → shizuka) 全体で使う用語の定義と関係。

---

## 1. 三層定義

| 層 | リポジトリ | 責務 | 格言 |
|---|-----------|------|------|
| **soubi** (網羅) | `bonsai/soubi` | 全デジタル資産のカタログ。静的インベントリ | "What do we have?" |
| **hamachi** (状態) | `bonsai/hamachi` | 残高・使用量・稼働のリアルタイム状態監視 | "How much left?" |
| **shizuka** (判断) | `bonsai/shizuka` | モデル選択・自動切替・レコメンドエンジン | "Which one now?" |

### データフロー

```
soubi.inventory DB  ──catalog──→  hamachi.metrics  ──status──→  shizuka.decision
     providers                           balance                 recommend
     models                              usage                   auto-switch
     CLIs                                forecast                fallback
     MCP servers                         alert
     skills/agents                       quota
```

---

## 2. コアエンティティ

### provider
AIモデル提供サービス。API エンドポイント + API キーを持つ。
- **例**: `deepseek`, `openai`, `openrouter`, `dashscope`, `sakura`, `anthropic`, `google-ai`
- **属性**: name, base_url, api_key_ref, tier (free/paid)
- **soubi 管理**: 全プロバイダーのカタログ
- **hamachi 管理**: 残高・使用量の監視
- **shizuka 管理**: 選択時の重み付け・スキップ判定

### model_key
shizuka 内部でモデルを一意に識別するキー。`models.agents.md` のキーと一致。
- **例**: `qwen-max`, `deepseek-chat`, `sakura-qwen3-coder`, `gpt-4o`
- **形式**: 小文字 + ハイフン、プロバイダー名を含まない短縮形
- **全層で共通**: soubi/hamachi/shizuka 間のジョインキー

### model_id
API 呼び出し時に実際に使うモデル識別子。プロバイダー固有の形式。
- **例**: `deepseek-chat`, `qwen-max-2026-01-25`, `gpt-4o-2026-05-08`
- model_key と 1:N の場合あり (e.g., `deepseek-chat` → `deepseek-chat`, `deepseek-v3-0324`)

### cli
AI コーディング CLI の名称。
- **例**: `qwen`, `opencode`, `cline`, `codex`, `gemini`, `kilo`, `kiro`, `claude`, `goose`
- **分類**: 無料枠のみ利用 (`qwen`/`cline`/`codex`/`kiro`/`vscode`) vs 有料併用 (`opencode`/`claude`)
- **soubi 管理**: CLI ごとの設定ファイルパス・形式・現在のプロバイダー

### tier
モデルの課金区分。
- **free**: 無料枠。クォータ制限あり。月次リセットされることが多い
- **paid**: 従量課金。残高から差し引かれる
- **free_with_auth**: 認証は必要だが無料 (例: Sakura)

### priority
CLI ごとのモデル優先順位 (P1 = 最優先, P2, ...)。
- P1-P3: 「禅モード」— 最適なモデルを自動選択
- P4+: 手動フォールバック用
- shizuka の `recommend.go` / `selector.py` で参照

### quota
無料モデルの月次使用上限に対する残量 (% or トークン数)。
- プロバイダーAPIで取得 (DashScope, OpenRouter 対応)
- hamachi が定期的にチェック → BQ quota テーブルへ書き込み
- shizuka が選択時に参照 (残量 < 20% でペナルティ)

### balance
有料プロバイダー (主に DeepSeek) のアカウント残高 (USD)。
- hamachi が API から取得 → BQ balance_snapshots テーブルへ書き込み
- shizuka の `mm_balance` で参照
- 残高 < $0.50 でアラート + 自動スキップ

### usage_log
API 呼び出しごとのトークン消費記録。
- 各 CLI が呼び出し後に `mm_log_usage` MCP ツールを叩く
- BQ `usage_log` テーブルに蓄積
- 使用量グラフ・コスト分析・復帰予測の元データ

### rotation_state
DashScope (Qwen) の無料モデルローテーション状態。
- モデルが枯渇すると expired=TRUE、次モデルへローテーション
- 復帰予定時刻 (expires_at) を設定可能
- BQ `rotation_state` テーブルで管理

### recovery_estimate
枯渇モデルの復帰予測。
- usage_log のパターンから ML 推定 (ARIMA or 単純平均)
- 手動設定も可能
- shizuka の `mm_recovery` で表示

---

## 3. 無料/有料マッピング (2026-06-29 現在)

| cli | デフォルトプロバイダー | モデル | 課金 | 備考 |
|-----|---------------------|--------|------|------|
| qwen | dashscope | qwen-max / qwen-plus | **無料** | 月次クォータ制, 自動ローテーション |
| opencode | openrouter / sakura | 各種 | **無料** | openrouter 無料モデル + sakura |
| cline | openrouter / sakura | 各種 | **無料** | 同上 |
| codex | openrouter | 各種 | **無料** | |
| kiro | openrouter | 各種 | **無料** | |
| vscode (copilot) | github / openrouter | 各種 | **無料** | |
| zen | openrouter | 各種 | **無料** | |
| claude | anthropic | claude-sonnet / claude-haiku | 無料+有料 | 無料枠あり + 従量 |
| gemini | google-ai | gemini-pro / gemini-flash | **無料** | |
| deepseek | deepseek | deepseek-chat / deepseek-coder | **有料** | 残高から差引、$0.50 未満でスキップ |
| openrouter | openrouter | 有料モデル | **有料** | 従量課金可能だが基本使わない |

**原則**: 無料枠が使える限り無料プロバイダーを優先。全無料枠枯渇時のみ DeepSeek (有料) にフォールバック。

---

## 4. DB スキーマ対応表

### BigQuery: yok-ai-2026.model_status (本番)

| テーブル | 責務 | 主キー | 更新者 |
|---------|------|--------|--------|
| `providers` | モデル定義カタログ | model_key | `selector.py sync` |
| `cli_priority` | CLIごとの優先順位 | (cli, priority) | `selector.py sync` |
| `quota` | 無料枠残量 (%) | (provider, model_key) | hamachi |
| `rotation_state` | DashScope ローテ状態 | model_key | shizuka local |
| `usage_log` | 使用履歴 | id (UNIX_MILLIS) | CLI → `mm_log_usage` |
| `recovery_estimate` | 復帰予測 | model_key | ML or 手動設定 |
| `balance_snapshots` | 残高スナップショット | (provider, timestamp) | hamachi |
| `ml_params` | ML 学習パラメータ | param_key | ML pipeline |

### SQLite: model-status.db (ローカル開発)

`schema.sql` に定義。上記 BQ テーブルを SQLite 用にミラー。

---

## 5. 命名規則

| 対象 | 規則 | 例 |
|------|------|-----|
| provider | 小文字スネークケース | `deepseek`, `dashscope`, `openrouter` |
| model_key | 小文字 + ハイフン | `qwen-max`, `deepseek-chat`, `gpt-4o` |
| model_id | プロバイダー指定の形式 | `qwen-max-2026-01-25` |
| cli | 小文字 | `opencode`, `cline`, `kiro` |
| tier | free / paid | `free`, `paid` |
| task_type | low / mid / high / code | `code`, `quick` |
| BQ dataset | スネークケース | `model_status` |
| BQ table | スネークケース | `usage_log`, `balance_snapshots` |
