# shizuka-mcp Cloud Run deploy (Windows PowerShell)
# 使い方: powershell -ExecutionPolicy Bypass -File deploy.ps1
# 前提: gcloud auth login 済み, git clone 済み

$PROJECT = "yok-ai-2026"
$REGION = "asia-northeast1"
$SERVICE = "shizuka-mcp"
$REPO = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "=== 1/5: BQ dataset ===" -ForegroundColor Cyan
bq mk --dataset --location=$REGION "$PROJECT`:model_status" 2>$null
Write-Host "OK" -ForegroundColor Green

Write-Host "=== 2/5: BQ DDL ===" -ForegroundColor Cyan
Get-Content "$REPO\schema\bq_ddl.sql" | bq query --nouse_legacy_sql --project_id=$PROJECT
Write-Host "OK" -ForegroundColor Green

Write-Host "=== 3/5: Tables ===" -ForegroundColor Cyan
bq ls --dataset="$PROJECT`:model_status"

Write-Host "=== 4/5: Cloud Run deploy ===" -ForegroundColor Cyan
gcloud run deploy $SERVICE `
  --source $REPO `
  --region=$REGION `
  --allow-unauthenticated `
  --min-instances=0 `
  --max-instances=1 `
  --memory=256Mi --cpu=1 --timeout=300 `
  --set-env-vars="GCP_PROJECT=$PROJECT,BQ_DATASET=model_status"

if ($LASTEXITCODE -ne 0) {
    Write-Host "DEPLOY FAILED" -ForegroundColor Red
    exit 1
}

Write-Host "=== 5/5: Verify ===" -ForegroundColor Cyan
$URL = gcloud run services describe $SERVICE --region=$REGION --format="value(status.url)"
Write-Host "Service URL: ${URL}/sse" -ForegroundColor Green
Write-Host "Health:      ${URL}/health" -ForegroundColor Green

Write-Host ""
Write-Host "opencode.jsonc に追加:" -ForegroundColor Yellow
Write-Host "  ""shizuka-mcp"": {"
Write-Host "    ""url"": ""${URL}/sse"""
Write-Host "  }"
