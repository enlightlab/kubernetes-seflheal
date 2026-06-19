# Force-remove stuck enlight-lab Docker containers
#Requires -Version 5.1
$ClusterName = "enlight-lab"

Write-Host ""
Write-Host "Force cleanup - stuck Docker containers" -ForegroundColor Yellow
Write-Host ""

$prev = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    $ErrorActionPreference = $prev
    throw "Docker not running. Start Docker Desktop first."
}
$ErrorActionPreference = $prev

$containers = @(
    "$ClusterName-control-plane",
    "$ClusterName-worker"
)

foreach ($name in $containers) {
    $exists = docker ps -a --filter "name=^${name}$" --format "{{.Names}}" 2>$null
    if ($exists) {
        Write-Host "Removing $name ..." -ForegroundColor Gray
        docker stop -t 2 $name 2>&1 | Out-Null
        docker rm -f -v $name 2>&1 | ForEach-Object { Write-Host $_ }
    }
}

# Remove any leftover kind containers for this cluster
docker ps -a --filter "label=io.x-k8s.kind.cluster=$ClusterName" --format "{{.Names}}" 2>$null | ForEach-Object {
    if ($_) {
        Write-Host "Removing leftover $_ ..." -ForegroundColor Gray
        docker rm -f -v $_ 2>&1 | Out-Null
    }
}

$prev = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
kind delete cluster --name $ClusterName 2>&1 | ForEach-Object { Write-Host $_ }
$ErrorActionPreference = $prev

$remaining = kind get clusters 2>&1 | Out-String
if ($remaining -match $ClusterName) {
    Write-Host ""
    Write-Host "Cluster still listed. Restart Docker Desktop, then run this script again." -ForegroundColor Red
    Write-Host "  Docker Desktop tray icon -> Restart" -ForegroundColor Yellow
    exit 1
}

Write-Host ""
Write-Host "Cleanup OK - enlight-lab removed." -ForegroundColor Green
exit 0
