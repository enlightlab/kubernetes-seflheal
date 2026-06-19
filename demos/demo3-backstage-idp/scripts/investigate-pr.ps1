# Demo 3 - Simulated incident investigation via PR (fix probe path / add annotation)
#Requires -Version 5.1
param(
    [string]$ServiceName = "demo-api",
    [string]$Repo = "kirtiprasad2003/enlight-lab-platform",
    [string]$BaseBranch = "main"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
$DeployFile = Join-Path $Root "workload\scaffolded\$ServiceName\k8s\deployment.yaml"
$Branch = "idp/investigate-$ServiceName-$(Get-Date -Format 'HHmmss')"

if (-not (Test-Path $DeployFile)) {
    throw "Deployment not found. Merge scaffold PR first."
}

$mcpPath = "$env:USERPROFILE\.cursor\mcp.json"
$token = (Get-Content $mcpPath -Raw | ConvertFrom-Json).mcpServers.github.env.GITHUB_PERSONAL_ACCESS_TOKEN
$headers = @{
    Authorization = "Bearer $token"
    Accept        = "application/vnd.github+json"
    "X-GitHub-Api-Version" = "2022-11-28"
}

Write-Host ""
Write-Host "=== Demo 3: Incident investigation via PR ===" -ForegroundColor Cyan
Write-Host "Simulating: agent found misconfigured probe, opens fix PR" -ForegroundColor Yellow
Write-Host ""

Push-Location $Root
$prevErr = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
try {
    git fetch origin $BaseBranch 2>&1 | Out-Null
    git checkout -B $Branch "origin/$BaseBranch" 2>&1 | Out-Null

    $text = Get-Content $DeployFile -Raw
    if ($text -notmatch 'incident/investigated') {
        $text = $text -replace '(team:\s*enlight-demo)', "`$1`n        incident/investigated: `"$(Get-Date -Format o)`"`n        incident/root-cause: `"probe threshold tuned after AI analysis`""
    }
    if ($text -match 'initialDelaySeconds:\s*5') {
        $text = $text -replace 'initialDelaySeconds:\s*5', 'initialDelaySeconds: 10'
    }
    Set-Content $DeployFile $text -NoNewline
    git add $DeployFile
    git commit -m "Investigation: tune $ServiceName probes after incident analysis" 2>&1
    git push -u origin $Branch 2>&1
} finally {
    $ErrorActionPreference = $prevErr
    Pop-Location
}

$body = @{
    title = "Investigation: fix $ServiceName after incident"
    head  = $Branch
    base  = $BaseBranch
    body  = @"
## AI-assisted incident investigation

**Finding:** Liveness probe too aggressive during rollout (simulated).
**Fix:** Increase ``initialDelaySeconds``; document root cause on pod labels.
**Flow:** HolmesGPT/k8sgpt explains → agent opens this PR → merge → ArgoCD heals.

*Demo investigation PR.*
"@
} | ConvertTo-Json

$pr = Invoke-RestMethod -Method Post -Uri "https://api.github.com/repos/$Repo/pulls" -Headers $headers -Body $body -ContentType "application/json"

Write-Host "RESULT: Investigation PR created" -ForegroundColor Green
Write-Host "  PR #$($pr.number): $($pr.html_url)" -ForegroundColor Cyan
Write-Host "SAY: AI explains; fix ships as a reviewable PR, not a shell command" -ForegroundColor Gray
