# TOMORROW MANAGER DEMO - all 5 demos (~20 min)
# Run AFTER: .\scripts\go-live.ps1
#Requires -Version 5.1
$Root = Split-Path -Parent $PSScriptRoot

function Wait-Enter($msg) {
    Write-Host ""
    Write-Host $msg -ForegroundColor Yellow
    Read-Host "Press Enter to continue"
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  ENLIGHT LAB - MANAGER DEMO (ALL 5)" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Open tabs: http://localhost:30800 | :8082 ArgoCD | GitHub Actions | platform.robusta.dev" -ForegroundColor Gray
Wait-Enter "PART 0 - Opening. SAY: Unified platform after 2 PoCs. Zero cloud cost. All local."

Write-Host ""
Write-Host "PART 1 - Live app" -ForegroundColor Green
Write-Host "SHOW: http://localhost:30800 (dashboard UI)" -ForegroundColor Cyan
try {
    $h = Invoke-RestMethod "http://localhost:30800/health" -TimeoutSec 5
    Write-Host "Health API: $($h | ConvertTo-Json -Compress)" -ForegroundColor Green
} catch { Write-Host "Run go-live.ps1 first!" -ForegroundColor Red }
Write-Host "SHOW: ArgoCD http://localhost:8082 -> fastapi-staging" -ForegroundColor Cyan
Wait-Enter "PART 1 done."

Write-Host ""
Write-Host "PART 2 - Demo 2 BLOCK" -ForegroundColor Green
& (Join-Path $Root "demos\demo2-chat-to-deploy\scripts\run-demo.ps1") -Variant non-compliant
Write-Host "OPTIONAL: trigger GitHub Actions non-compliant from Cursor MCP" -ForegroundColor Gray
Wait-Enter "PART 2 done - show VIOLATIONS."

Write-Host ""
Write-Host "PART 3 - Demo 2 PASS" -ForegroundColor Green
& (Join-Path $Root "demos\demo2-chat-to-deploy\scripts\run-demo.ps1") -Variant compliant
Write-Host "SHOW: http://localhost:30800/health" -ForegroundColor Cyan
Wait-Enter "PART 3 done."

Write-Host ""
Write-Host "PART 4 - Demo 1 Incident" -ForegroundColor Green
& (Join-Path $Root "demos\demo1-incident-response\scripts\inject-failure.ps1")
Write-Host "SHOW: Robusta/Holmes OR ask Cursor k8sgpt to explain" -ForegroundColor Cyan
Wait-Enter "After AI explains, press Enter for rollback."
& (Join-Path $Root "demos\demo1-incident-response\scripts\heal-rollback.ps1")
Wait-Enter "PART 4 done."

Write-Host ""
Write-Host "PART 5 - Demo 4 Drift & Cost" -ForegroundColor Green
& (Join-Path $Root "demos\demo4-drift-cost\scripts\run-demo.ps1") -Phase drift
Wait-Enter "Show DRIFT DETECTED. Press Enter to reconcile."
& (Join-Path $Root "demos\demo4-drift-cost\scripts\run-demo.ps1") -Phase reconcile
Wait-Enter "PART 5 done."

Write-Host ""
Write-Host "PART 6 - Demo 5 PR Compliance" -ForegroundColor Green
& (Join-Path $Root "demos\demo5-pr-compliance\scripts\run-demo.ps1") -Variant non-compliant
& (Join-Path $Root "demos\demo5-pr-compliance\scripts\run-demo.ps1") -Variant compliant
Wait-Enter "PART 6 done."

Write-Host ""
Write-Host "PART 7 - Demo 3 Backstage IDP" -ForegroundColor Green
& (Join-Path $Root "demos\demo3-backstage-idp\scripts\run-demo.ps1") -ServiceName demo-api
Write-Host "SHOW folder: workload\scaffolded\demo-api" -ForegroundColor Cyan
Wait-Enter "PART 7 done."

Write-Host ""
Write-Host "CLOSING" -ForegroundColor Green
Write-Host @"
SAY:
- 5 demos on one local platform
- Policy CI + cluster, GitOps, monitoring, AI ops
- Zero cloud cost, production-equivalent patterns
- Demos 1-2 live; 3-5 demonstrated locally today
"@ -ForegroundColor White
Write-Host ""
Write-Host "Demo complete. Good luck!" -ForegroundColor Green
