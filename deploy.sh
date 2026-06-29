#!/usr/bin/env bash
set -euo pipefail
# shizuka-mcp GCP デプロイスクリプト
# 使い方: bash deploy.sh
# 前提: gcloud 認証済み, プロジェクト yok-ai-2026

PROJECT="${GCP_PROJECT:-yok-ai-2026}"
REGION="${GCP_REGION:-asia-northeast1}"
DATASET="${BQ_DATASET:-model_status}"
SERVICE="shizuka-mcp"

echo "=== 1/5: BQ データセット作成 ==="
bq mk --dataset --location=asia-northeast1 "${PROJECT}:${DATASET}" 2>/dev/null || echo "already exists"

echo "=== 2/5: BQ DDL 適用 ==="
bq query --nouse_legacy_sql --project_id="${PROJECT}" < schema/bq_ddl.sql

echo "=== 3/5: BQ テーブル一覧 ==="
bq ls --dataset="${PROJECT}:${DATASET}"

echo "=== 4/5: Cloud Run デプロイ ==="
gcloud run deploy "${SERVICE}" \
  --source . \
  --region="${REGION}" \
  --allow-unauthenticated \
  --min-instances=0 \
  --max-instances=1 \
  --memory=256Mi \
  --cpu=1 \
  --timeout=300 \
  --set-env-vars="GCP_PROJECT=${PROJECT},BQ_DATASET=${DATASET}"

echo "=== 5/5: デプロイ確認 ==="
SERVICE_URL=$(gcloud run services describe "${SERVICE}" --region="${REGION}" --format='value(status.url)')
echo "Service URL: ${SERVICE_URL}/sse"
echo "Health:      ${SERVICE_URL}/health"
echo ""
echo "opencode.jsonc に追加:"
echo "  \"shizuka-mcp\": {"
echo "    \"url\": \"${SERVICE_URL}/sse\""
echo "  }"
