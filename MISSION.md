# shizuka — Model Manager 使命

## ミッション
全プロバイダー・全モデルを一元管理し、**無料優先・コスト最適・タスク適合**で最適モデルを自動選択する。

## アーキテクチャ（2026-06-30 改定）

```
models.json ──→ unified models.db (ローカル正)
                       ├─ providers (39)
                       ├─ cli_priority (70)
                       ├─ usage_log
                       ├─ quota / recovery_estimate
                       └─ rotation_state (10)

Python MCP stdio server ──→ opencode / 他CLI
Python CLI (main.py)    ──→ status / list / recommend / usage

BQ (model_status) ──→ 分析専用（定例同期）
```

**変更点:** BQ正→ローカル正。BQはML分析と長期保存に限定。

## ルーティング規則
1. `opencode-zen / big-pickle`（無料stealth）
2. `openrouter / owl-alpha`（無料best）
3. `dashscope`（Qwen無料枠ローテーション）
4. `sakura / Qwen3-Coder-480B`（$0.2 コード特化）
5. `deepseek / deepseek-v4-flash`（$0.14 最終手段）

**無料枠がある限り上位を優先。全無料枠枯渇時のみ DeepSeek。**

## 成功基準
- `python main.py status` で全モデル状態を一覧表示
- MCP tools (`mm_*`) で全操作を完結（CLI依存ゼロ）
- BQML復帰予測・モデル推薦の実用精度達成
- usage_log の BQ 定期同期自動化
