# Demo 1 - rollback to healthy version (simulates ArgoCD self-heal)
#Requires -Version 5.1
$Root = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
$Ctx = "kind-enlight-lab"
$Ns = "enlight-staging"
$GoodImage = "enlight-fastapi:demo-pass"

Write-Host "Demo 1 - rollback to healthy deployment..." -ForegroundColor Green
kubectl config use-context $Ctx 2>$null

# Clean up stuck Kyverno demo pod from PART 4 (optional, frees cluster noise)
kubectl delete pod policy-violation-demo -n $Ns --ignore-not-found 2>&1 | Out-Null

Write-Host "Restoring image: $GoodImage" -ForegroundColor Cyan
kubectl set image deployment/fastapi api=$GoodImage -n $Ns
kubectl apply -k (Join-Path $Root "demos\demo2-chat-to-deploy\overlays\local") 2>&1 | Out-Null
kubectl rollout restart deployment/fastapi -n $Ns 2>&1 | Out-Null

Write-Host "Waiting for pod to recover (up to 60 sec)..." -ForegroundColor Yellow
$deadline = (Get-Date).AddSeconds(60)
$recovered = $false
while ((Get-Date) -lt $deadline) {
    $ready = kubectl get pods -n $Ns -l app=fastapi --field-selector=status.phase=Running -o jsonpath="{.items[0].status.containerStatuses[0].ready}" 2>$null
    if ($ready -eq "true") {
        $recovered = $true
        break
    }
    Start-Sleep -Seconds 3
}

kubectl get pods -n $Ns
Write-Host ""

if (-not $recovered) {
    Write-Host "Pod still broken - loading image into cluster (Docker Desktop must be running)..." -ForegroundColor Yellow
    $dockerOk = $false
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    docker info 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { $dockerOk = $true }
    $ErrorActionPreference = $prev

    if ($dockerOk) {
        & (Join-Path $Root "foundation\scripts\01-build-image.ps1")
        kubectl rollout restart deployment/fastapi -n $Ns 2>&1 | Out-Null
        Start-Sleep -Seconds 25
        kubectl get pods -n $Ns
        $ready = kubectl get pods -n $Ns -l app=fastapi -o jsonpath="{.items[0].status.containerStatuses[0].ready}" 2>$null
        if ($ready -eq "true") { $recovered = $true }
    } else {
        Write-Host ""
        Write-Host "Docker Desktop is NOT running. Do this:" -ForegroundColor Red
        Write-Host "  1. Start Docker Desktop (wait until green)" -ForegroundColor White
        Write-Host "  2. .\scripts\go-live.bat" -ForegroundColor White
        Write-Host "  3. Run this script again" -ForegroundColor White
    }
}

if ($recovered) {
    try {
        $h = Invoke-RestMethod "http://localhost:30800/health" -TimeoutSec 5
        Write-Host "App recovered: $($h | ConvertTo-Json -Compress)" -ForegroundColor Green
        Write-Host 'SAY: System rolled back to last good version. AI explained, platform fixed.' -ForegroundColor Gray
    } catch {
        Write-Host "/health not reachable - run: .\scripts\port-forward-all.ps1" -ForegroundColor Yellow
        Write-Host "Then open: http://localhost:30800/health" -ForegroundColor Cyan
    }
} else {
    Write-Host "Rollback not complete yet - follow Docker steps above." -ForegroundColor Red
}
