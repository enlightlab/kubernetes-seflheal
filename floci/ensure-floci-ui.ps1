# Clone floci-ui source for Docker build (first run only)
#Requires -Version 5.1
$ErrorActionPreference = "Stop"
$FlociDir = $PSScriptRoot
$VendorDir = Join-Path $FlociDir ".vendor\floci-ui"
$Marker = Join-Path $VendorDir "Dockerfile"

if (Test-Path $Marker) {
    Write-Host "Floci UI source already present." -ForegroundColor Gray
    exit 0
}

Write-Host "Downloading Floci UI (one-time, ~1 min)..." -ForegroundColor Cyan
New-Item -ItemType Directory -Path (Split-Path $VendorDir) -Force | Out-Null

$git = Get-Command git -ErrorAction SilentlyContinue
if (-not $git) {
    Write-Host "git is required to download floci-ui. Install Git for Windows and retry." -ForegroundColor Red
    exit 1
}

& git clone --depth 1 https://github.com/floci-io/floci-ui.git $VendorDir
if ($LASTEXITCODE -ne 0) {
    Write-Host "Failed to clone floci-ui." -ForegroundColor Red
    exit 1
}

Write-Host "Floci UI source ready at $VendorDir" -ForegroundColor Green
