# Stop port-forwards (cluster stays up for fast restart)
#Requires -Version 5.1
$PidFile = Join-Path $PSScriptRoot ".port-forward-pids.txt"

if (-not (Test-Path $PidFile)) {
    Write-Host "No port-forwards tracked." -ForegroundColor Yellow
    exit 0
}

Get-Content $PidFile | ForEach-Object {
    $procId = ($_ -split '\s+')[0]
    if ($procId -match '^\d+$') {
        Stop-Process -Id ([int]$procId) -Force -ErrorAction SilentlyContinue
    }
}
Remove-Item $PidFile -Force
Write-Host "Port-forwards stopped." -ForegroundColor Green
Write-Host "Full teardown: .\foundation\scripts\99-destroy.ps1" -ForegroundColor Gray
