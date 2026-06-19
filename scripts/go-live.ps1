# Port-forwards for self-heal demo (app + ArgoCD)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$PfScript = Join-Path $Root "scripts\port-forward-all.ps1"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "Docker not found. Install Docker Desktop for the kind cluster." -ForegroundColor Red
    exit 1
}
docker info 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Docker Desktop is not running." -ForegroundColor Red
    Write-Host "Start Docker Desktop, then run go-live.bat in D:\enlight-lab-platform first." -ForegroundColor Yellow
    exit 1
}

if (-not (Get-Command kubectl -ErrorAction SilentlyContinue)) {
    Write-Host "kubectl not found. Install kubectl and ensure kind-enlight-lab cluster is running." -ForegroundColor Red
    exit 1
}

$ctx = kubectl config current-context 2>$null
if ($ctx -ne "kind-enlight-lab") {
    Write-Host "Switching context to kind-enlight-lab..." -ForegroundColor Yellow
    kubectl config use-context kind-enlight-lab | Out-Null
}

& $PfScript
Write-Host ""
Write-Host "Port forwards active:" -ForegroundColor Green
Write-Host "  App:    http://localhost:30800/health"
Write-Host "  ArgoCD: http://localhost:8082"
Write-Host ""
Write-Host "Next: run start-selfheal-ui.bat and open http://localhost:30901"
