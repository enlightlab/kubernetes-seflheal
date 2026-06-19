# Start Enlight Lab Demo Control Center (host-side UI on :30900)
#Requires -Version 5.1
$Root = Split-Path -Parent $PSScriptRoot
$Ctrl = Join-Path $PSScriptRoot "demo-control"
$PidFile = Join-Path $PSScriptRoot ".demo-control-pid.txt"
$Port = 30900

if (Test-Path $PidFile) {
    $old = Get-Content $PidFile -ErrorAction SilentlyContinue
    if ($old -match '^\d+$') {
        Stop-Process -Id ([int]$old) -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "Starting Demo Control Center on http://localhost:$Port ..." -ForegroundColor Cyan
pip install --user -q -r (Join-Path $Ctrl "requirements.txt") 2>$null | Out-Null

$job = Start-Process -FilePath "python" -ArgumentList @(
    "-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", $Port
) -WorkingDirectory $Ctrl -PassThru -WindowStyle Hidden

$job.Id | Set-Content $PidFile
Start-Sleep -Seconds 2

try {
    $r = Invoke-WebRequest "http://localhost:$Port/" -UseBasicParsing -TimeoutSec 5
    if ($r.StatusCode -eq 200) {
        Write-Host "Demo Control Center: http://localhost:$Port" -ForegroundColor Green
        Write-Host "Share THIS window for client / manager demos." -ForegroundColor Yellow
    }
} catch {
    Write-Host "Demo Control may still be starting - open http://localhost:$Port" -ForegroundColor Yellow
}
