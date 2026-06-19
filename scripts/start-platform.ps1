# One command: cluster + platform + app ($0 cloud, local kind)
#Requires -Version 5.1
$ErrorActionPreference = "Stop"
$Scripts = Join-Path $PSScriptRoot "..\foundation\scripts"

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Enlight Lab Platform - START" -ForegroundColor Cyan
Write-Host "  Cost: `$0 cloud (local kind cluster)" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

. (Join-Path $PSScriptRoot "check-docker.ps1")
if (-not (Test-EnlightDocker)) { exit 1 }

& (Join-Path $Scripts "00-create-cluster.ps1")
& (Join-Path $Scripts "01-build-image.ps1")
& (Join-Path $Scripts "02-install-platform.ps1")
& (Join-Path $Scripts "03-deploy-app.ps1")

Write-Host ""
Write-Host "Starting port-forwards..." -ForegroundColor Yellow
& (Join-Path $PSScriptRoot "port-forward-all.ps1")

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  PLATFORM READY" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  App health:  http://localhost:30800/health"
Write-Host "  Grafana:     http://localhost:3000  (admin / enlight-admin)"
Write-Host "  ArgoCD:      http://localhost:8080"
Write-Host ""
Write-Host "  Manager demo: .\scripts\run-manager-demo.ps1"
Write-Host ""
