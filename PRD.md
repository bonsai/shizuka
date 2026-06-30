# PRD: shizuka — Model Manager

## 三層アーキテクチャ
```
soubi (網羅)  →  hamachi (状態)  →  shizuka (判断)
```

| 層 | リポジトリ | 責務 | DB |
|---|-----------|------|-----|
| 網羅 | `bonsai/soubi` | 全プロバイダー・モデル・CLIのカタログ | `inventory.sqlite` |
| 状態 | `bonsai/hamachi` | 残高・使用量・稼働監視 | `data.db` |
| **判断** | **`bonsai/shizuka`** | **モデル選択・自動切替・レコメンド** | **`models.db` (統合)** |

## バージョン
- **V2.0 (current, 2026-06-30):** Python移植・DB統合完了
- **V1.x (retired):** Go実装 (`shizuka.exe`)

## 課題
- 10種CLIが個別の設定ファイル・プロバイダーを持つ → 把握・切替が煩雑
- 無料枠枯渇時に手動切り替えが必要
- プロバイダー分散により全体把握が困難

## 機能要件

| ID | 機能 | 状態 | 実装 |
|----|------|------|------|
| F-01 | 全CLI現状一覧 (`list`) | ✅ | `main.py list` / `mm_list` |
| F-02 | プロバイダー優先順位テーブル | ✅ | `models.json` + seed |
| F-03 | 優先順位切替 (`next` / `pN`) | ✅ | `mm_next` / `mm_priority` |
| F-04 | Qwen Dashscopeローテーション | ✅ | `mm_rotate` |
| F-05 | 枯渇マーク＋自動ローテーション | ✅ | `mm_exhausted` |
| F-06 | MCPサーバーモード | ✅ | `mcp_server.py` (stdio) |
| F-07 | CSV出力 | ✅ | `mm_csv` |
| F-08 | レコメンド機能 | ✅ | `mm_recommend` |
| F-09 | 使用量ログ | ✅ | `mm_log_usage` / `mm_usage` |
| F-10 | クォータチェック (Anthropic) | ✅ | `mm_quota` |
| F-11 | DB統合 (models.db一本化) | ✅ | `migration.py` / `seed_db.py` |
| F-12 | BQ定期同期 | 🔄 | `bq_sync.py` (作成中) |
| F-13 | BQML復帰予測 | 🔄 | `bq_sync.py` 完了後 |
| F-14 | 自動フォールバック | 📋 | 設計済み、未実装 |

## 非機能要件
| 項目 | 要件 |
|------|------|
| DB | unified models.db (Roaming\model-manager) |
| MCP | protocol 2024-11-05, stdio |
| 言語 | Python 3.10+ (Goから移行済み) |
| 設定破壊 | 既存設定ファイルのフォーマットを保持 |
| Windows対応 | Windows 11 プライマリ |

## レコメンドロジック
```
score = quality_score(task) - cost_score - quota_penalty
```
- `quality_score`: タスク難易度とモデル能力のマッチ度
- `cost_score`: 入力1Mトークン単価を0-10に正規化
- `quota_penalty`: 残20%以下で+ペナルティ、0%で使用不可
