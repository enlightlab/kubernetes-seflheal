# Track the latest IDP-scaffolded service for PR / deploy steps
#Requires -Version 5.1

function Get-IdpRoot {
    Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
}

function Get-IdpLastServiceFile {
    Join-Path (Get-IdpRoot) "workload\scaffolded\.last-idp-service"
}

function Get-IdpLastBranchFile {
    Join-Path (Get-IdpRoot) "workload\scaffolded\.last-idp-branch"
}

function Get-IdpLastService {
    $f = Get-IdpLastServiceFile
    if (Test-Path $f) {
        return (Get-Content $f -Raw).Trim()
    }
    return $null
}

function Set-IdpLastService {
    param([string]$ServiceName)
    Set-Content -Path (Get-IdpLastServiceFile) -Value $ServiceName -Encoding UTF8 -NoNewline
}

function Get-IdpLastBranch {
    $f = Get-IdpLastBranchFile
    if (Test-Path $f) {
        return (Get-Content $f -Raw).Trim()
    }
    return $null
}

function Set-IdpLastBranch {
    param([string]$Branch)
    Set-Content -Path (Get-IdpLastBranchFile) -Value $Branch -Encoding UTF8 -NoNewline
}

function Test-IdpPathOnMain {
    param([string]$ServiceName)
    $Root = Get-IdpRoot
    $rel = "workload/scaffolded/$ServiceName/k8s"
    Push-Location $Root
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    try {
        git fetch origin main 2>&1 | Out-Null
        git rev-parse "origin/main:$rel" 2>&1 | Out-Null
        return ($LASTEXITCODE -eq 0)
    } finally {
        $ErrorActionPreference = $prev
        Pop-Location
    }
}

function Resolve-IdpServiceName {
    param(
        [string]$ServiceName,
        [string]$Phase
    )
    if ($Phase -eq "scaffold") {
        return "svc-" + (Get-Date -Format "yyyyMMddHHmmss")
    }
    if ($ServiceName -in @("auto", "", "demo-api")) {
        $last = Get-IdpLastService
        if (-not $last) {
            throw "No scaffolded service yet. Click Scaffold first."
        }
        return $last
    }
    return $ServiceName
}
