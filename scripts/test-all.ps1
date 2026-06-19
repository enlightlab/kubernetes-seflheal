# Validate entire setup - run before 12pm demo
#Requires -Version 5.1
param([switch]$Quick)

$Root = Split-Path -Parent $PSScriptRoot
$Ctx = "kind-enlight-lab"
$pass = 0
$fail = 0

function Test-Step {
    param([string]$Name, [scriptblock]$Check)
    Write-Host -NoNewline "  $Name ... "
    try {
        $ok = & $Check
        if ($ok) { Write-Host "PASS" -ForegroundColor Green; $script:pass++ }
        else { Write-Host "FAIL" -ForegroundColor Red; $script:fail++ }
    } catch {
        Write-Host "FAIL" -ForegroundColor Red; $script:fail++
    }
}

Write-Host ""
Write-Host "Enlight Lab - Test All" -ForegroundColor Cyan
Write-Host ""

Test-Step "Docker running" {
    $p = $ErrorActionPreference; $ErrorActionPreference = "SilentlyContinue"
    docker info *> $null; $ok = ($LASTEXITCODE -eq 0); $ErrorActionPreference = $p; return $ok
}

Test-Step "Cluster API" {
    $p = $ErrorActionPreference; $ErrorActionPreference = "SilentlyContinue"
    kubectl config use-context $Ctx 2>&1 | Out-Null
    kubectl get nodes --request-timeout=15s 2>&1 | Out-Null
    $ok = ($LASTEXITCODE -eq 0); $ErrorActionPreference = $p; return $ok
}

if (-not $Quick) {
    Test-Step "Kyverno running" {
        $p = $ErrorActionPreference; $ErrorActionPreference = "SilentlyContinue"
        $r = kubectl get pods -n kyverno -l app.kubernetes.io/component=admission-controller --request-timeout=10s -o jsonpath="{.items[0].status.phase}" 2>$null
        $ErrorActionPreference = $p; return ($r -eq "Running")
    }

    Test-Step "ArgoCD running" {
        $p = $ErrorActionPreference; $ErrorActionPreference = "SilentlyContinue"
        $r = kubectl get pods -n argocd -l app.kubernetes.io/name=argocd-server --request-timeout=10s -o jsonpath="{.items[0].status.phase}" 2>$null
        $ErrorActionPreference = $p; return ($r -eq "Running")
    }
}

Test-Step "FastAPI pod ready" {
    $p = $ErrorActionPreference; $ErrorActionPreference = "SilentlyContinue"
    $r = kubectl get pods -n enlight-staging -l app=fastapi --request-timeout=10s -o jsonpath="{.items[0].status.containerStatuses[0].ready}" 2>$null
    $ErrorActionPreference = $p; return ($r -eq "true")
}

Test-Step "App /health endpoint" {
    try {
        $h = Invoke-RestMethod "http://localhost:30800/health" -TimeoutSec 5
        return ($h.status -eq "ok")
    } catch { return $false }
}

Test-Step "OPA policy BLOCK" {
    $p = $ErrorActionPreference; $ErrorActionPreference = "SilentlyContinue"
    & (Join-Path $Root "demos\demo2-chat-to-deploy\scripts\run-demo.ps1") -Variant non-compliant 2>&1 | Out-Null
    $ok = ($LASTEXITCODE -eq 0); $ErrorActionPreference = $p; return $ok
}

Test-Step "OPA policy PASS" {
    $p = $ErrorActionPreference; $ErrorActionPreference = "SilentlyContinue"
    & (Join-Path $Root "demos\demo2-chat-to-deploy\scripts\run-demo.ps1") -Variant compliant 2>&1 | Out-Null
    $ok = ($LASTEXITCODE -eq 0); $ErrorActionPreference = $p; return $ok
}

Write-Host ""
Write-Host "Results: $pass passed, $fail failed" -ForegroundColor $(if ($fail -eq 0) { "Green" } else { "Yellow" })
if ($fail -gt 0) {
    Write-Host "Fix: .\scripts\go-live.ps1" -ForegroundColor Yellow
}
Write-Host ""
exit $(if ($fail -eq 0) { 0 } else { 1 })
