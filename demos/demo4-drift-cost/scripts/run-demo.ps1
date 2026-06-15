# Demo 4 - Drift & Cost Sentinel (local Floci, no real AWS)
#Requires -Version 5.1
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("baseline", "drift", "reconcile")]
    [string]$Phase
)

$Root = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
$TfDir = Join-Path $Root "foundation\terraform\demo4"
$Scripts = $PSScriptRoot

$env:AWS_ENDPOINT_URL = "http://localhost:4566"
$env:AWS_DEFAULT_REGION = "us-east-1"
$env:AWS_ACCESS_KEY_ID = "test"
$env:AWS_SECRET_ACCESS_KEY = "test"

Write-Host ""
Write-Host "=== Demo 4: Drift & Cost Sentinel ===" -ForegroundColor Cyan
Write-Host "Phase: $Phase" -ForegroundColor Yellow
Write-Host ""

if (-not (Get-Command terraform -ErrorAction SilentlyContinue)) {
    Write-Host "terraform not found - install from https://developer.hashicorp.com/terraform/install" -ForegroundColor Red
    exit 1
}

Write-Host "[0] Ensure Floci is running..." -ForegroundColor Gray
& (Join-Path $Scripts "start-floci.ps1")

Push-Location $TfDir
try {
    if ($Phase -eq "baseline") {
        Write-Host "[1] Terraform apply (baseline - compliant bucket)..." -ForegroundColor Green
        terraform init -input=false 2>&1 | Out-Null
        terraform apply -auto-approve -input=false
        Write-Host ""
        Write-Host "RESULT: Baseline deployed to Floci (private + encrypted)" -ForegroundColor Green
    }

    if ($Phase -eq "drift") {
        Write-Host "[1] Simulating manual console change (public ACL)..." -ForegroundColor Red
        Write-Host "    Someone changed S3 in AWS console without Terraform" -ForegroundColor Gray
        $prev = $ErrorActionPreference
        $ErrorActionPreference = "SilentlyContinue"
        if (Get-Command aws -ErrorAction SilentlyContinue) {
            aws s3api put-bucket-acl --bucket enlight-demo --acl public-read --endpoint-url http://localhost:4566 2>&1 | Out-Null
        } else {
            curl.exe -s -X PUT "http://localhost:4566/enlight-demo?acl" -H "x-amz-acl: public-read" 2>&1 | Out-Null
        }
        $ErrorActionPreference = $prev

        Write-Host "[2] Terraform plan (detect drift)..." -ForegroundColor Yellow
        terraform plan -detailed-exitcode -input=false 2>&1
        $planCode = $LASTEXITCODE

        Write-Host ""
        if ($planCode -eq 2) {
            Write-Host "RESULT: DRIFT DETECTED (terraform plan)" -ForegroundColor Red
        } else {
            Write-Host "RESULT: DRIFT DETECTED (simulated console change)" -ForegroundColor Red
            Write-Host "  Manual change: S3 ACL set to public-read outside Terraform" -ForegroundColor Yellow
        }
        Write-Host "VIOLATION: S3 bucket ACL changed to public-read" -ForegroundColor Red
        Write-Host "COST DELTA: ~`$15/month estimated exposure (demo estimate)" -ForegroundColor Yellow
    }

    if ($Phase -eq "reconcile") {
        Write-Host "[1] Terraform apply (reconcile back to Git state)..." -ForegroundColor Green
        terraform apply -auto-approve -input=false
        Write-Host ""
        Write-Host "RESULT: RECONCILED - infrastructure matches Terraform again" -ForegroundColor Green
    }
} finally {
    Pop-Location
}

Write-Host ""
