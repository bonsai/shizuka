# ADR-012: shizuka MCP の GCP 常駐化 + BigQuery バックエンド本番運用

**ステータス:** Implemented  
**作成日:** 2026-06-29  
**更新日:** 2026-06-29  
**置き換え:** ADR-003 (stdio MCP) / ADR-010 (SQLite 一次) の一部

---

## 背景

### 現状

- shizuka (model-manager) は Go バイナリとしてローカルで `stdio` MCP サーバーとして動作 (`mcp.go`)
- バックエンドは SQLite (`model-status.db`, `schema.sql`)
- BigQuery バックエンド (`bq_ddl.sql`, `bq_backend.py`) は実装済みだが、本番未運用
- モデル選択・クォータ管理はローカルマシンに閉じている

### 課題

| 課題 | 詳細 |
|------|------|
| **稼働時間の制約** | CLI使っている間しかshizukaが動かない。opencoe/clineが常時MCP接続しても情報が最新でない |
| **DB分裂** | SQLite と BQ の2重持ち。どちらが正か不明瞭 |
| **スケーラビリティ** | 使用量ログ・クォータ履歴が1台のSQLiteに閉じ、分析や可視化に BQ のクエリ能力を活かせない |
| **復旧耐性** | SQLite はマシン障害で消失リスク。BQ は managed で耐久性◎ |

### 前提: 既存アセット

BQ の DDL と Python バックエンドはすでに書かれている:

- `bq_ddl.sql` — `yok-ai-2026.model_status` データセット (providers / cli_priority / quota / rotation_state / usage_log / recovery_estimate / ml_params / v_available)
- `bq_backend.py` — `bq` CLI 経由で全 CRUD 操作を提供 (status / select / usage / log / quota / exhausted / recovery / sync)
- `selector.py` — YAML (models.agents.md) + DB からモデル選択エンジン

---

## 決定

### 1. shizuka MCP を GCP Cloud Run に常駐デプロイ

Python (FastAPI) + MCP over SSE (Server-Sent Events) で HTTP MCP サーバーを構築し、Cloud Run に常駐させる。

```
┌──────────────────────────────────────────────────┐
│  OpenCode / Cline / その他 CLI                    │
│  MCP client (SSE transport)                      │
└──────────┬───────────────────────────┬───────────┘
           │ GET /sse                 │ POST /message
           ▼                          ▼
┌──────────────────────────────────────────────────┐
│  Cloud Run: shizuka-mcp (Python FastAPI)         │
│  ┌──────────────────────────────────────────┐    │
│  │ SSE エンドポイント (/sse)               │    │
│  │ → endpoint: /message を通知             │    │
│  │ → keepalive 30秒                       │    │
│  └──────────────────────────────────────────┘    │
│  ┌──────────────────────────────────────────┐    │
│  │ JSON-RPC Dispatcher                      │    │
│  │ tools/list → bq_backend ツール一覧       │    │
│  │ tools/call → bq_backend 関数ディスパッチ  │    │
│  └──────────────────────────────────────────┘    │
└──────────────────────┬───────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────┐
│  BigQuery: yok-ai-2026.model_status               │
│  providers / usage_log / quota / rotation_state   │
│  recovery_estimate / ml_params                    │
└──────────────────────────────────────────────────┘
```

### 2. バックエンド: BigQuery をプライマリ、SQLite を開発用に

| 環境 | バックエンド | DB 場所 |
|------|------------|---------|
| 本番 (Cloud Run) | **BigQuery** | `yok-ai-2026.model_status` |
| ローカル開発 | SQLite | `%APPDATA%/model-manager/model-status.db` |
| 同期 | `selector.py sync --backend bq` | YAML → BQ へアップロード |

### 3. スコープ分割: リモート MCP vs ローカル CLI

| 機能 | デプロイ先 | API | 説明 |
|------|-----------|-----|------|
| `mm_recommend` | **Cloud Run** | BQ select | タスク難易度＋残量から最適モデル推薦 |
| `mm_status` | **Cloud Run** | BQ v_available | 全モデルの状態一覧 |
| `mm_balance` | **Cloud Run** | hamachi API or BQ balance_snapshots | プロバイダー残高 |
| `mm_usage` | **Cloud Run** | BQ usage_log | 使用量集計・グラフ |
| `mm_quota` | **Cloud Run** | BQ quota | クォータ残量確認 |
| `mm_log_usage` | **Cloud Run** | BQ INSERT usage_log | 使用量記録 |
| `mm_exhausted` | **Cloud Run** | BQ rotation_state | 枯渇マーク |
| `mm_set` | **ローカル** | Go + priority.go | CLI設定ファイル書き換え (要ローカルFS) |
| `mm_next` | **ローカル** | Go | 次優先順位へ |
| `mm_priority` | **ローカル** | Go | 優先順位指定 |
| `mm_rotate` | **ローカル** | Go | Qwen ローテーション |
| `mm_csv` | **ローカル** | Go | CSV 出力 |

opencode.jsonc に2つの MCP サーバーとして登録:

```jsonc
{
  "mcpServers": {
    "shizuka-mcp-local": {
      "command": "model-manager",
      "args": ["--mcp"]
    },
    "shizuka-mcp": {
      "url": "https://shizuka-mcp-xxxxx-uc.a.run.app/sse"
    }
  }
}
```

### 4. トランスポート: MCP over SSE (HTTP)

FastAPI + `sse-starlette` で実装:

| エンドポイント | メソッド | 説明 |
|-------------|---------|------|
| `GET /sse` | SSE | MCP 初期化。`endpoint` イベントで POST URL 通知 |
| `POST /message` | JSON-RPC | ツール呼び出しの受付・応答 |
| `GET /health` | HTTP | Cloud Run ヘルスチェック用 |
| `GET /` | HTTP | 管理画面 or 最低限のステータス表示 |

### 5. インフラ: Cloud Run Free Tier

| リソース | 設定 | 無料枠内の根拠 |
|---------|------|---------------|
| Cloud Run | 1 vCPU, 256MB, min=0, max=1, リクエストタイムアウト300s | 2M req/month 無料。個人利用で十分 |
| BigQuery | 10GB storage, 1TB query/month | DDL ~87行、日次クエリ数十 → 1TB に遠く及ばない |
| Artifact Registry | Docker image 保存 | 500MB 無料。image は ~200MB |
| Container build | Cloud Build 無料枠 or ローカルビルド→gcloud push | 月120分無料 |

**月間コスト予測: $0** (すべて無料枠内) — ただし Cloud Run の always-on は min=1 にすると課金。min=0 (コールドスタート許容) で無料維持。

### 6. 認証

個人利用のため、Cloud Run の**認証なし** + 推測困難な URL で運用。
将来的に必要な場合: Cloud Run IAP または API Key ヘッダー。

---

## 実装計画

### Phase 1: BQ MCP サーバー (Python + FastAPI)

- `server/mcp_sse.py` — FastAPI アプリ + SSE エンドポイント
- `server/handlers.py` — MCP ツールディスパッチャ (bq_backend.py をラップ)
- `Dockerfile` — python:3.12-slim + uvicorn
- `docker-compose.yml` — ローカル開発用

### Phase 2: Cloud Run デプロイ

- `gcloud run deploy shizuka-mcp` (region: asia-northeast1)
- 環境変数: `PROJECT=yok-ai-2026`, `DATASET=model_status`
- サービスアカウント: `shizuka-mcp@yok-ai-2026.iam.gserviceaccount.com`
  - 権限: `bigquery.datasets.get`, `bigquery.tables.get`, `bigquery.tables.updateData`, `bigquery.tables.list`

### Phase 3: BQ DDL 適用

```bash
bq mk --dataset yok-ai-2026:model_status
bq query --nouse_legacy_sql < bq_ddl.sql
```

### Phase 4: 初回同期

```bash
python selector.py sync --backend bq
# → models.agents.md YAML の全レコードを BQ providers テーブルに投入
```

### Phase 5: opencode 設定更新

opencode.jsonc に `url` ベースの MCP サーバー追加。

---

## トレードオフ

| 観点 | 現状 (ローカル stdio + SQLite) | 提案 (Cloud Run + BQ) |
|------|-------------------------------|----------------------|
| **可用性** | CLI起動中のみ | 24/365 常時稼働 |
| **データ永続性** | ローカルSQLite (消失リスク) | BQ managed (耐久性◎) |
| **分析クエリ** | SQLite の JOIN/集計 | BQ SQL (高速・大規模) |
| **レイテンシ** | ほぼ0 (ローカル) | 初回 2-5s (コールドスタート) |
| **ネットワーク依存** | なし | HTTPS 必須 |
| **認証** | なし (ローカルバイナリ) | 要考慮 (URL難読化 or IAP) |
| **コスト** | 無料 (既存リソース) | 無料 (free tier内) |
| **設定ファイル操作** | 可 (ローカルFS直アクセス) | 不可 → ローカルMCPと併用 |
| **依存** | Go のみ | Python + FastAPI + Cloud Run + BQ |

### リスクと緩和

| リスク | 確率 | 緩和策 |
|--------|------|--------|
| Cloud Run の無料枠超過 | 低 | min=0, 使わないときは課金なし |
| BQ クエリ料金超過 (1TB/月) | 極低 | 日次数十クエリ、数十KB/回 |
| コールドスタートのレイテンシ | 中 | min=1 にすると月 $5-10 程度。許容できれば min=0 で無料 |
| GCP クレデンシャル漏洩 | 低 | サービスアカウント鍵を使わず Cloud Run の workload identity |
| Python 依存の増加 | 中 | FastAPI は軽量、依存は最小限に |

---

## 代替案検討

### 案B: Go HTTP サーバー (棄却)
Go にも `net/http` で SSE は実装できるが、既存の `bq_backend.py` / `selector.py` が Python であるため、BQ 操作用に Go で再実装が必要。Go + `cloud.google.com/go/bigquery` で同等の機能を書く工数に対し、Python はほぼゼロ工数で流用できる。

### 案C: Cloud Run ジョブ (棄却)
常駐ではなく、MCP からのリクエストのたびに Cloud Run ジョブを起動する方式。レイテンシが大きく (起動〜完了に10秒+)、SSE の永続コネクションと相性が悪い。却下。

### 案D: GCE (棄却)
常時稼働 VM。無料枠 (e2-micro 1ヶ月) はあるが、管理コストが高い。Cloud Run で十分。

---

## アクションアイテム

- [ ] ADR-012 レビュー・承認
- [ ] BQ DDL 適用 (`bq_ddl.sql` → `yok-ai-2026.model_status`)
- [ ] `server/mcp_sse.py` — FastAPI + SSE 実装
- [ ] `server/handlers.py` — bq_backend.py をラップしたツールディスパッチャ
- [ ] `Dockerfile` 作成
- [ ] サービスアカウント作成 + BQ 権限付与
- [ ] Cloud Run デプロイ (`gcloud run deploy`)
- [ ] 初回 BQ 同期 (`selector.py sync --backend bq`)
- [ ] opencode.jsonc に `shizuka-mcp` (SSE) 追加
- [ ] opencode.jsonc に `shizuka-mcp-local` (stdio) 維持 (設定書き込み用)
- [ ] ドキュメント更新: README にリモートMCP接続手順
