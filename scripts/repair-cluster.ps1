# Fix broken cluster: delete and recreate fresh (5-10 min)
#Requires -Version 5.1
$ErrorActionPreference = "Stop"
$Scripts = Join-Path $PSScriptRoot "..\foundation\scripts"
$ClusterName = "enlight-lab"

Write-Host ""
Write-Host "========================================" -ForegroundColor Yellow
Write-Host "  REPAIR - delete and recreate cluster" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Yellow
Write-Host ""

. (Join-Path $PSScriptRoot "check-docker.ps1")
if (-not (Test-EnlightDocker)) { exit 1 }

Write-Host "Stopping port-forwards..." -ForegroundColor Gray
$prev = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
& (Join-Path $PSScriptRoot "stop-platform.ps1") 2>&1 | Out-Null
$ErrorActionPreference = $prev

Write-Host "Force deleting cluster $ClusterName ..." -ForegroundColor Yellow
& (Join-Path $PSScriptRoot "force-cleanup-docker.ps1")
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "STOP: Restart Docker Desktop, then run:" -ForegroundColor Red
    Write-Host "  .\scripts\force-cleanup-docker.ps1" -ForegroundColor Yellow
    Write-Host "  .\scripts\repair-cluster.ps1" -ForegroundColor Yellow
    exit 1
}

Write-Host "Creating fresh cluster..." -ForegroundColor Cyan
& (Join-Path $Scripts "00-create-cluster.ps1") -ForceRecreate
& (Join-Path $Scripts "01-build-image.ps1")
& (Join-Path $Scripts "02-install-platform.ps1")
& (Join-Path $Scripts "03-deploy-app.ps1")
& (Join-Path $PSScriptRoot "port-forward-all.ps1")

Write-Host ""
Write-Host "REPAIR DONE" -ForegroundColor Green
Write-Host "Open: http://localhost:30800/health" -ForegroundColor Cyan
