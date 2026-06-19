# Tear down local cluster
#Requires -Version 5.1
Write-Host "Deleting kind cluster enlight-lab..." -ForegroundColor Yellow
kind delete cluster --name enlight-lab 2>$null
Write-Host "Done. For real EKS: cd foundation\terraform\eks && terraform destroy" -ForegroundColor Gray
