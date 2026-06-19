# Fast check: cluster + pods + port-forwards
#Requires -Version 5.1
$Ctx = "kind-enlight-lab"

Write-Host "Enlight Lab - URL check" -ForegroundColor Cyan
Write-Host ""

kubectl config use-context $Ctx 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[FAIL] kubectl context missing" -ForegroundColor Red
    exit 1
}

$prev = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
kubectl get nodes --request-timeout=10s 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[FAIL] Cluster API timeout - run repair-cluster.ps1" -ForegroundColor Red
    exit 1
}
$ErrorActionPreference = $prev
Write-Host "[OK] Cluster reachable" -ForegroundColor Green

Write-Host ""
Write-Host "Pods:" -ForegroundColor Yellow
kubectl get pods -n enlight-staging 2>$null
kubectl get pods -n argocd -l app.kubernetes.io/name=argocd-server 2>$null
kubectl get pods -n monitoring -l app.kubernetes.io/name=grafana 2>$null

Write-Host ""
Write-Host "URLs (need port-forward-all.ps1 running):" -ForegroundColor Yellow

foreach ($u in @(
    @{ Name = "App"; Url = "http://localhost:30800/health" },
    @{ Name = "ArgoCD"; Url = "http://localhost:8080" },
    @{ Name = "Grafana"; Url = "http://localhost:3000" }
)) {
    try {
        $r = Invoke-WebRequest -Uri $u.Url -TimeoutSec 3 -UseBasicParsing -ErrorAction Stop
        Write-Host "[OK] $($u.Name) $($u.Url) ($($r.StatusCode))" -ForegroundColor Green
    } catch {
        Write-Host "[FAIL] $($u.Name) $($u.Url) - run .\scripts\port-forward-all.ps1" -ForegroundColor Red
    }
}
