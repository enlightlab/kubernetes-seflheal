# Demo 3 - Backstage IDP golden path (full PR-driven flow)
# Phases: scaffold | pr | deploy | runbook | investigate | full
#Requires -Version 5.1
param(
    [string]$ServiceName = "auto",
    [ValidateSet("scaffold", "pr", "deploy", "runbook", "investigate", "full")]
    [string]$Phase = "scaffold",
    [ValidateSet("scale-up", "scale-down", "rotate")]
    [string]$RunbookAction = "scale-up"
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "idp-state.ps1")

$DemoRoot = Split-Path -Parent $PSScriptRoot
$Root = Get-IdpRoot
$TemplateDir = Join-Path $DemoRoot "templates\golden-path"
$Scripts = $PSScriptRoot

$ServiceName = Resolve-IdpServiceName -ServiceName $ServiceName -Phase $Phase
$OutDir = Join-Path $Root "workload\scaffolded\$ServiceName"

function Invoke-Scaffold {
    Write-Host ""
    Write-Host "=== Demo 3: IDP Golden Path - Scaffold ===" -ForegroundColor Cyan
    Write-Host "Service: $ServiceName" -ForegroundColor Yellow
    Write-Host "SERVICE_NAME: $ServiceName" -ForegroundColor Gray
    Write-Host ""

    if (Test-Path $OutDir) {
        Write-Host "Refreshing scaffold folder..." -ForegroundColor Gray
        Remove-Item $OutDir -Recurse -Force
    }

    Get-ChildItem $TemplateDir -Recurse -File | ForEach-Object {
        $rel = $_.FullName.Substring($TemplateDir.Length + 1)
        $dest = Join-Path $OutDir $rel
        $destDir = Split-Path $dest -Parent
        if (-not (Test-Path $destDir)) { New-Item -ItemType Directory -Path $destDir -Force | Out-Null }
        $content = Get-Content $_.FullName -Raw
        $safeTf = $ServiceName -replace '-', '_'
        $content = $content -replace '\{\{SERVICE_NAME\}\}', $ServiceName
        $content = $content -replace '\{\{SERVICE_NAME_TF\}\}', $safeTf
        Set-Content -Path $dest -Value $content -NoNewline
    }

    $ciSrc = Join-Path $OutDir "ci\service-ci.yaml"
    $ciNamed = Join-Path $OutDir "ci\$ServiceName-ci.yml"
    if (Test-Path $ciSrc) { Move-Item $ciSrc $ciNamed -Force }

    $argoSrc = Join-Path $OutDir "gitops\argocd-application.yaml"
    $argoDest = Join-Path $Root "gitops\argocd\applications\$ServiceName.yaml"
    if (Test-Path $argoSrc) {
        New-Item -ItemType Directory -Path (Split-Path $argoDest -Parent) -Force | Out-Null
        Copy-Item $argoSrc $argoDest -Force
    }

    Write-Host "[1/4] Scaffolded bundle:" -ForegroundColor Green
    Write-Host "  catalog-info.yaml" -ForegroundColor Gray
    Write-Host "  k8s/ deployment, service, secret, servicemonitor, alerts, grafana dashboard" -ForegroundColor Gray
    Write-Host "  terraform/main.tf" -ForegroundColor Gray
    Write-Host "  ci/$ServiceName-ci.yml" -ForegroundColor Gray
    Write-Host "  gitops -> gitops/argocd/applications/$ServiceName.yaml" -ForegroundColor Gray

    Write-Host "[2/4] Policy check..." -ForegroundColor Yellow
    $deployFile = Join-Path $OutDir "k8s\deployment.yaml"
    $text = Get-Content $deployFile -Raw
    $ok = ($text -match 'limits:') -and ($text -match 'cpu:') -and ($text -match 'memory:')
    if ($ok) { Write-Host "  Policy: PASS" -ForegroundColor Green } else { Write-Host "  Policy: FAIL" -ForegroundColor Red; exit 1 }

    Write-Host "[3/4] Observability pre-wired:" -ForegroundColor Green
    Write-Host "  ServiceMonitor, PrometheusRule, Grafana ConfigMap" -ForegroundColor Gray
    Write-Host "[4/4] Output: $OutDir" -ForegroundColor Cyan

    Set-IdpLastService $ServiceName
    Write-Host ""
    Write-Host "RESULT: New service $ServiceName scaffolded" -ForegroundColor Green
    Write-Host "Next: Create PR then Deploy (or merge PR first for full GitOps)" -ForegroundColor Yellow
}

function Invoke-Deploy {
    Write-Host ""
    Write-Host "=== Demo 3: ArgoCD Deploy ===" -ForegroundColor Cyan
    Write-Host "Service: $ServiceName" -ForegroundColor Yellow
    Write-Host "SERVICE_NAME: $ServiceName" -ForegroundColor Gray
    $argoApp = Join-Path $Root "gitops\argocd\applications\$ServiceName.yaml"
    if (-not (Test-Path $argoApp)) {
        Write-Host "ArgoCD app missing - run scaffold first" -ForegroundColor Red
        exit 1
    }

    $onMain = Test-IdpPathOnMain -ServiceName $ServiceName
    $branch = Get-IdpLastBranch
    $revision = if ($onMain) { "main" } elseif ($branch) { $branch } else { "main" }

    Write-Host "Git revision for ArgoCD: $revision" -ForegroundColor Gray
    if (-not $onMain -and $branch) {
        Write-Host "  (PR branch - path not on main yet; using branch until you merge)" -ForegroundColor Gray
    }

    kubectl config use-context kind-enlight-lab 2>$null
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"

    kubectl apply -k (Join-Path $OutDir "k8s") 2>&1 | Out-Null
    Write-Host "Applied K8s manifests locally (pod can start before merge)" -ForegroundColor Gray

    $argoYaml = Get-Content $argoApp -Raw
    $argoYaml = $argoYaml -replace 'targetRevision:\s*main', "targetRevision: $revision"
    $argoTmp = Join-Path $env:TEMP "argocd-$ServiceName.yaml"
    Set-Content -Path $argoTmp -Value $argoYaml -Encoding UTF8
    kubectl apply -f $argoTmp 2>&1 | Out-Null

    Start-Sleep 8
    kubectl patch application $ServiceName -n argocd --type merge -p "{`"spec`":{`"source`":{`"targetRevision`":`"$revision`"}}}" 2>&1 | Out-Null
    $argoStatus = kubectl get application $ServiceName -n argocd -o jsonpath="{.status.sync.status}/{.status.health.status}" 2>$null
    kubectl get application $ServiceName -n argocd -o jsonpath="sync={.status.sync.status} health={.status.health.status}`n" 2>$null
    kubectl get pods -n enlight-staging -l "app=$ServiceName" 2>$null
    $ErrorActionPreference = $prev
    Write-Host ""
    if ($argoStatus -eq "Synced/Healthy") {
        Write-Host "RESULT: $ServiceName live via GitOps (Synced/Healthy)" -ForegroundColor Green
    } elseif ($revision -ne "main") {
        Write-Host "RESULT: $ServiceName syncing from PR branch $revision" -ForegroundColor Green
        Write-Host "  Merge PR on GitHub, then Deploy again to pin to main" -ForegroundColor Gray
    } else {
        Write-Host "RESULT: $ServiceName registered - create PR and merge, then Deploy again" -ForegroundColor Yellow
    }
    Write-Host "Open: http://localhost:8082/applications/argocd/$ServiceName" -ForegroundColor Cyan
}

switch ($Phase) {
    "scaffold" { Invoke-Scaffold }
    "pr" {
        if (-not (Test-Path $OutDir)) { throw "Scaffold not found for $ServiceName. Run Scaffold first." }
        & (Join-Path $Scripts "create-pr.ps1") -ServiceName $ServiceName
    }
    "deploy" { Invoke-Deploy }
    "runbook" { & (Join-Path $Scripts "runbook-pr.ps1") -ServiceName $ServiceName -Action $RunbookAction }
    "investigate" { & (Join-Path $Scripts "investigate-pr.ps1") -ServiceName $ServiceName }
    "full" {
        Invoke-Scaffold
        $pr = & (Join-Path $Scripts "create-pr.ps1") -ServiceName $ServiceName
        Write-Host ""
        Write-Host "FULL FLOW - merge PR #$($pr.number) in GitHub, then Deploy" -ForegroundColor Yellow
        Write-Host "SHOW: http://localhost:30800/idp" -ForegroundColor Cyan
        Write-Host "SHOW: $($pr.html_url)" -ForegroundColor Cyan
    }
}
