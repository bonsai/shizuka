# BigQuery DDL — model_status dataset

CREATE TABLE IF NOT EXISTS model_status.providers (
    model_key   STRING NOT NULL,
    provider    STRING NOT NULL,
    model_id    STRING NOT NULL,
    tier        STRING NOT NULL,
    cost_in     FLOAT64,
    quality_low  INT64,
    quality_mid  INT64,
    quality_high INT64,
    quality_code INT64,
    priority    INT64,
    note        STRING,
    updated_at  TIMESTAMP
)
CLUSTER BY priority, provider;

CREATE TABLE IF NOT EXISTS model_status.cli_priority (
    cli         STRING NOT NULL,
    priority    INT64 NOT NULL,
    model_key   STRING NOT NULL
)
CLUSTER BY cli, priority;

CREATE TABLE IF NOT EXISTS model_status.quota (
    provider    STRING NOT NULL,
    model_key   STRING NOT NULL,
    remaining   FLOAT64,
    tokens_remaining INT64,
    tokens_limit     INT64,
    checked_at  TIMESTAMP NOT NULL
)
CLUSTER BY provider, model_key;

CREATE TABLE IF NOT EXISTS model_status.rotation_state (
    model_key   STRING NOT NULL,
    sort_order  INT64 NOT NULL,
    expired     BOOL,
    expires_at  TIMESTAMP,
    last_error  STRING,
    updated_at  TIMESTAMP
)
CLUSTER BY model_key;

CREATE TABLE IF NOT EXISTS model_status.usage_log (
    id          INT64 NOT NULL,
    cli         STRING NOT NULL,
    model_key   STRING NOT NULL,
    task_type   STRING,
    tokens_in   INT64,
    tokens_out  INT64,
    cost        FLOAT64,
    latency_ms  INT64,
    success     BOOL,
    error_msg   STRING,
    logged_at   TIMESTAMP
)
PARTITION BY DATE(logged_at)
CLUSTER BY model_key, cli, task_type;

CREATE TABLE IF NOT EXISTS model_status.recovery_estimate (
    model_key        STRING NOT NULL,
    estimated_at     TIMESTAMP,
    confidence       FLOAT64,
    based_on         STRING,
    cached_remaining FLOAT64,
    updated_at       TIMESTAMP
)
CLUSTER BY model_key;

CREATE TABLE IF NOT EXISTS model_status.ml_params (
    param_key   STRING NOT NULL,
    param_value FLOAT64 NOT NULL,
    updated_at  TIMESTAMP
)
CLUSTER BY param_key;

CREATE TABLE IF NOT EXISTS model_status.balance_snapshots (
    provider        STRING NOT NULL,
    total_balance   FLOAT64 NOT NULL,
    currency        STRING DEFAULT 'USD',
    is_available    BOOL DEFAULT TRUE,
    topped_up       FLOAT64 DEFAULT 0,
    granted         FLOAT64 DEFAULT 0,
    timestamp       TIMESTAMP NOT NULL
)
CLUSTER BY provider, timestamp;

CREATE OR REPLACE VIEW model_status.v_available AS
SELECT p.*, q.remaining, r.expired, r.expires_at, rec.estimated_at, rec.confidence
FROM model_status.providers p
LEFT JOIN model_status.quota q ON q.model_key = p.model_key
LEFT JOIN model_status.rotation_state r ON r.model_key = p.model_key
LEFT JOIN model_status.recovery_estimate rec ON rec.model_key = p.model_key
WHERE (r.expired IS NULL OR r.expired = FALSE)
  AND (q.remaining IS NULL OR q.remaining > 0)
ORDER BY p.priority;
