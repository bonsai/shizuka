# Model Manager

**全CLI共通 モデル選択エンジン**

FREE優先・コスト最適・タスク適合で最適モデルを自動選択する。

## アーキテクチャ

```
selector.py ──ADC──→ BigQuery (model_status)
                          ├── providers (8 models)
                          ├── usage_log (PARTITION BY date)
                          ├── quota
                          ├── rotation_state
                          └── BQML (recovery_model / recommend_model)
Secret Manager ──→ API 鍵 (DEEPSEEK / OPENROUTER / SAKURA / DASHSCOPE)
Cloud Scheduler ──→ Cloud Functions ──→ selector.py select (3h 定期)
```

## 選択ポリシー

```
ZEN (OpenRouter 無料枠) → Qwen DashScope rotation → fallback
   ├─ short: DeepSeek 有料
   └─ long: Sakura (Kimi K2.6 / Qwen3-Coder-480B)
```

## クイックスタート

```bash
# ローカルSQLite backend
python selector.py sync
python selector.py status
python selector.py select --task quick

# BigQuery backend
python selector.py --backend bq sync
python selector.py --backend bq status
python selector.py --backend bq select --task long
python selector.py --backend bq log --cli kilo --model-key owl-alpha \
  --tokens-in 500 --tokens-out 1200
python selector.py --backend bq usage --days 7
```

## タスクボード

`KANBAN.md` が source of truth。全イシューを構造化管理。

## 詳細

- `MISSION.md` — 使命・憲章
- `ADR-005-db-location.md` — アーキテクチャ決定 (BigQuery + Secret Manager)
- `models.agents.md` — モデル定義 YAML (全CLI共通 source of truth)
- `bq_ddl.sql` — BigQuery DDL
- `bq_backend.py` — BigQuery バックエンド
- `.opencode/agents/model-agent.md` — Hermes モデル選択エージェント
