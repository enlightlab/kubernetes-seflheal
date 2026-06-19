# Restart ArgoCD port-forward only
#Requires -Version 5.1
Write-Host "ArgoCD -> http://localhost:8080" -ForegroundColor Cyan
Write-Host "Leave this window open. Ctrl+C to stop." -ForegroundColor Gray
kubectl port-forward -n argocd svc/argocd-server 8080:80
