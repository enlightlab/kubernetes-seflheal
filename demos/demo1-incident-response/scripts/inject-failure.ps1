# Demo 1 - inject scripted failure (bad image -> ImagePullBackOff)
#Requires -Version 5.1
$Ctx = "kind-enlight-lab"

Write-Host "Demo 1 - injecting failure (bad image tag)..." -ForegroundColor Red
kubectl config use-context $Ctx 2>$null

kubectl set image deployment/fastapi api=enlight-fastapi:DOES-NOT-EXIST -n enlight-staging

Write-Host "Waiting for failure (30 sec)..." -ForegroundColor Yellow
Start-Sleep -Seconds 15

kubectl get pods -n enlight-staging
Write-Host ""
Write-Host "Expected: ImagePullBackOff or ErrImagePull (this is the incident)" -ForegroundColor Yellow
Write-Host "SAY: App is broken on purpose. AI would explain why here." -ForegroundColor Gray
Write-Host "Next: run heal-rollback.ps1 to recover" -ForegroundColor Green
