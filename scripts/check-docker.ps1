# Verify Docker Desktop is running before any platform scripts
#Requires -Version 5.1

function Test-EnlightDocker {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-Host ""
        Write-Host "Docker not installed." -ForegroundColor Red
        Write-Host "Install: https://www.docker.com/products/docker-desktop/" -ForegroundColor Yellow
        return $false
    }

    $prev = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    docker info *> $null
    $ok = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $prev

    if (-not $ok) {
        Write-Host ""
        Write-Host "========================================" -ForegroundColor Red
        Write-Host "  Docker Desktop is NOT running" -ForegroundColor Red
        Write-Host "========================================" -ForegroundColor Red
        Write-Host ""
        Write-Host "  1. Open Docker Desktop from Start menu" -ForegroundColor Yellow
        Write-Host "  2. Wait until it says 'Engine running'" -ForegroundColor Yellow
        Write-Host "  3. Test:  docker info" -ForegroundColor Yellow
        Write-Host "  4. Retry:  .\scripts\start-platform.ps1" -ForegroundColor Yellow
        Write-Host ""
        return $false
    }

    return $true
}

if ($MyInvocation.InvocationName -ne '.') {
    if (Test-EnlightDocker) {
        Write-Host "Docker OK" -ForegroundColor Green
        exit 0
    }
    exit 1
}
