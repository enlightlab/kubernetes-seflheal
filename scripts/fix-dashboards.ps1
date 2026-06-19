# Fix ArgoCD + Grafana for manager demo
#Requires -Version 5.1
$Root = Split-Path -Parent $PSScriptRoot
$Ctx = "kind-enlight-lab"

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  FIX ARGOCD + GRAFANA" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

kubectl config use-context $Ctx 2>$null

Write-Host "[1/5] Restarting ArgoCD and Grafana pods..." -ForegroundColor Yellow
$prev = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
kubectl rollout restart deployment/argocd-server -n argocd --request-timeout=30s 2>&1 | Out-Null
kubectl rollout restart deployment/monitoring-grafana -n monitoring --request-timeout=30s 2>&1 | Out-Null
$ErrorActionPreference = $prev

Write-Host "  waiting 45 sec for pods..." -ForegroundColor Gray
Start-Sleep -Seconds 45

kubectl get pods -n argocd -l app.kubernetes.io/name=argocd-server --request-timeout=20s 2>&1
kubectl get pods -n monitoring -l app.kubernetes.io/name=grafana --request-timeout=20s 2>&1
Write-Host ""

Write-Host "[2/5] Stopping old port-forwards..." -ForegroundColor Yellow
& (Join-Path $PSScriptRoot "stop-platform.ps1") 2>&1 | Out-Null

Write-Host "[3/5] Starting port-forwards (app 30800, ArgoCD 8082, Grafana 3000)..." -ForegroundColor Yellow
& (Join-Path $PSScriptRoot "port-forward-all.ps1")

Write-Host "[4/5] Testing URLs..." -ForegroundColor Yellow
Start-Sleep -Seconds 3
& (Join-Path $PSScriptRoot "check-urls.ps1")

Write-Host ""
Write-Host "[5/5] ArgoCD login" -ForegroundColor Yellow
Write-Host "  URL:      http://localhost:8082" -ForegroundColor Cyan
Write-Host "  User:     admin" -ForegroundColor White
Write-Host "  Password: (run command below)" -ForegroundColor White
Write-Host ""
Write-Host '  kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | ForEach-Object { [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($_)) }' -ForegroundColor Gray
Write-Host ""
Write-Host "Grafana: http://localhost:3000  (admin / enlight-admin)" -ForegroundColor Cyan
Write-Host ""
Write-Host "NOTE: If ArgoCD/Grafana pods show 0/1, close other kind clusters" -ForegroundColor Yellow
Write-Host "      (cost-poc, test-cluster) to free RAM, then run this script again." -ForegroundColor Yellow
Write-Host ""
