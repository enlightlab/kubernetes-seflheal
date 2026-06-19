# Connect GitHub repo to ArgoCD and deploy fastapi-staging app
#Requires -Version 5.1
$Root = Split-Path -Parent $PSScriptRoot
$Repo = "https://github.com/kirtiprasad2003/enlight-lab-platform.git"
$Ctx = "kind-enlight-lab"

Write-Host ""
Write-Host "ArgoCD GitOps setup" -ForegroundColor Cyan
Write-Host ""

kubectl config use-context $Ctx 2>$null

Write-Host "[1/2] Register repo in ArgoCD (public repo = no password needed)..." -ForegroundColor Yellow
$repoYaml = @"
apiVersion: v1
kind: Secret
metadata:
  name: repo-enlight-lab-platform
  namespace: argocd
  labels:
    argocd.argoproj.io/secret-type: repository
stringData:
  type: git
  url: $Repo
"@
$repoYaml | kubectl apply -f -

Write-Host "[2/2] Deploy ArgoCD Application (fastapi-staging, selfHeal on)..." -ForegroundColor Yellow
kubectl apply -f (Join-Path $Root "gitops\argocd\applications\fastapi-staging.yaml")

Start-Sleep -Seconds 5
kubectl get application fastapi-staging -n argocd 2>&1

Write-Host ""
Write-Host "Open ArgoCD: http://localhost:8082" -ForegroundColor Cyan
Write-Host "You should see app: fastapi-staging (Synced / Healthy)" -ForegroundColor Green
Write-Host ""
Write-Host "If repo is PRIVATE, use UI instead:" -ForegroundColor Yellow
Write-Host "  Settings -> Repositories -> Connect Repo" -ForegroundColor Gray
Write-Host "  URL: $Repo" -ForegroundColor Gray
Write-Host "  Username: kirtiprasad2003" -ForegroundColor Gray
Write-Host "  Password: GitHub Personal Access Token (repo scope)" -ForegroundColor Gray
