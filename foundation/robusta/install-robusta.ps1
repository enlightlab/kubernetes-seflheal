# Install Robusta agent on kind-enlight-lab (from Robusta UI wizard files)
#Requires -Version 5.1
$ErrorActionPreference = "Stop"
$Dir = $PSScriptRoot
$Ctx = "kind-enlight-lab"
$Ns = "robusta"

$secrets = Join-Path $Dir "robusta-secrets.yaml"
$values = Join-Path $Dir "generated_values.yaml"

if (-not (Test-Path $secrets)) {
    Write-Host "MISSING: $secrets" -ForegroundColor Red
    Write-Host "Download from Robusta UI step 1 and save here." -ForegroundColor Yellow
    exit 1
}
if (-not (Test-Path $values)) {
    Write-Host "MISSING: $values" -ForegroundColor Red
    Write-Host "Download from Robusta UI step 2 and save here." -ForegroundColor Yellow
    exit 1
}

Write-Host "Installing Robusta on $Ctx ..." -ForegroundColor Cyan
kubectl config use-context $Ctx

Write-Host "[1/4] Namespace $Ns" -ForegroundColor Gray
kubectl create namespace $Ns --dry-run=client -o yaml | kubectl apply -f -

Write-Host "[2/4] Apply secrets" -ForegroundColor Gray
# Fix trailing space in namespace from Robusta UI download
$sec = Get-Content $secrets -Raw
$sec = $sec -replace "namespace:\s*['\`"]?robusta\s+['\`"]?", "namespace: robusta"
Set-Content -Path $secrets -Value $sec -NoNewline
kubectl apply -f $secrets -n $Ns

Write-Host "[3/4] Helm repo" -ForegroundColor Gray
helm repo add robusta https://robusta-charts.storage.googleapis.com 2>$null
helm repo update

Write-Host "[4/4] Helm install (2-5 min)..." -ForegroundColor Yellow
helm upgrade --install robusta robusta/robusta `
    -f $values `
    -n $Ns `
    --set clusterName=enlight-lab-kind `
    --set isSmallCluster=true `
    --set holmes.resources.requests.memory=512Mi `
    --wait --timeout 10m

Write-Host ""
kubectl get pods -n $Ns
Write-Host ""
Write-Host "Done. Check Robusta UI: platform.robusta.dev" -ForegroundColor Green
Write-Host "Next: complete Verify step in browser, then HolmesGPT onboarding." -ForegroundColor Cyan
