# Create local kind cluster ($0 cloud)
#Requires -Version 5.1
param(
    [switch]$ForceRecreate
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Config = Join-Path $Root "cluster\kind-config.yaml"
$ClusterName = "enlight-lab"
$Ctx = "kind-$ClusterName"

function Get-KindClusters {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    $raw = kind get clusters 2>&1
    $ErrorActionPreference = $prev
    if ($LASTEXITCODE -ne 0) { throw "kind get clusters failed: $raw" }
    return ($raw | Out-String).Trim() -split "[\r\n]+" | ForEach-Object { $_.Trim() } | Where-Object { $_ }
}

function Remove-KindCluster {
    param([string]$Name)
    Write-Host "Deleting cluster $Name ..." -ForegroundColor Yellow
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    kind delete cluster --name $Name 2>&1 | ForEach-Object { Write-Host $_ }
    $ErrorActionPreference = $prev
    Start-Sleep -Seconds 3
}

function New-KindCluster {
    Write-Host "Creating cluster $ClusterName - about 2 minutes..." -ForegroundColor Yellow
    Write-Host "Config: $Config" -ForegroundColor Gray
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    kind create cluster --name $ClusterName --config $Config 2>&1 | ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0) {
        $ErrorActionPreference = $prev
        throw "kind create cluster failed (exit $LASTEXITCODE)"
    }
    $ErrorActionPreference = $prev
}

function Test-KubeContext {
    param([string]$Name)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    kubectl config use-context $Name 2>&1 | Out-Null
    $ok = ($LASTEXITCODE -eq 0)
    if ($ok) {
        kubectl get nodes --request-timeout=10s 2>&1 | Out-Null
        $ok = ($LASTEXITCODE -eq 0)
    }
    $ErrorActionPreference = $prev
    return $ok
}

Write-Host "Enlight Lab - create kind cluster" -ForegroundColor Cyan

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker not found. Install Docker Desktop."
}

$prev = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    $ErrorActionPreference = $prev
    throw "Docker Desktop is not running. Start Docker, wait for Engine running, then retry."
}
$ErrorActionPreference = $prev
Write-Host "Docker OK" -ForegroundColor Green

if (-not (Get-Command kind -ErrorAction SilentlyContinue)) {
    throw "kind not found. Install: choco install kind"
}

if (-not (Test-Path $Config)) {
    throw "Config not found: $Config"
}

$clusters = Get-KindClusters
$exists = $clusters -contains $ClusterName

if ($ForceRecreate -and $exists) {
    Remove-KindCluster -Name $ClusterName
    $exists = $false
}

if ($exists) {
    if (Test-KubeContext -Name $Ctx) {
        Write-Host "Cluster $ClusterName already exists and is healthy - skipping create." -ForegroundColor Yellow
    } else {
        Write-Host "Cluster $ClusterName exists but kubeconfig is broken - recreating..." -ForegroundColor Yellow
        Remove-KindCluster -Name $ClusterName
        $clusters = Get-KindClusters
        if ($clusters -contains $ClusterName) {
            throw "Failed to delete cluster $ClusterName. Run: kind delete cluster --name $ClusterName"
        }
        New-KindCluster
    }
} else {
    New-KindCluster
}

if (-not (Test-KubeContext -Name $Ctx)) {
    throw "Context '$Ctx' not available after create. Run repair-cluster.ps1"
}

kubectl get nodes
Write-Host "Cluster ready: $Ctx" -ForegroundColor Green
