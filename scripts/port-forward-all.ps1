# Background port-forwards for manager demo (tolerates slow cluster API)
#Requires -Version 5.1
$PidFile = Join-Path $PSScriptRoot ".port-forward-pids.txt"
$Ctx = "kind-enlight-lab"

function Start-Forward {
    param([string]$Name, [string[]]$KubectlArgs, [int]$LocalPort)
    $proc = Start-Process -FilePath "kubectl" -ArgumentList $KubectlArgs `
        -WindowStyle Hidden -PassThru
    Add-Content $PidFile "$($proc.Id) $Name localhost:$LocalPort"
    Write-Host "  $Name -> localhost:$LocalPort (pid $($proc.Id))" -ForegroundColor Gray
}

$prevEap = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
kubectl config use-context $Ctx 2>&1 | Out-Null
$ErrorActionPreference = $prevEap

& (Join-Path $PSScriptRoot "stop-port-forwards.ps1")
New-Item -ItemType File -Path $PidFile -Force | Out-Null

Write-Host "Starting port-forwards..." -ForegroundColor Cyan

# Always start app + ArgoCD (do not let slow API block these)
Start-Forward "fastapi" @("port-forward", "-n", "enlight-staging", "svc/fastapi", "30800:80") 30800
# insecure mode in helm values -> service port 80
# Port 8080 is often taken by k8sgpt MCP - use 8082 for ArgoCD
Start-Forward "argocd" @("port-forward", "-n", "argocd", "svc/argocd-server", "8082:80") 8082

# Grafana optional - skip if API slow
$ErrorActionPreference = "SilentlyContinue"
kubectl get svc monitoring-grafana -n monitoring --request-timeout=5s 2>&1 | Out-Null
if ($LASTEXITCODE -eq 0) {
    Start-Forward "grafana" @("port-forward", "-n", "monitoring", "svc/monitoring-grafana", "3000:80") 3000
} else {
    Write-Host "  grafana skipped (API slow or monitoring not ready)" -ForegroundColor Yellow
}
$ErrorActionPreference = $prevEap

Start-Sleep -Seconds 2
Write-Host ""
Write-Host "Port-forwards started." -ForegroundColor Green
Write-Host "  App:     http://localhost:30800/health" -ForegroundColor Cyan
Write-Host "  ArgoCD:  http://localhost:8082" -ForegroundColor Cyan
Write-Host "  Grafana: http://localhost:3000 (only if line above shows grafana pid)" -ForegroundColor Gray
Write-Host "Stop with: .\scripts\stop-platform.ps1" -ForegroundColor Gray
