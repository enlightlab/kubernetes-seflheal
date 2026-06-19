# Deploy FastAPI to enlight-staging (local kind overlay)
#Requires -Version 5.1
$ErrorActionPreference = "Stop"
$PlatformRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$Overlay = Join-Path $PlatformRoot "demos\demo2-chat-to-deploy\overlays\local"
$Ctx = "kind-enlight-lab"

kubectl config use-context $Ctx 2>$null

Write-Host "Deploying FastAPI (local overlay)..." -ForegroundColor Cyan
kubectl apply -k $Overlay

Write-Host "Waiting for pod (up to 3 min)..." -ForegroundColor Yellow
$prev = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
kubectl rollout status deployment/fastapi -n enlight-staging --timeout=180s 2>&1 | ForEach-Object { Write-Host $_ }
$ErrorActionPreference = $prev

kubectl get pods -n enlight-staging -o wide

$ready = kubectl get pods -n enlight-staging -l app=fastapi -o jsonpath="{.items[0].status.containerStatuses[0].ready}" 2>$null
if ($ready -eq "true") {
    Write-Host "App deployed and READY." -ForegroundColor Green
} else {
    Write-Host "Rollout timed out but check pod above - if 1/1 Running, app is OK." -ForegroundColor Yellow
}
