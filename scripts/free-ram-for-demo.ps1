# Stop extra kind clusters so ArgoCD + Grafana can run (manager demo)
#Requires -Version 5.1
Write-Host ""
Write-Host "Freeing RAM for Enlight Lab demo..." -ForegroundColor Cyan
Write-Host "Deletes cost-poc + test-cluster only. Keeps enlight-lab." -ForegroundColor Yellow
Write-Host "This can take 5-10 minutes. You will see progress below." -ForegroundColor Yellow
Write-Host ""

$toDelete = @("cost-poc", "test-cluster")

Write-Host "[1/3] Listing clusters..." -ForegroundColor Gray
$existing = @()
$prev = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
$all = kind get clusters 2>&1
$ErrorActionPreference = $prev
if ($all) { $existing = $all | ForEach-Object { $_.ToString().Trim() } }
Write-Host "  Found: $($existing -join ', ')" -ForegroundColor Gray

Write-Host "[2/3] Deleting extra clusters (slow - wait)..." -ForegroundColor Yellow
foreach ($c in $toDelete) {
    if ($existing -contains $c) {
        Write-Host "  Deleting $c ... (2-5 min, do not Ctrl+C)" -ForegroundColor Cyan
        kind delete cluster --name $c
        Write-Host "  $c deleted." -ForegroundColor Green
    } else {
        Write-Host "  $c not found - skip." -ForegroundColor Gray
    }
}

Write-Host "[3/3] Restarting enlight-lab node..." -ForegroundColor Yellow
docker restart enlight-lab-control-plane
Write-Host "  Waiting 60 sec for node Ready..." -ForegroundColor Gray
Start-Sleep -Seconds 60

kubectl config use-context kind-enlight-lab 2>$null
kubectl get nodes --request-timeout=30s

Write-Host ""
Write-Host "Done. Next run:" -ForegroundColor Green
Write-Host "  .\fix-dashboards.bat" -ForegroundColor Cyan
