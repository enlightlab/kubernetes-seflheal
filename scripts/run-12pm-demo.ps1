# Full 12-minute manager demo - run after go-live.ps1
#Requires -Version 5.1
$Root = Split-Path -Parent $PSScriptRoot

function Wait-Enter($msg) {
    Write-Host ""; Write-Host $msg -ForegroundColor Yellow; Read-Host "Press Enter"
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  12PM ENLIGHT LAB DEMO" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

Wait-Enter "PART 0 - Opening line. Press Enter when ready."

Write-Host ""
Write-Host 'SAY: "Unified Enlight Lab platform. Zero cloud cost locally. Phase 2 after two PoCs."' -ForegroundColor Gray
Write-Host ""

Write-Host "PART 1 - Live app" -ForegroundColor Green
Write-Host "Open: http://localhost:30800/health" -ForegroundColor Cyan
try {
    $h = Invoke-RestMethod "http://localhost:30800/health" -TimeoutSec 5
    Write-Host "Health: $($h | ConvertTo-Json -Compress)" -ForegroundColor Green
} catch {
    Write-Host "Run .\scripts\go-live.ps1 first" -ForegroundColor Red
}
Wait-Enter "PART 1 done."

Write-Host ""
Write-Host "PART 2 - Demo 2 BLOCK (bad deploy)" -ForegroundColor Green
& (Join-Path $Root "demos\demo2-chat-to-deploy\scripts\run-full-pipeline.ps1") -Variant non-compliant
Wait-Enter "PART 2 done - show red VIOLATIONS."

Write-Host ""
Write-Host "PART 3 - Demo 2 PASS (good deploy)" -ForegroundColor Green
& (Join-Path $Root "demos\demo2-chat-to-deploy\scripts\run-full-pipeline.ps1") -Variant compliant
Wait-Enter "PART 3 done - show /health ok."

Write-Host ""
Write-Host "PART 4 - Cluster policy (Kyverno)" -ForegroundColor Green
$prev = $ErrorActionPreference; $ErrorActionPreference = "SilentlyContinue"
kubectl apply -f (Join-Path $Root "foundation\policies\kyverno\test-violation-pod.yaml") 2>&1
$ErrorActionPreference = $prev
Write-Host "Expected: denied by policy" -ForegroundColor Yellow
Wait-Enter "PART 4 done."

Write-Host ""
Write-Host "PART 5 - Demo 1 failure + heal (optional)" -ForegroundColor Green
Write-Host "inject-failure.ps1 -> AI explains (MCP) -> heal-rollback.ps1" -ForegroundColor Gray
$ans = Read-Host "Run Demo 1 live? (y/n)"
if ($ans -eq "y") {
    & (Join-Path $Root "demos\demo1-incident-response\scripts\inject-failure.ps1")
    Wait-Enter "Ask Claude/k8sgpt to explain the failure. Then press Enter for rollback."
    & (Join-Path $Root "demos\demo1-incident-response\scripts\heal-rollback.ps1")
}

Write-Host ""
Write-Host "PART 6 - Close" -ForegroundColor Green
Write-Host @"
SAY:
- Policy at CI (OPA) and cluster (Kyverno)
- GitOps with ArgoCD self-heal
- Live app on Kubernetes, `$0 cloud locally
- Next: Claude MCP triggers GitHub Actions on EKS

OUTCOMES: block bad deploys, deploy good ones, recover from failure
"@ -ForegroundColor White

Write-Host "Demo complete." -ForegroundColor Green
