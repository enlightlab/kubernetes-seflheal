# Install Kyverno FIRST, then ArgoCD, then monitoring (local-friendly order)
#Requires -Version 5.1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$PlatformRoot = Split-Path -Parent $Root
$Values = Join-Path $Root "helm\values"
$Ctx = "kind-enlight-lab"

foreach ($cmd in @("kubectl", "helm")) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { throw "$cmd not found" }
}

kubectl config use-context $Ctx 2>$null

Write-Host "Waiting for all nodes Ready..." -ForegroundColor Yellow
$prev = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
kubectl wait --for=condition=Ready nodes --all --timeout=180s 2>&1 | ForEach-Object { Write-Host $_ }
if ($LASTEXITCODE -ne 0) {
    $ErrorActionPreference = $prev
    throw "Nodes not Ready. Run: .\scripts\repair-cluster.ps1"
}
$ErrorActionPreference = $prev
kubectl get nodes

kubectl apply -f (Join-Path $Root "manifests\namespaces.yaml")

helm repo add argo https://argoproj.github.io/argo-helm 2>$null
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts 2>$null
helm repo add kyverno https://kyverno.github.io/kyverno/ 2>$null
helm repo update

# Kyverno FIRST - must be healthy before other installs
Write-Host "Kyverno (install first)..." -ForegroundColor Yellow
helm upgrade --install kyverno kyverno/kyverno -n kyverno --create-namespace `
    -f (Join-Path $Values "kyverno.yaml") --wait --timeout 10m

Write-Host "Waiting for Kyverno webhook..." -ForegroundColor Gray
Start-Sleep -Seconds 15
$prev = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
kubectl wait --for=condition=Available deployment -l app.kubernetes.io/component=admission-controller -n kyverno --timeout=180s 2>&1 | Out-Null
$ErrorActionPreference = $prev

Write-Host "ArgoCD..." -ForegroundColor Yellow
kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -
helm upgrade --install argocd argo/argo-cd -n argocd `
    -f (Join-Path $Values "argocd.yaml") --wait --timeout 10m

Write-Host "Prometheus + Grafana (optional, may be slow)..." -ForegroundColor Yellow
$prev = $ErrorActionPreference
$ErrorActionPreference = "Continue"
helm upgrade --install monitoring prometheus-community/kube-prometheus-stack -n monitoring --create-namespace `
    -f (Join-Path $Values "prometheus.yaml") --wait --timeout 8m 2>&1 | ForEach-Object { Write-Host $_ }
if ($LASTEXITCODE -ne 0) {
    Write-Host "Monitoring install slow/failed - app demo still works without Grafana." -ForegroundColor Yellow
}
$ErrorActionPreference = $prev

Write-Host "Kyverno policies..." -ForegroundColor Yellow
$policyDir = Join-Path $Root "policies\kyverno"
Get-ChildItem $policyDir -Filter "*.yaml" | Where-Object { $_.Name -notmatch "test-violation" } | ForEach-Object {
    kubectl apply -f $_.FullName
}

kubectl apply -f (Join-Path $PlatformRoot "gitops\argocd\projects\enlight-lab.yaml")

Write-Host ""
Write-Host "Platform installed." -ForegroundColor Green
