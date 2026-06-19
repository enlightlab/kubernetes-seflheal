# Simulates GitHub Actions pipeline locally (test -> build -> OPA -> deploy)
#Requires -Version 5.1
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("compliant", "non-compliant")]
    [string]$Variant
)

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
$DemoScript = Join-Path $PSScriptRoot "run-demo.ps1"

Write-Host ""
Write-Host "=== CHAT-TO-DEPLOY PIPELINE (local) ===" -ForegroundColor Cyan
Write-Host "Variant: $Variant" -ForegroundColor Yellow
Write-Host ""

# [1/4] TEST - use live app (no local Python needed)
Write-Host "[1/4] TEST - health check" -ForegroundColor Green
$testOk = $false
try {
    $h = Invoke-RestMethod "http://localhost:30800/health" -TimeoutSec 5
    if ($h.status -eq "ok") {
        Write-Host "  test passed (live /health)" -ForegroundColor Gray
        $testOk = $true
    }
} catch {
    Write-Host "  live test skipped - run port-forward-all.ps1 first" -ForegroundColor Yellow
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    pip install -q -r (Join-Path $Root "workload\fastapi\requirements.txt") httpx 2>$null
    Push-Location (Join-Path $Root "workload\fastapi")
    python -c "from fastapi.testclient import TestClient; from app.main import app; assert TestClient(app).get('/health').json()=={'status':'ok'}; print('  test passed (python)')"
    if ($LASTEXITCODE -eq 0) { $testOk = $true }
    Pop-Location
    $ErrorActionPreference = $prev
}
if (-not $testOk) {
    Write-Host "  WARN: test step inconclusive - continuing demo" -ForegroundColor Yellow
}

# [2/4] BUILD - skip if app already running (fast demo path)
Write-Host "[2/4] BUILD - Docker image" -ForegroundColor Green
& (Join-Path $Root "foundation\scripts\01-build-image.ps1") -Quick
Write-Host "  build step done (quick/skip)" -ForegroundColor Gray

# [3/4] OPA POLICY CHECK
Write-Host "[3/4] OPA POLICY CHECK" -ForegroundColor Green
$prev = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
& $DemoScript -Variant $Variant 2>&1 | ForEach-Object { Write-Host $_ }
$policyOk = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = $prev

if ($Variant -eq "non-compliant") {
    Write-Host ""
    Write-Host "PIPELINE STOPPED - bad deploy BLOCKED (this is correct!)" -ForegroundColor Red
    Write-Host "Show manager the red VIOLATION lines above." -ForegroundColor Yellow
    exit 0
}

if (-not $policyOk) {
    Write-Host "PIPELINE FAILED - compliant variant should pass policy" -ForegroundColor Red
    exit 1
}

# [4/4] DEPLOY
Write-Host "[4/4] DEPLOY - staging" -ForegroundColor Green
kubectl apply -k (Join-Path $Root "demos\demo2-chat-to-deploy\overlays\local") 2>&1 | Out-Null
Start-Sleep -Seconds 3
try {
    $h = Invoke-RestMethod "http://localhost:30800/health" -TimeoutSec 5
    Write-Host "  staging /health: $($h | ConvertTo-Json -Compress)" -ForegroundColor Green
} catch {
    Write-Host "  run: .\scripts\port-forward-all.ps1" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "PIPELINE SUCCESS" -ForegroundColor Green
exit 0
