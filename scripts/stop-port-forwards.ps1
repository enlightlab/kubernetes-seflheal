# Stop kubectl port-forwards (kills tracked PIDs + listeners on demo ports)
#Requires -Version 5.1
$PidFile = Join-Path $PSScriptRoot ".port-forward-pids.txt"

function Stop-ListenersOnPort {
    param([int]$Port)
    $ErrorActionPreference = "SilentlyContinue"
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        ForEach-Object {
            $proc = Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue
            if ($proc -and $proc.ProcessName -match 'kubectl') {
                Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
            }
        }
}

if (Test-Path $PidFile) {
    Get-Content $PidFile | ForEach-Object {
        $procId = ($_ -split '\s+')[0]
        if ($procId -match '^\d+$') {
            Stop-Process -Id ([int]$procId) -Force -ErrorAction SilentlyContinue
        }
    }
    Remove-Item $PidFile -Force
}

foreach ($port in @(30800, 8082, 3000)) {
    Stop-ListenersOnPort -Port $port
}
