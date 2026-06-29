# PRD: shizuka — Decision Layer (Model Manager)

## 0. 三層アーキテクチャ

```
soubi (網羅)  →  hamachi (状態)  →  shizuka (判断)
 資産台帳        残高・使用量・稼働     最適選択・自動切替
```

| 層 | リポジトリ | 責務 | DB |
|---|-----------|------|-----|
| 網羅 | `bonsai/soubi` | 全プロバイダー・モデル・CLIのカタログ | `inventory.sqlite` |
| 状態 | `bonsai/hamachi` | 残高・使用量・稼働監視 | `data.db` (balance_snapshots) |
| **判断** | **`bonsai/shizuka`** | **モデル選択・自動切替・レコメンド** | **`data.db` (providers/usage_log/quota)** |

shizuka は **soubi (カタログ)** と **hamachi (状態)** を入力とし、
コスト・品質・可用性を総合判断して最適モデルを選択・自動切替する。

---

**バージョン:** 1.0 (current) / 2.0 (planned)  
**作成日:** 2026-05-24  
**オーナー:** bonsai

---

## 1. 背景・課題

AIコーディングCLIが乱立し（qwen / crush / opencode / cline / codex / gemini / kilo / kiro / claude）、それぞれが独自の設定ファイル・プロバイダー切り替え方法を持つ。

| 課題 | 詳細 |
|------|------|
| 無料枠の枯渇 | DashScope・Sakura・OpenRouterの無料モデルは月次でクォータ切れ → 手動で設定ファイルを探して書き換えていた |
| プロバイダー分散 | 10種CLIが4〜5種プロバイダーに分散。どのCLIが何を使っているか把握できない |
| 切り替えコスト | 設定ファイルのパス・JSON構造がCLIごとに異なり、切り替えに毎回調査が必要 |
| コスト最適化の困難 | モデルごとのトークン単価・残クォータ・タスク難易度を照合する手段がない |

---

## 2. ゴール

- **単一コマンド**で全CLIのモデルを把握・切り替えできる
- **TUI** でキーボードだけで操作できる
- **MCP サーバー** として他のCLIから呼び出せる（クロスツール）
- **レコメンド機能**（V2）でタスク難易度・残クォータ・コストから最適モデルを提案

---

## 3. ユーザー

| ユーザー | 用途 |
|---------|------|
| 開発者本人 (bonsai) | TUI / CLIで日常的にモデル切り替え |
| 他のAI CLI | MCP経由で model-manager を呼び出してモデル情報取得・変更 |

---

## 4. 機能要件

### V1（リリース済み）

| ID | 機能 | 状態 |
|----|------|------|
| F-01 | 全CLI現状一覧表示 (`list`) | ✅ |
| F-02 | TUI（矢印キー操作・3ペイン：CLI一覧/優先順位/コマンドリファレンス） | ✅ |
| F-03 | プロバイダー優先順位テーブル（priority.go） | ✅ |
| F-04 | 優先順位切替 (`next` / `pN`) | ✅ |
| F-05 | qwen Dashscopeローテーション統合 | ✅ |
| F-06 | 枯渇マーク＋自動ローテーション (`exhausted`) | ✅ |
| F-07 | MCP サーバーモード (`--mcp`) | ✅ |
| F-08 | CSV出力・保存 (`csv`) | ✅ |
| F-09 | Claude Code / Gemini CLI へのMCP登録 | ✅ |
| F-10 | aihubmix プロバイダー対応 | ✅ |

### V2（計画）

| ID | 機能 | 優先度 | 状態 |
|----|------|--------|------|
| F-11 | **レコメンド機能** — タスク難易度・単価からBestモデル提案 | 高 | ✅ 基本実装済み |
| F-12 | 残クォータ自動取得（DashScope API / OpenRouter API） | 高 | 未着手 |
| F-13 | トークン単価テーブル（ModelSpec — `recommend.go`） | 高 | ✅ 実装済み |
| F-14 | タスク難易度入力 (`low` / `mid` / `high` / `code`) | 高 | ✅ 実装済み |
| F-15 | 使用量ログ（セッションごとのトークン消費記録） | 中 |
| F-16 | クォータアラート（残量X%以下で警告） | 中 |
| F-17 | **自動フォールバック** — hamachi の残高情報を基に枯渇プロバイダーを自動スキップ | 高 |
| F-18 | **hamachi連携** — `hm_balance` MCPツール呼び出しで選択時に残高考慮 | 高 |
| F-19 | Cline / OpenCode へのMCP登録 | 低 |
| F-20 | Webダッシュボード（使用状況可視化） | 低 |

### 自動フォールバックロジック (F-17)

```
1. shizuka が最適モデルを選択 (F-11)
2. hamachi balance --json で残高確認 (F-18)
3. 残高 < $0.50 → そのプロバイダーをスキップ、次優先順位へ
4. 全プロバイダー枯渇 → alert 発報
```

---

## 5. 非機能要件

| 項目 | 要件 |
|------|------|
| 起動速度 | TUI起動 < 200ms |
| 依存関係 | バイナリ単体で動作（Go製、ランタイム不要） |
| 設定ファイル破壊 | 既存設定ファイルのフォーマット・コメント・フィールドを保持 |
| Windows対応 | Windows 11 (amd64) をプライマリターゲット |
| MCP互換 | MCP protocol 2024-11-05 準拠 |

---

## 6. V2 レコメンドロジック（設計案）

### 入力

```
model-manager recommend --task <low|medium|high|code>
```

または MCP ツール `mm_recommend` で `{"task": "code"}` を渡す。

### スコアリング

各モデルを以下の軸でスコアリングし、総合スコア最大のモデルを推薦：

```
score = quality_score(task) - cost_score - quota_penalty
```

| 軸 | 説明 |
|----|------|
| `quality_score` | タスク難易度とモデル能力のマッチ度（テーブル定義） |
| `cost_score` | 入力1Mトークン単価を0-10に正規化 |
| `quota_penalty` | 残クォータが20%以下で+ペナルティ、0%で使用不可 |

### モデル能力テーブル（`priority.go` に追加）

```go
type ModelSpec struct {
    Provider   string
    ModelID    string
    CostIn     float64 // USD per 1M tokens
    CostOut    float64
    QualityLow int     // 0-10: 適性スコア for low tasks
    QualityMid int
    QualityHigh int
    QualityCode int
    FreeQuota  bool    // 無料枠あり
}
```

### 残クォータ取得

| プロバイダー | API |
|------------|-----|
| DashScope | `GET https://dashscope-intl.aliyuncs.com/api/v1/quotas` |
| OpenRouter | `GET https://openrouter.ai/api/v1/auth/key` |
| Sakura | 未対応（手動管理） |
| Anthropic | `GET https://api.anthropic.com/v1/usage` |

---

## 7. 成功指標

| 指標 | 目標 |
|------|------|
| モデル切り替え時間 | 5秒以内（TUI操作含む） |
| 無料枠の有効活用率 | 月間クォータ消費率 > 80% |
| 不要な有料API呼び出し削減 | 無料枠使用中は有料API呼び出し = 0 |

---

## 8. スコープ外（V1）

- モデルの性能ベンチマーク自動実行
- チーム共有・マルチユーザー対応
- GUI（デスクトップアプリ）
