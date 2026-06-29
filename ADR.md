# ADR — model-manager アーキテクチャ決定記録

**プロジェクト:** model-manager  
**作成日:** 2026-05-24

---

## ADR-001: 実装言語に Go を選択

**ステータス:** Accepted

### 背景
複数のCLIから呼び出せる共有ツールが必要。Node.js（既存環境）またはGo（単一バイナリ）の2択。

### 決定
Go を選択する。

### 理由
| 観点 | Go | Node.js |
|------|----|----|
| 配布 | 単一バイナリ、ランタイム不要 | node_modules必要 |
| 起動速度 | ~20ms | ~300ms+ |
| TUIライブラリ | bubbletea（成熟・実績あり） | blessed（メンテ懸念） |
| 型安全 | 静的型付け | TypeScript化が必要 |
| 既存環境 | Go 1.25.5 インストール済み | Node.js インストール済み |

### トレードオフ
- 優先順位・ローテーションリストを変更するたびにリビルドが必要
- Node.jsはホットリロードが容易だがバイナリ配布が複雑

---

## ADR-002: 優先順位テーブルをコードに埋め込む

**ステータス:** Accepted

### 背景
各CLIのプロバイダー優先順位（P1→PN）を管理する方法として、外部JSONファイルとコードへの埋め込みの2択。

### 決定
`priority.go` にGo構造体としてハードコードする。

### 理由
- 型安全・補完が効く（文字列キーのJSONより安全）
- 設定ファイルの場所・形式をユーザーが覚える必要がない
- バイナリ単体で動作（外部ファイル依存なし）
- 変更頻度が低い（月1-2回程度）

### トレードオフ
- 変更のたびにリビルドが必要（手順: `go build -o model-manager.exe . && Copy-Item ...`）
- 複数マシンで使う場合、バイナリの同期が必要

### 将来の見直し条件
- 変更頻度が週1以上になった場合 → 外部YAMLに移行を検討

---

## ADR-003: MCP サーバーをライブラリなしで実装

**ステータス:** Accepted

### 背景
MCP（Model Context Protocol）サーバーを実装するにあたり、`mark3labs/mcp-go` 等のライブラリを使うか、JSON-RPC 2.0 を直接実装するかの選択。

### 決定
外部MCPライブラリを使わず、JSON-RPC 2.0 を直接実装する（`mcp.go`）。

### 理由
- MCP の stdio transport は「1行JSON入力 → 1行JSON出力」のシンプルな構造
- 必要なメソッドは `initialize` / `tools/list` / `tools/call` の3つのみ
- ライブラリ依存を減らすことでバイナリサイズ・ビルド時間を削減
- 実装コスト: ~150行で完結

### トレードオフ
- MCP プロトコルの仕様変更に追随する必要がある
- `notifications/initialized` 等の非同期メソッドは最小限の実装

---

## ADR-004: 設定ファイルを各CLI固有形式のまま読み書き

**ステータス:** Accepted

### 背景
各CLIの設定（qwen settings.json / crush crush.json / opencode.jsonc / sakura-ai.env）を中央管理DBに移行するか、それぞれ固有形式を維持するかの選択。

### 決定
各CLIの設定ファイルをそのまま読み書きする。中央DBは作らない。

### 理由
- 各CLIが自分の設定ファイルを読む → 中央DBへの同期が不要
- 既存CLIの設定UIとの共存が可能（crushのGUI等）
- 設定ファイルが「Single Source of Truth」のまま維持される
- ユーザーが直接設定ファイルを編集しても反映される

### トレードオフ
- 各CLIのJSON構造変更で読み取りコードの修正が必要
- opencode.jsonc の JSONC フォーマット（コメント付きJSON）は専用パース処理が必要

---

## ADR-005: TUI に bubbletea を使用

**ステータス:** Accepted

### 背景
TUIフレームワークの選択。bubbletea（charmbracelet）/ tcell / tview の3択。

### 決定
`github.com/charmbracelet/bubbletea` + `lipgloss` を使用する。

### 理由
- Elm Architecture（immutable model + Update関数）で状態管理が明確
- lipgloss によるスタイリングが宣言的でメンテしやすい
- Windows ターミナル（PowerShell / Windows Terminal）での動作実績あり
- bubbletea v0.27.0 で API が安定

### トレードオフ
- 低レベルのカーソル制御が必要な場合に制限がある
- tcell より学習コストが低いが、複雑なレイアウトは記述量が増える

---

## ADR-006: cline の設定変更を `cline auth` コマンド経由で行う

**ステータス:** Accepted

### 背景
Cline CLIの認証情報は `~/.cline/` 配下のSQLiteまたはバイナリDBに格納されており、直接ファイル編集が困難。

### 決定
`cline auth --provider <p> --modelid <m> --apikey <k>` を子プロセスとして実行する。

### 理由
- Cline 公式のAPIを使うため、DB破壊リスクがない
- cline のDB形式が変わっても追随不要（cline側で吸収される）

### トレードオフ
- `cline` バイナリがPATHにある必要がある
- 認証エラー時のエラーハンドリングがclineのexitコードに依存

---

## ADR-007: V2 レコメンド機能の設計方針

**ステータス:** Accepted（候補Aで実装済み、将来候補Cへ拡張予定）

### 背景
「残クォータ・トークン単価・タスク難易度からBestモデルを推薦する」機能（PRD F-11〜F-14）。

### 候補A: Go本体に組み込む
- `mm_recommend` MCPツール + `recommend` CLIコマンドを追加
- 各プロバイダーAPIに問い合わせてクォータ取得
- スコアリングロジックをGoで実装
- 変更時はリビルド必要

### 候補B: Claude スキルとして実装
- `/model-manager recommend` スキルを `mm_list` + `mm_csv` で情報取得
- Claude自身がスコアリングと推薦テキストを生成
- モデル能力の判断をLLMに委譲できる
- リビルド不要

### 候補C: ハイブリッド
- Go: クォータ取得・単価テーブル・スコア計算（機械的な部分）
- Claude skill: 結果を自然言語で説明・提案（解釈の部分）

### 決定
**候補A**（Go本体に `mm_recommend` + `recommend` コマンドを追加）で実装。

### 実装済み内容
- `recommend.go`: `ModelSpec` テーブル（単価・タスク別品質スコア）+ `Recommend(task)` 関数
- `main.go`: `model-manager recommend [low|mid|high|code]` コマンド
- `mcp.go`: `mm_recommend` MCPツール
- スコアリング: `score = quality(task) - costPenalty`（costPenalty = CostIn/1.5, max 10）

### 残タスク（候補Cへの拡張）
- F-12: DashScope / OpenRouter クォータAPIでリアルタイム残量取得 → quota_penalty に反映
- Claude skillから `mm_recommend` を呼ぶ構成

---

## ADR-008: ModelSpec（モデルコスト・品質テーブル）の保存先

**ステータス:** Superseded by ADR-010

### 背景
V2レコメンド機能に必要な「モデルごとの単価・品質スコア」テーブル（`ModelSpec`）をどこに保存するか。
候補は3つ: **SQLite** / **外部JSON** / **Goハードコード**。

### 比較

| 観点 | SQLite | 外部JSON | Goハードコード |
|------|--------|----------|--------------|
| 可搬性 | DBファイルが必要 | JSONファイルが必要 | バイナリ単体で完結 |
| 読み書き | CGo or 純Go driver | 標準library | 変更→リビルド |
| スキーマ変更 | ALTER TABLE | JSONキー追加 | 構造体変更+リビルド |
| クエリ柔軟性 | SQL（柔軟） | コードで走査（十分） | コードで走査（十分） |
| 変更頻度 | — | — | 月1回程度 |
| UIでの編集 | GUI不要→不要 | テキストエディタで可 | エディタ+リビルド |
| CGo依存 | あり（mattn/go-sqlite3） | なし | なし |

### 決定
**Goハードコード**（`recommend.go` の `ModelSpecs` スライス）を選択する。

### 理由
- **変更頻度が低い**: モデル単価の更新は月1〜2回程度。毎日変わるデータではない
- **バイナリ単体動作**: ADR-002と一貫。外部ファイルを持ち歩く必要がない
- **型安全**: `ModelSpec` 構造体はコンパイル時に検証される
- **CGo不要**: SQLite（mattn/go-sqlite3）はCGoが必要でクロスコンパイルが複雑になる
- **スケール不要**: 現状20モデル程度。SQLのクエリ最適化は過剰
- **外部JSONとの差分**: JSONにすると「モデル追加するたびにJSONファイルをどこかに置く手間」が発生し、ADR-002の方針と矛盾する

### トレードオフ
- 単価が変わったらリビルドが必要（手順: `go build && Copy-Item`）
- JSONなら `model-manager.exe` 配置後にファイル編集だけで済む
- SQLiteなら将来的に使用量ログ（F-15）と統合できる可能性がある

### 将来の見直し条件
- F-15（使用量ログ）を実装する場合 → セッションデータはGoに持てないのでSQLiteへの移行を検討
- モデル追加頻度が週2回以上になった場合 → 外部JSONへの移行を検討

---

## ADR-009: TUIレイアウトを3ペインに変更

**ステータス:** Accepted

### 背景
当初のTUIは2ペイン（左: CLI一覧 / 右: 優先順位リスト）。
CLIコマンドとMCPツールの一覧が画面上に表示されず、ユーザーがREADMEを参照する必要があった。

### 決定
下部に固定の**リファレンスペイン**を追加し、3ペイン構成にする。

### レイアウト
```
┌──────────────┐  ┌─────────────────────────────────────────┐
│ CLI 一覧     │  │ 優先順位リスト（選択中CLI）              │
│ qwen ▶      │  │ P1  sakura  Qwen3-Coder-480B...          │
│ crush-large  │  │ P2  openrouter  gemini...                │
└──────────────┘  └─────────────────────────────────────────┘
┌────────────────────────────────────────────────────────────┐
│ CLI commands                                               │
│   model-manager list | csv | priority | rotate             │
│   model-manager recommend [low|mid|high|code]              │
│   model-manager <cli> next | p<N> | <provider> [model]     │
│ MCP tools                                                  │
│   mm_list  mm_set  mm_priority  mm_next  mm_rotate         │
│   mm_exhausted  mm_csv  mm_recommend                       │
└────────────────────────────────────────────────────────────┘
```

### 理由
- コマンドリファレンスが常に見え、操作の発見性が上がる
- インタラクティブでない（フォーカス不要）ので実装コストが低い
- `q quit` ヘルプラインは最下部に残す

### トレードオフ
- 画面縦方向の使用量が増える（小さいターミナルでは見切れる可能性）
- ペインの内容は静的なのでリアルタイム更新は不要

---

---

## ADR-010: モデルデータ・メタデータ・クォータを SQLite に移行

**ステータス:** Accepted  
**置き換え:** ADR-002（優先順位テーブルのハードコード）、ADR-008（ModelSpecのハードコード）

### 背景

現状の問題:
- モデルを追加・変更するたびに `priority.go` / `recommend.go` を編集 → リビルド → デプロイが必要
- Sakura が新モデルを追加するたびに、ModelSpec テーブルも手動で更新してリビルド
- クォータ（F-12）や使用量ログ（F-15）は実行時データなので、バイナリに持てない
- kanban-viewer も KANBAN.md を直接パースしており、毎回ファイルを再読みしている

→ **データの変更頻度 >> ロジックの変更頻度** となった。データとバイナリの分離が必要。

### 比較（再評価）

| 観点 | SQLite | 外部JSON | Goハードコード |
|------|--------|----------|--------------|
| モデル追加 | `INSERT` 1行 | JSONファイル編集 | Go編集+リビルド |
| クォータ保存 | ✅ ランタイムに追記 | 書き込み可だが煩雑 | ❌ 不可 |
| 使用量ログ | ✅ APPEND簡単 | ❌ 並行書き込み危険 | ❌ 不可 |
| CGo問題 | `modernc.org/sqlite`（純Go）で解決 | — | — |
| スキーマ進化 | `ALTER TABLE` / マイグレーション | JSONキー追加 | 構造体変更 |
| 初回起動 | DBファイル自動作成＋seed | JSONファイル同梱 | 不要 |
| TUI編集 | ✅ `mm db add/remove` コマンド | エディタが必要 | リビルドが必要 |

**CGo問題は `modernc.org/sqlite`（純Go SQLite実装）で解消される。** これが前回SQLiteを却下した主な理由を排除する。

### 決定

`modernc.org/sqlite` を使い、**SQLiteをモデルデータ・メタデータの Single Source of Truth** とする。

### DBファイル場所

```
%APPDATA%\model-manager\data.db
（Windows: C:\Users\dance\AppData\Roaming\model-manager\data.db）
```

バイナリとは独立して存在 → リビルド後も設定が保持される。

### スキーマ

```sql
-- CLIごとの優先順位エントリ（旧 priority.go Priorities）
CREATE TABLE providers (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    cli          TEXT NOT NULL,
    provider     TEXT NOT NULL,
    model        TEXT NOT NULL DEFAULT '',
    priority     INTEGER NOT NULL,           -- 1-based P番号
    note         TEXT DEFAULT '',
    use_rotation INTEGER DEFAULT 0,          -- 1=qwen rotation
    cost_in      REAL DEFAULT 0,             -- USD/1M input tokens
    quality_low  INTEGER DEFAULT 5,
    quality_mid  INTEGER DEFAULT 5,
    quality_high INTEGER DEFAULT 5,
    quality_code INTEGER DEFAULT 5,
    tags         TEXT DEFAULT ''             -- 'vl,coding,cpu' etc.
);

-- Qwen ローテーションリスト（旧 QwenRotation）
CREATE TABLE qwen_rotation (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id  TEXT NOT NULL UNIQUE,
    expires   TEXT DEFAULT '',
    exhausted INTEGER DEFAULT 0,
    sort_order INTEGER DEFAULT 0
);

-- クォータ状態（F-12: 将来拡張）
CREATE TABLE quota (
    provider   TEXT NOT NULL,
    model_id   TEXT DEFAULT '',
    remaining  REAL DEFAULT 100.0,   -- %
    checked_at TEXT NOT NULL,
    PRIMARY KEY (provider, model_id)
);

-- 使用量ログ（F-15: 将来拡張）
CREATE TABLE usage_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    cli        TEXT NOT NULL,
    provider   TEXT NOT NULL,
    model      TEXT NOT NULL,
    task       TEXT DEFAULT '',
    tokens_in  INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0,
    logged_at  TEXT NOT NULL
);
```

### 移行方針

1. **`db.go`** を追加: DB初期化・CRUD関数
2. **初回起動時 seed**: 現在の `priority.go` / `recommend.go` の値をDBに投入（冪等）
3. **`priority.go`**: データスライスを削除し、DB読み出し関数に置き換え
4. **`recommend.go`**: `ModelSpecs` を削除し、DB読み出しに置き換え
5. **新コマンド**:
   - `model-manager db add <cli> <provider> <model> [--priority N] [--cost 0.20]`
   - `model-manager db remove <cli> <provider> <model>`
   - `model-manager db list [cli]`
   - `model-manager db set-cost <provider> <model> <usd_per_1m>`

### トレードオフ

- DBファイルが `%APPDATA%` に存在しないと動かない（初回自動作成で対処）
- 複数マシンで使う場合、DBファイルの同期が必要（バイナリと同様の問題だが、バイナリより小さく転送しやすい）
- `modernc.org/sqlite` はバイナリサイズが増加（+~3MB）

### 実装優先度

| フェーズ | 内容 |
|---------|------|
| Phase 1 | `db.go` 作成 + seed + `providers`/`qwen_rotation` テーブル |
| Phase 2 | priority.go / recommend.go をDB読み出しに切り替え |
| Phase 3 | `mm db` サブコマンド群 |
| Phase 4 | `quota` テーブル + F-12 クォータAPI連携 |
| Phase 5 | `usage_log` + F-15 使用量ログ |

---

## 変更履歴

| 日付 | ADR | 変更 |
|------|-----|------|
| 2026-05-24 | 001-006 | 初版作成（V1実装後） |
| 2026-05-24 | 007 | V2レコメンド設計案追加 |
| 2026-05-24 | 008 | ModelSpec保存先の決定（Goハードコード）→ ADR-010で覆す |
| 2026-05-24 | 009 | TUI 3ペインレイアウトへ変更 |
| 2026-05-24 | 010 | SQLite移行決定（ADR-002/008を置き換え） |
| 2026-06-29 | 011 | hamachi統合 — 残高・使用量の一元管理（`adr/ADR-011-hamachi-integration.md`） |
| 2026-06-29 | 012 | shizuka MCP GCP常駐化 + BigQuery (FastAPI/SSE, `adr/ADR-012-gcp-mcp-bq.md`) |
