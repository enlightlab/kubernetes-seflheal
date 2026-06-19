# Demo 2 - Chat-to-Deploy (policy gate works without AWS)
#Requires -Version 5.1
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("compliant", "non-compliant")]
    [string]$Variant
)

$ErrorActionPreference = "Stop"
$DemoRoot = Split-Path -Parent $PSScriptRoot
$PlatformRoot = Split-Path -Parent (Split-Path -Parent $DemoRoot)
$Overlay = Join-Path $DemoRoot "overlays\$Variant"
$Policy = Join-Path $PlatformRoot "foundation\policies\opa"
$OutFile = Join-Path $env:TEMP "enlight-$Variant.yaml"

Write-Host ""
Write-Host "=== Demo 2: Chat-to-Deploy ===" -ForegroundColor Cyan
Write-Host "Variant: $Variant" -ForegroundColor Yellow
Write-Host ""

if (-not (Get-Command kubectl -ErrorAction SilentlyContinue)) {
    throw "kubectl required for kustomize build"
}

kubectl kustomize $Overlay | Out-File -FilePath $OutFile -Encoding utf8
Write-Host "Manifest:" -ForegroundColor Gray
Get-Content $OutFile
Write-Host ""

function Test-Policy {
    param([string]$Path)
    $text = Get-Content $Path -Raw
    $violations = @()

    if ($text -match 'image:\s*([^\s]+)') {
        $img = $Matches[1].Trim()
        if ($img -match ':latest$') { $violations += "forbidden :latest tag ($img)" }
        $ok = ($img -match '^public\.ecr\.aws/') -or ($img -match '^\d+\.dkr\.ecr\.') `
            -or ($img -match '^172\.26\.48\.1:5000/') -or ($img -match '^enlight-fastapi:')
        if (-not $ok) { $violations += "unapproved registry ($img)" }
    }
    if ($text -notmatch 'limits:[\s\S]*cpu:') { $violations += "missing cpu limits" }
    if ($text -notmatch 'limits:[\s\S]*memory:') { $violations += "missing memory limits" }

    foreach ($v in $violations) { Write-Host "VIOLATION: $v" -ForegroundColor Red }
    return ($violations.Count -eq 0)
}

$pass = Test-Policy -Path $OutFile

Write-Host ""
if ($pass) {
    Write-Host "RESULT: PASS - ready for ArgoCD deploy" -ForegroundColor Green
    if ($Variant -eq "non-compliant") { exit 1 }
} else {
    Write-Host "RESULT: BLOCKED - pipeline stops here" -ForegroundColor Red
    if ($Variant -eq "compliant") { exit 1 }
}

Write-Host ""
Write-Host "Next: trigger .github/workflows/chat-to-deploy.yml via Claude GitHub MCP" -ForegroundColor Gray
exit 0
