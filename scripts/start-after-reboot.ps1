# After PC reboot: verify cluster, recreate if dead, redeploy app
#Requires -Version 5.1
$ErrorActionPreference = "Stop"
$Scripts = Join-Path $PSScriptRoot "..\foundation\scripts"
$Ctx = "kind-enlight-lab"
$ClusterName = "enlight-lab"

function Test-ClusterReachable {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    kubectl config use-context $Ctx 2>&1 | Out-Null
    kubectl get nodes --request-timeout=8s 2>&1 | Out-Null
    $ok = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $prev
    return $ok
}

function Repair-KindCluster {
    Write-Host "Cluster unreachable - repairing..." -ForegroundColor Yellow

    $prev = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    $listed = (kind get clusters 2>&1 | Out-String) -match $ClusterName
    if ($listed) {
        Write-Host "Deleting stale kind cluster $ClusterName ..." -ForegroundColor Gray
        kind delete cluster --name $ClusterName 2>&1 | Out-Null
    }
    $ErrorActionPreference = $prev

    & (Join-Path $Scripts "00-create-cluster.ps1")
    & (Join-Path $Scripts "02-install-platform.ps1")
}

Write-Host "Enlight Lab - resume after reboot" -ForegroundColor Cyan

$prev = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    $ErrorActionPreference = $prev
    throw "Start Docker Desktop first, wait for Engine running, then retry."
}
$ErrorActionPreference = $prev
Write-Host "Docker OK" -ForegroundColor Green

if (-not (Test-ClusterReachable)) {
    Write-Host "Cluster API not responding (common after Docker restart)." -ForegroundColor Yellow
    Repair-KindCluster
} else {
    Write-Host "Cluster OK" -ForegroundColor Green
    kubectl get nodes
}

& (Join-Path $Scripts "01-build-image.ps1")

Write-Host "Deploying app..." -ForegroundColor Yellow
kubectl apply -k (Join-Path $PSScriptRoot "..\demos\demo2-chat-to-deploy\overlays\local")

$prev = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
kubectl rollout status deployment/fastapi -n enlight-staging --timeout=180s 2>&1 | ForEach-Object { Write-Host $_ }
$ErrorActionPreference = $prev

kubectl get pods -n enlight-staging

& (Join-Path $PSScriptRoot "port-forward-all.ps1")

Write-Host ""
Write-Host "Ready: http://localhost:30800/health" -ForegroundColor Green
