# shizuka — Model Manager

**全CLI共通 モデル選択エンジン**  
FREE優先・コスト最適・タスク適合で最適モデルを自動選択する。

## V2.0 Dev Recap (2026-06-30)

| 変更 | 内容 |
|------|------|
| Go→Python | `shizuka.exe` 廃止、`main.py` + `mcp_server.py` に完全移植 |
| DB統一 | `data.db` + `model-status.db` → unified `models.db` |
| 39 providers | 全優先度チェーンをシード、70件のcli_priority |
| MCP stdio | Go MCP → Python MCP (protocol 2024-11-05) |
| 設定読取 | `state_reader.py` で全CLI (qwen/grok/opencode/cline/codex/gemini/kilo/kiro/claude/qwencode/goose/crush等) の現在設定をJSONから読取 |
| seeds | `models.json` → `models.db` へのseed完了、migration.py で旧DBからusage_log移行 |

## アーキテクチャ

```
models.json ──→ seed_db.py ──→ unified models.db (ローカル正)
                                     ├─ providers (39)
                                     ├─ cli_priority (70)
                                     ├─ usage_log (過去ログ保持)
                                     ├─ quota / recovery_estimate
                                     ├─ rotation_state (10)
                                     └─ ml_params (5)

main.py (CLI) ────→ status / list / recommend / usage / quota
mcp_server.py ────→ opencode MCP (mm_list / mm_set / mm_recommend / etc.)

BQ (model_status) ──→ 分析専用 (#24 bq_sync.py 作成予定)
```

## ファイル構成

| ファイル | 役割 |
|---------|------|
| `main.py` | CLIエントリポイント: status / list / recommend / usage / quota / set / priority / next / rotate / exhausted / csv |
| `mcp_server.py` | MCP stdio サーバ。全 mm_* ツール |Git
| `db.py` | models.db への全DB操作 (CRUD + クエリ) |
| `state_reader.py` | 各CLI設定ファイル読み取り (qwen / grok / opencode / cline / codex / gemini / kilo / kiro / claude / qwencode / goose / crush) |
| `state_writer.py` | 各CLI設定ファイル書き込み |
| `recommend.py` | スコアリングエンジン: quality × cost × quota |
| `usage.py` | 使用量グラフ生成 (token consumption over N days) |
| `quota.py` | Anthropic rate-limit チェック |
| `migration.py` | 旧DB (data.db / model-status.db) → unified models.db 移行 |
| `seed_db.py` | models.json → models.db シード |
| `models.json` | 優先順位定義 (全CLI共通のsource of truth) |
| `bq_backend.py` | BigQuery バックエンド (旧 selector.py 用。V2.1で刷新予定) |

## クイックスタート

```bash
# 状態確認
python main.py status          # 全モデル状態一覧 (39件)
python main.py list            # 全CLIの現在設定

# レコメンド
python main.py recommend       # タスクmidで最適モデル推薦
python main.py recommend --task code   # コードタスク用
python main.py recommend --task low    # 軽量タスク用

# 使用量グラフ
python main.py usage           # 直近14日
python main.py usage --days 30

# モデル切替
python main.py set --cli qwen --provider dashscope --model qwen3.5-flash-2026-02-23
python main.py priority --cli cline --n 2   # ClineをP2に
python main.py next --cli kilo              # kiloを次優先度に進める
python main.py rotate                       # Qwen DashScopeローテーション
python main.py exhausted --model-key owl-alpha  # 枯渇マーク

# クォータ
python main.py quota            # Anthropic rate-limit

# MCPサーバ (opencode用)
python mcp_server.py            # stdioモードで起動
```

## MCPツール一覧

| ツール | 引数 | 説明 |
|--------|------|------|
| `mm_list` | — | 全CLI状態一覧 |
| `mm_set` | cli, provider, model | CLIのモデル設定 |
| `mm_priority` | cli, n | 優先度設定 (1=最高) |
| `mm_next` | cli | 次優先度に進める |
| `mm_rotate` | — | Qwen DashScopeローテーション |
| `mm_exhausted` | model_key | 枯渇マーク + 自動ローテーション |
| `mm_recommend` | task (low/mid/high/code) | 最適モデル推薦 |
| `mm_quota` | — | Anthropic rate-limit |
| `mm_log_usage` | cli, tokens_in, tokens_out | 使用量記録 |
| `mm_usage` | days | 使用量グラフ |
| `mm_status` | — | 全モデル状態テーブル |
| `mm_csv` | — | CSV出力 |

## 依存

- Python 3.10+ (標準ライブラリのみ)
- `bq` CLI (BQ同期時。`gcloud components install bq`)
- 各CLIの設定ファイルが所定のパスにあること

## タスクボード

`KANBAN.md` が source of truth。  
GitHub Issues: https://github.com/bonsai/shizuka/issues

## 詳細

- `MISSION.md` — 使命・憲章
- `PRD.md` — 製品要件定義
- `ARCH.md` — 全体模式図 (mermaid)
- `ADR.md` — アーキテクチャ決定記録
- `ONTOLOGY.md` — 概念定義
- `cost_opt_report.md` — コスト最適化分析


（クラウドリンク）

| model-agent-skill | [https://github.com/bonsai/model-agent-skill](https://github.com/bonsai/model-agent-skill) | 関連 tool/skill repo |
| openrouter-models | [https://github.com/bonsai/openrouter-models](https://github.com/bonsai/openrouter-models) | 関連 tool/skill repo |

## 構成

```
AGENT.md              定義（role / mission / duties）
schema/agent.schema.json  定義スキーマ
tools/                （tool repo へのリンク＝上記 Spec 対象）
```

## 参照

- 実行基盤: [gh-aw](https://github.com/bonsai/gh-aw) — Archimedes の Task を GitHub 操作として実行
- 設計層: [archimedes](https://github.com/bonsai/archimedes) — SCAN→CLUSTER→ADVISE→TASK


（クラウドリンク）

| model-agent-skill | [https://github.com/bonsai/model-agent-skill](https://github.com/bonsai/model-agent-skill) | 関連 tool/skill repo |
| openrouter-models | [https://github.com/bonsai/openrouter-models](https://github.com/bonsai/openrouter-models) | 関連 tool/skill repo |

## 構成

```
AGENT.md              定義（role / mission / duties）
schema/agent.schema.json  定義スキーマ
tools/                （tool repo へのリンク＝上記 Spec 対象）
```

## 参照

- 実行基盤: [gh-aw](https://github.com/bonsai/gh-aw) — Archimedes の Task を GitHub 操作として実行
- 設計層: [archimedes](https://github.com/bonsai/archimedes) — SCAN→CLUSTER→ADVISE→TASK
