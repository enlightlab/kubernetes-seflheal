# Deploy self-heal demo to Oracle OKE
# Prerequisites: kubectl connected to OKE, docker logged into bom.ocir.io
param(
    [string]$OcirUser = "bmitpaosivqx/kirti@enlightlab.com",
    [string]$OcirRegistry = "bom.ocir.io/bmitpaosivqx"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

Write-Host "=== 1. Verify cluster ===" -ForegroundColor Cyan
kubectl get nodes
if ($LASTEXITCODE -ne 0) { throw "kubectl not connected. Run kubeconfig command from OCI Console first." }

Write-Host "`n=== 2. Build & push images ===" -ForegroundColor Cyan
docker build -f "$Root\deploy\Dockerfile" -t "${OcirRegistry}/selfheal-ui:latest" $Root
docker push "${OcirRegistry}/selfheal-ui:latest"

docker build -f "D:\enlight-lab-platform\workload\fastapi\Dockerfile" `
    -t "${OcirRegistry}/enlight-fastapi:demo-pass" "D:\enlight-lab-platform\workload\fastapi"
docker push "${OcirRegistry}/enlight-fastapi:demo-pass"

Write-Host "`n=== 3. Deploy staging app ===" -ForegroundColor Cyan
kubectl apply -f "$Root\deploy\k8s\staging-app\"

Write-Host "`n=== 4. Install ArgoCD ===" -ForegroundColor Cyan
kubectl get ns argocd 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    kubectl create namespace argocd
    kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
    Write-Host "Waiting for ArgoCD..." -ForegroundColor Yellow
    kubectl -n argocd wait --for=condition=available deployment/argocd-server --timeout=300s
}

Write-Host "`n=== 5. Register ArgoCD app ===" -ForegroundColor Cyan
kubectl apply -f "$Root\deploy\k8s\argocd\fastapi-staging-app.yaml"

Write-Host "`n=== 6. Deploy self-heal UI ===" -ForegroundColor Cyan
kubectl apply -f "$Root\deploy\k8s\selfheal-ui.yaml"

Write-Host "`n=== 7. Wait for LoadBalancers ===" -ForegroundColor Cyan
Write-Host "Staging app:" -ForegroundColor Green
kubectl -n enlight-staging get svc fastapi -w
