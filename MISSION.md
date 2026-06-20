# Model Manager — 使命

## ミッションステートメント

全プロバイダーの全モデルを一元管理し、**FREE優先・コスト最適・タスク適合** で最適なモデルを自動選択する。

---

## 原則

1. **FREE優先** — OpenRouter 無料枠 → Qwen DashScope ローテーション → 格安有料
2. **コスト最適化** — タスクに過剰なモデルを使わない (ミスマッチ防止)
3. **タスク分類** — すべてのタスクを `quick | code | long | mid` に分類し、最適モデルを割り当てる
4. **クロスプロバイダー** — 特定CLIの枠を超えて全プロバイダー・全モデルを横断管理
5. **客観評価** — LM Studio のモデルはユーザー試験評価 + ランキングで選定
6. **セキュリティ** — API鍵は Secret Manager に一元管理、生鍵をコードに書かない
7. **ML駆動** — 使用実績から BQML で復帰予測・モデル推薦を自動化

---

## アーキテクチャ決定 (ADR-005)

**I. BigQuery + Secret Manager (GCP 完結)**

```
selector.py ──ADC──→ BigQuery (model_status)
                          ├── providers
                          ├── usage_log
                          ├── quota
                          ├── rotation_state
                          └── BQML モデル (recovery / recommend)
Secret Manager ──→ API 鍵 (DEEPSEEK / OPENROUTER / SAKURA / DASHSCOPE)
Cloud Scheduler ──→ Cloud Functions ──→ selector.py select (3h 定期)
```

---

## プロジェクト憲章

### スコープ

| 領域 | 含む | 含まない |
|------|------|----------|
| モデル管理 | 全プロバイダー・全モデルの優先順位・状態・切替 | モデルそのものの開発 |
| コスト最適化 | タスク種別 × 品質 × コスト のマッチング | 予算管理そのもの |
| 自動選択 | タスク種別に応じた最適モデルの推薦・切替 | ユーザーの最終決定権の奪取 |
| 評価 | LM Studio モデルのユーザー試験・ランキング | ベンチマーク自動実行 |
| 鍵管理 | Secret Manager への登録・参照 | 鍵の生成・ローテーション自動化 |
| タスク管理 | 本プロジェクト内のタスク構造化・進捗 | 他プロジェクトのタスク |

### 成功基準

- モデル選択の完全自動化（ユーザーがモデルを意識しなくなる）
- 無料枠優先使用率 100%（有料APIは枯渇時のみ）
- タスクミスマッチ率 0%（quick に高性能モデルを割り当てない）
- 全API鍵の Secret Manager 移行完了
- BQML による復帰予測の実用精度達成
