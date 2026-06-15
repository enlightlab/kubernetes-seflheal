# Run all 5 demos in order (local/kind)
#Requires -Version 5.1
$Root = Split-Path -Parent $PSScriptRoot

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  ENLIGHT LAB - ALL DEMOS" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

Write-Host ">>> Demo 2: Chat-to-Deploy" -ForegroundColor Green
& (Join-Path $Root "demos\demo2-chat-to-deploy\scripts\run-demo.ps1") -Variant non-compliant
& (Join-Path $Root "demos\demo2-chat-to-deploy\scripts\run-demo.ps1") -Variant compliant
Read-Host "Press Enter for Demo 1"

Write-Host ">>> Demo 1: Incident Response" -ForegroundColor Green
& (Join-Path $Root "demos\demo1-incident-response\scripts\inject-failure.ps1")
Write-Host "Ask k8sgpt/Holmes to explain, then press Enter"
Read-Host "Press Enter for heal"
& (Join-Path $Root "demos\demo1-incident-response\scripts\heal-rollback.ps1")
Read-Host "Press Enter for Demo 4"

Write-Host ">>> Demo 4: Drift & Cost" -ForegroundColor Green
& (Join-Path $Root "demos\demo4-drift-cost\scripts\run-demo.ps1") -Phase baseline
& (Join-Path $Root "demos\demo4-drift-cost\scripts\run-demo.ps1") -Phase drift
& (Join-Path $Root "demos\demo4-drift-cost\scripts\run-demo.ps1") -Phase reconcile
Read-Host "Press Enter for Demo 5"

Write-Host ">>> Demo 5: PR Compliance" -ForegroundColor Green
& (Join-Path $Root "demos\demo5-pr-compliance\scripts\run-demo.ps1") -Variant non-compliant
& (Join-Path $Root "demos\demo5-pr-compliance\scripts\run-demo.ps1") -Variant compliant
Read-Host "Press Enter for Demo 3"

Write-Host ">>> Demo 3: Backstage IDP" -ForegroundColor Green
& (Join-Path $Root "demos\demo3-backstage-idp\scripts\run-demo.ps1") -ServiceName demo-api

Write-Host ""
Write-Host "All demos complete." -ForegroundColor Green
