# Provision EKS (run OFF demo stage - takes 15-20 min)
#Requires -Version 5.1
$ErrorActionPreference = "Stop"
$TfDir = Join-Path (Split-Path -Parent $PSScriptRoot) "terraform\eks"

Write-Host "Enlight Lab - provision EKS" -ForegroundColor Cyan
Write-Host "This takes 15-20 minutes. Do not run live during a demo." -ForegroundColor Yellow
Write-Host ""

Set-Location $TfDir
terraform init
terraform apply

Write-Host ""
terraform output configure_kubectl
terraform output ecr_repository_url
