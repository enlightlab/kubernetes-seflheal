# Pack web/ + deploy/ for OCI Cloud Shell (Linux-safe tar.gz — no backslash zip issues).
# Run on Windows:  powershell -File deploy/oci/pack-holmes-overlay.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
if (-not (Test-Path "$Root\web\actions.py")) {
    $Root = (Get-Location).Path
}
$Out = Join-Path $env:USERPROFILE "Downloads\holmes-deploy.tar.gz"
Push-Location $Root
try {
    if (Test-Path $Out) { Remove-Item $Out -Force }
    # Linux bash rejects CRLF — normalize shell scripts before tar.
    Get-ChildItem -Path deploy -Filter *.sh -Recurse | ForEach-Object {
        $text = [System.IO.File]::ReadAllText($_.FullName)
        if ($text -match "`r") {
            $fixed = $text -replace "`r`n", "`n" -replace "`r", "`n"
            [System.IO.File]::WriteAllText($_.FullName, $fixed)
        }
    }
    if (Test-Path "$Root\apply-holmes.sh") {
        $text = [System.IO.File]::ReadAllText("$Root\apply-holmes.sh")
        if ($text -match "`r") {
            $fixed = $text -replace "`r`n", "`n" -replace "`r", "`n"
            [System.IO.File]::WriteAllText("$Root\apply-holmes.sh", $fixed)
        }
    }
    tar -czf $Out apply-holmes.sh web deploy
    Write-Host "Created: $Out"
    Write-Host "Upload to Cloud Shell, then:"
    Write-Host "  cd ~ && rm -rf devops-selfheal && mkdir devops-selfheal"
    Write-Host "  tar -xzf ~/holmes-deploy.tar.gz -C devops-selfheal"
    Write-Host "  cd devops-selfheal && bash deploy/oci/deploy-holmes-live.sh"
} finally {
    Pop-Location
}
