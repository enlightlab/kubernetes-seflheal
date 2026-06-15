# Run ONCE tonight before tomorrow demo
#Requires -Version 5.1
$Root = Split-Path -Parent $PSScriptRoot

Write-Host ""
Write-Host "TONIGHT PREP - Tomorrow Demo" -ForegroundColor Cyan
Write-Host ""

Write-Host "[1/5] Free RAM + go live..." -ForegroundColor Yellow
& (Join-Path $Root "scripts\free-ram-for-demo.ps1")
& (Join-Path $Root "scripts\go-live.ps1")

Write-Host "[2/5] Test all..." -ForegroundColor Yellow
& (Join-Path $Root "scripts\test-all.ps1")

Write-Host "[3/5] Demo 4 baseline (Floci + Terraform)..." -ForegroundColor Yellow
& (Join-Path $Root "demos\demo4-drift-cost\scripts\run-demo.ps1") -Phase baseline

Write-Host "[4/5] Quick smoke - Demo 3 + 5..." -ForegroundColor Yellow
& (Join-Path $Root "demos\demo5-pr-compliance\scripts\run-demo.ps1") -Variant non-compliant
& (Join-Path $Root "demos\demo3-backstage-idp\scripts\run-demo.ps1") -ServiceName demo-api

Write-Host "[5/5] URLs..." -ForegroundColor Yellow
& (Join-Path $Root "scripts\check-urls.ps1")

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  TONIGHT PREP DONE" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Now rehearse: .\run-tomorrow-demo.bat" -ForegroundColor Cyan
Write-Host "Read: TOMORROW-DEMO.md" -ForegroundColor Cyan
