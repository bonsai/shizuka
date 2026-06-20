-- model-status.db スキーマ
-- 全CLI共有: models.agents.md の動的状態 + 使用実績 + 復帰予測

-- プロバイダー・モデル定義（models.agents.md YAML から同期）
CREATE TABLE IF NOT EXISTS providers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    model_key   TEXT NOT NULL UNIQUE,        -- models.agents.md のキー
    provider    TEXT NOT NULL,
    model_id    TEXT NOT NULL,
    tier        TEXT NOT NULL DEFAULT 'free', -- free | paid
    cost_in     REAL DEFAULT 0,              -- $/1M input tokens
    quality_low  INTEGER DEFAULT 5,
    quality_mid  INTEGER DEFAULT 5,
    quality_high INTEGER DEFAULT 5,
    quality_code INTEGER DEFAULT 5,
    priority    INTEGER DEFAULT 999,        -- P1=1, P2=2, ...
    note        TEXT DEFAULT ''
);

-- 各CLIの現在の優先順位（モデル選択順）
CREATE TABLE IF NOT EXISTS cli_priority (
    cli         TEXT NOT NULL,
    priority    INTEGER NOT NULL,           -- 1-based order
    model_key   TEXT NOT NULL REFERENCES providers(model_key),
    PRIMARY KEY (cli, priority)
);

-- クォータ残量（リアルタイム）
CREATE TABLE IF NOT EXISTS quota (
    provider    TEXT NOT NULL,
    model_key   TEXT DEFAULT '',
    remaining   REAL DEFAULT 100.0,         -- 残量 % (0-100)
    tokens_remaining INTEGER DEFAULT -1,    -- トークン残量（わかる場合）
    tokens_limit     INTEGER DEFAULT -1,
    checked_at  TEXT NOT NULL,              -- ISO timestamp
    PRIMARY KEY (provider, model_key)
);

-- Qwen ローテーション状態
CREATE TABLE IF NOT EXISTS rotation_state (
    model_key   TEXT NOT NULL UNIQUE REFERENCES providers(model_key),
    sort_order  INTEGER NOT NULL,
    expired     INTEGER DEFAULT 0,          -- 1 = 枯渇/期限切れ
    expires_at  TEXT DEFAULT '',            -- 復帰予定 ISO timestamp
    last_error  TEXT DEFAULT '',            -- 最後のエラー内容
    updated_at  TEXT DEFAULT (datetime('now'))
);

-- 使用履歴（ML訓練データ）
CREATE TABLE IF NOT EXISTS usage_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    cli         TEXT NOT NULL,
    model_key   TEXT NOT NULL REFERENCES providers(model_key),
    task_type   TEXT DEFAULT '',            -- code | long | quick | other
    tokens_in   INTEGER DEFAULT 0,
    tokens_out  INTEGER DEFAULT 0,
    cost        REAL DEFAULT 0,
    latency_ms  INTEGER DEFAULT 0,
    success     INTEGER DEFAULT 1,          -- 1=成功 0=エラー
    error_msg   TEXT DEFAULT '',
    timestamp   TEXT DEFAULT (datetime('now'))
);

-- 復帰予測（usage_log から算出 or 手動設定）
CREATE TABLE IF NOT EXISTS recovery_estimate (
    model_key   TEXT NOT NULL UNIQUE REFERENCES providers(model_key),
    estimated_at   TEXT,                    -- 復帰予定時刻 ISO
    confidence     REAL DEFAULT 0.5,        -- 0.0-1.0
    based_on       TEXT DEFAULT '',         -- quota | pattern | manual
    cached_remaining REAL DEFAULT 0,        -- 最後に確認した残量
    updated_at     TEXT DEFAULT (datetime('now'))
);

-- ML モデル学習パラメータ（簡単な線形回帰モデル用）
CREATE TABLE IF NOT EXISTS ml_params (
    param_key   TEXT PRIMARY KEY,
    param_value REAL NOT NULL,
    updated_at  TEXT DEFAULT (datetime('now'))
);

-- ビュー: 現在選択可能なモデル（優先順位順・枯渇除外）
CREATE VIEW IF NOT EXISTS v_available AS
SELECT p.*, q.remaining, r.expired, r.expires_at, rec.estimated_at, rec.confidence
FROM providers p
LEFT JOIN quota q ON q.model_key = p.model_key
LEFT JOIN rotation_state r ON r.model_key = p.model_key
LEFT JOIN recovery_estimate rec ON rec.model_key = p.model_key
WHERE (r.expired IS NULL OR r.expired = 0)
  AND (q.remaining IS NULL OR q.remaining > 0)
ORDER BY p.priority;

-- インデックス
CREATE INDEX IF NOT EXISTS idx_usage_model ON usage_log(model_key, timestamp);
CREATE INDEX IF NOT EXISTS idx_usage_cli ON usage_log(cli, timestamp);
CREATE INDEX IF NOT EXISTS idx_usage_task ON usage_log(task_type);
