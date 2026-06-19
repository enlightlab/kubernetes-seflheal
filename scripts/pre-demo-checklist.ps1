# Pre-demo health checks
#Requires -Version 5.1
$Ctx = "kind-enlight-lab"

Write-Host "Enlight Lab - Pre-demo checklist" -ForegroundColor Cyan
Write-Host ""

$ok = $true

$prev = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[FAIL] Docker not running" -ForegroundColor Red
    $ok = $false
} else {
    Write-Host "[OK] Docker running" -ForegroundColor Green
}
$ErrorActionPreference = $prev

kubectl config use-context $Ctx 2>$null
if ($LASTEXITCODE -eq 0) {
    $nodes = kubectl get nodes --no-headers 2>$null
    if ($nodes) { Write-Host "[OK] Cluster $Ctx reachable" -ForegroundColor Green }
    else { Write-Host "[FAIL] No nodes" -ForegroundColor Red; $ok = $false }
} else {
    Write-Host "[WARN] Cluster not up - run start-platform.ps1" -ForegroundColor Yellow
}

try {
    $h = Invoke-RestMethod "http://localhost:30800/health" -TimeoutSec 3
    if ($h.status -eq "ok") { Write-Host "[OK] App health endpoint" -ForegroundColor Green }
} catch {
    Write-Host "[WARN] App not reachable on :30800" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Manual:" -ForegroundColor Yellow
Write-Host "  [ ] Recorded fallbacks in demos/*/recordings/"
Write-Host "  [ ] Rehearsed manager script 5x"
Write-Host "  [ ] executive-summary.md reviewed"

if (-not $ok) { exit 1 }
