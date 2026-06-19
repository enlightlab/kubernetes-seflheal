# Start local cloud sandbox for Demo 4 (runs behind Demo Control — no extra browser URL)
#Requires -Version 5.1
$ErrorActionPreference = "Stop"
$FlociDir = $PSScriptRoot

Write-Host "Starting cloud sandbox for config-guard demo..." -ForegroundColor Cyan
Push-Location $FlociDir
docker compose up -d
Pop-Location

Start-Sleep -Seconds 3

$env:AWS_ENDPOINT_URL = "http://localhost:4566"
$env:AWS_DEFAULT_REGION = "us-east-1"
$env:AWS_ACCESS_KEY_ID = "test"
$env:AWS_SECRET_ACCESS_KEY = "test"

Write-Host "Cloud sandbox ready (used automatically by Demo Control)." -ForegroundColor Green
