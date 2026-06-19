# Build FastAPI image and load into kind
#Requires -Version 5.1
param(
    [switch]$SkipBuild,
    [switch]$SkipLoad,
    [switch]$Quick
)

$ErrorActionPreference = "Stop"
$PlatformRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$AppDir = Join-Path $PlatformRoot "workload\fastapi"
$Image = "enlight-fastapi:demo-pass"
$ClusterName = "enlight-lab"
$Ctx = "kind-$ClusterName"

if ($Quick) {
    $SkipBuild = $true
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    $ready = kubectl get pods -n enlight-staging -l app=fastapi --request-timeout=10s -o jsonpath="{.items[0].status.containerStatuses[0].ready}" 2>$null
    $ErrorActionPreference = $prev
    if ($ready -eq "true") {
        Write-Host "Quick mode: app already running - skipping build/load" -ForegroundColor Green
        return
    }
}

if (-not $SkipBuild) {
    Write-Host "Building $Image ..." -ForegroundColor Cyan
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    docker build -t $Image $AppDir 2>&1 | ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0) {
        $ErrorActionPreference = $prev
        throw "docker build failed. Is Docker Desktop running?"
    }
    $ErrorActionPreference = $prev
} else {
    Write-Host "Skipping docker build (image exists locally)" -ForegroundColor Gray
}

if ($SkipLoad) {
    Write-Host "Skipping kind load" -ForegroundColor Gray
    return
}

# Skip load if image already on kind node
$prev = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
$onNode = docker exec "${ClusterName}-control-plane" crictl images 2>$null | Select-String "enlight-fastapi"
$ErrorActionPreference = $prev
if ($onNode) {
    Write-Host "Image already in cluster - skipping kind load" -ForegroundColor Green
    return
}

Write-Host "Loading image into kind (1-3 min, do not Ctrl+C)..." -ForegroundColor Yellow

$job = Start-Job -ScriptBlock {
    param($Img, $Cluster)
    & kind load docker-image $Img --name $Cluster 2>&1
    exit $LASTEXITCODE
} -ArgumentList $Image, $ClusterName

$deadline = (Get-Date).AddMinutes(5)
while ($job.State -eq "Running") {
    if ((Get-Date) -gt $deadline) {
        Stop-Job $job -Force
        Remove-Job $job -Force
        Write-Host ""
        Write-Host "kind load timed out after 5 min - continuing anyway." -ForegroundColor Yellow
        kubectl config use-context $Ctx 2>$null
        return
    }
    Write-Host "." -NoNewline -ForegroundColor Gray
    Start-Sleep -Seconds 5
}

$output = Receive-Job $job -Wait
$code = $job.ChildJobs[0].JobStateInfo.Reason
Remove-Job $job -Force
Write-Host ""
if ($output) { $output | ForEach-Object { Write-Host $_ } }
Write-Host "Image load step done." -ForegroundColor Green

kubectl config use-context $Ctx 2>$null
