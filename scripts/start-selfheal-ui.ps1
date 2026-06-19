# Start Kube Self-Heal web UI on port 30901
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Web = Join-Path $Root "web"

Set-Location $Web

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "Python not found. Install Python 3.10+." -ForegroundColor Red
    exit 1
}

$venv = Join-Path $Web ".venv"
if (-not (Test-Path $venv)) {
    Write-Host "Creating virtual environment..." -ForegroundColor Cyan
    python -m venv $venv
    & "$venv\Scripts\pip.exe" install -q -r requirements.txt
}

$env:ENLIGHT_LAB_ROOT = if ($env:ENLIGHT_LAB_ROOT) { $env:ENLIGHT_LAB_ROOT } else { "D:\enlight-lab-platform" }

Write-Host "Starting Kube Self-Heal UI at http://localhost:30901" -ForegroundColor Green
Write-Host "Using cluster overlay from: $env:ENLIGHT_LAB_ROOT"
Write-Host ""

& "$venv\Scripts\python.exe" -m uvicorn server:app --host 0.0.0.0 --port 30901 --app-dir $Web
