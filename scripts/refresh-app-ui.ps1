# Rebuild FastAPI with new dashboard UI and redeploy
#Requires -Version 5.1
$Root = Split-Path -Parent $PSScriptRoot

Write-Host "Rebuilding Enlight Lab UI..." -ForegroundColor Cyan
& (Join-Path $Root "foundation\scripts\01-build-image.ps1")
kubectl apply -k (Join-Path $Root "demos\demo2-chat-to-deploy\overlays\local")
kubectl rollout restart deployment/fastapi -n enlight-staging
kubectl rollout status deployment/fastapi -n enlight-staging --timeout=120s

Write-Host ""
Write-Host "Open: http://localhost:30800" -ForegroundColor Green
Write-Host "(run port-forward-all.ps1 if needed)" -ForegroundColor Gray
