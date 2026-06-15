# Demo 3 - Runbook action via PR (scale service)
#Requires -Version 5.1
param(
    [string]$ServiceName = "demo-api",
    [ValidateSet("scale-up", "scale-down", "rotate")]
    [string]$Action = "scale-up",
    [string]$Repo = "kirtiprasad2003/enlight-lab-platform",
    [string]$BaseBranch = "main"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
$DeployFile = Join-Path $Root "workload\scaffolded\$ServiceName\k8s\deployment.yaml"
$Branch = "idp/runbook-$Action-$ServiceName-$(Get-Date -Format 'HHmmss')"

if (-not (Test-Path $DeployFile)) {
    throw "Deployment not found. Merge scaffold PR first or run scaffold + create-pr."
}

$replicas = switch ($Action) {
    "scale-up"   { 2 }
    "scale-down" { 1 }
    "rotate"     { 1 }
}

$mcpPath = "$env:USERPROFILE\.cursor\mcp.json"
$token = (Get-Content $mcpPath -Raw | ConvertFrom-Json).mcpServers.github.env.GITHUB_PERSONAL_ACCESS_TOKEN
$headers = @{
    Authorization = "Bearer $token"
    Accept        = "application/vnd.github+json"
    "X-GitHub-Api-Version" = "2022-11-28"
}

Write-Host ""
Write-Host "=== Demo 3: Runbook via PR ===" -ForegroundColor Cyan
Write-Host "Action: $Action -> replicas=$replicas" -ForegroundColor Yellow
Write-Host ""

Push-Location $Root
$prevErr = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
try {
    git fetch origin $BaseBranch 2>&1 | Out-Null
    git checkout -B $Branch "origin/$BaseBranch" 2>&1 | Out-Null

    $text = Get-Content $DeployFile -Raw
    if ($text -match 'replicas:\s*\d+') {
        $text = $text -replace 'replicas:\s*\d+', "replicas: $replicas"
    }
    if ($Action -eq "rotate") {
        $text = $text -replace 'team:\s*enlight-demo', "team: enlight-demo`n        runbook/rotated: `"$(Get-Date -Format o)`""
    }
    Set-Content $DeployFile $text -NoNewline
    git add $DeployFile
    git commit -m "Runbook: $Action $ServiceName (replicas=$replicas)" 2>&1
    git push -u origin $Branch 2>&1
} finally {
    $ErrorActionPreference = $prevErr
    Pop-Location
}

$body = @{
    title = "Runbook: $Action $ServiceName"
    head  = $Branch
    base  = $BaseBranch
    body  = "Agent runbook action ``$Action`` for ``$ServiceName``. Replicas -> $replicas. Merge triggers ArgoCD sync."
} | ConvertTo-Json

$pr = Invoke-RestMethod -Method Post -Uri "https://api.github.com/repos/$Repo/pulls" -Headers $headers -Body $body -ContentType "application/json"

Write-Host "RESULT: Runbook PR created" -ForegroundColor Green
Write-Host "  PR #$($pr.number): $($pr.html_url)" -ForegroundColor Cyan
Write-Host "Operations runbooks go through PRs - no manual kubectl in production" -ForegroundColor Gray
