# Demo 5 - Open a fresh GitHub PR that triggers pr-compliance.yml
#Requires -Version 5.1
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("non-compliant", "compliant")]
    [string]$Variant,
    [string]$Repo = "kirtiprasad2003/enlight-lab-platform",
    [string]$BaseBranch = "main"
)

$ErrorActionPreference = "Stop"
$DemoRoot = Split-Path -Parent $PSScriptRoot
$Root = Split-Path -Parent (Split-Path -Parent $DemoRoot)
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$Branch = "demo5/$Variant-$Stamp"
$RunDir = Join-Path $DemoRoot "pr-runs\$Stamp"
$Samples = Join-Path $DemoRoot "samples"

$mcpPath = "$env:USERPROFILE\.cursor\mcp.json"
if (-not (Test-Path $mcpPath)) { throw "GitHub token not found in mcp.json" }
$token = (Get-Content $mcpPath -Raw | ConvertFrom-Json).mcpServers.github.env.GITHUB_PERSONAL_ACCESS_TOKEN
$headers = @{
    Authorization          = "Bearer $token"
    Accept                 = "application/vnd.github+json"
    "X-GitHub-Api-Version" = "2022-11-28"
}

$pySrc = if ($Variant -eq "compliant") { "good-config.py" } else { "bad-config.py" }
$tfSrc = if ($Variant -eq "compliant") { "good-s3.tf" } else { "bad-s3.tf" }

Write-Host ""
Write-Host "=== Demo 5: Fresh compliance PR ($Variant) ===" -ForegroundColor Cyan
Write-Host "Branch: $Branch" -ForegroundColor Gray
Write-Host ""

New-Item -ItemType Directory -Path $RunDir -Force | Out-Null
Copy-Item (Join-Path $Samples $pySrc) (Join-Path $RunDir "service-config.py") -Force
Copy-Item (Join-Path $Samples $tfSrc) (Join-Path $RunDir "storage.tf") -Force

$relRun = "demos/demo5-pr-compliance/pr-runs/$Stamp"
$readme = @"
# Demo 5 PR run $Stamp

Variant: **$Variant**

Files checked by ``pr-compliance.yml``:
- ``$relRun/service-config.py`` (secret scan)
- ``$relRun/storage.tf`` (S3 policy)

*Auto-generated for client demo - do not merge.*
"@
Set-Content -Path (Join-Path $RunDir "README.md") -Value $readme -Encoding UTF8

Push-Location $Root
$prevErr = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
try {
    git fetch origin $BaseBranch 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "git fetch failed" }

    git checkout -B $Branch "origin/$BaseBranch" 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "git checkout failed" }

    git add $relRun
    git commit -m "Demo 5: $Variant compliance check ($Stamp)" 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Nothing to commit or commit failed" }

    git push -u origin $Branch 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "git push failed" }
} finally {
    $ErrorActionPreference = $prevErr
    Pop-Location
}

$title = if ($Variant -eq "compliant") {
    "Demo 5: compliant config ($Stamp)"
} else {
    "Demo 5: NON-COMPLIANT config ($Stamp)"
}
$expectedCi = if ($Variant -eq "compliant") {
    "PASS - merge allowed"
} else {
    "FAIL - secrets / S3 policy violation"
}
$prBody = @"
## PR compliance bot - live demo

**Variant:** ``$Variant``  
**Run id:** ``$Stamp``

Expected CI:
- $expectedCi

*Demo PR - close after presentation.*
"@
$body = @{
    title = $title
    head  = $Branch
    base  = $BaseBranch
    body  = $prBody
} | ConvertTo-Json

$pr = Invoke-RestMethod -Method Post -Uri "https://api.github.com/repos/$Repo/pulls" -Headers $headers -Body $body -ContentType "application/json"

Write-Host "RESULT: PR #$($pr.number) created" -ForegroundColor Green
Write-Host "  $($pr.html_url)" -ForegroundColor Cyan
Write-Host "  Checks: $($pr.html_url)/checks" -ForegroundColor Gray
Write-Host ""

[PSCustomObject]@{
    number    = $pr.number
    url       = $pr.html_url
    checksUrl = "$($pr.html_url)/checks"
    branch    = $Branch
    variant   = $Variant
    stamp     = $Stamp
}
