# Demo 5 - PR-Gated Compliance Bot (local simulation)
#Requires -Version 5.1
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("non-compliant", "compliant")]
    [string]$Variant
)

$DemoRoot = Split-Path -Parent $PSScriptRoot
$Samples = Join-Path $DemoRoot "samples"
$EvidenceDir = Join-Path $DemoRoot "evidence"
New-Item -ItemType Directory -Path $EvidenceDir -Force | Out-Null

Write-Host ""
Write-Host "=== Demo 5: PR Compliance Bot ===" -ForegroundColor Cyan
Write-Host "Variant: $Variant" -ForegroundColor Yellow
Write-Host ""

function Test-SecretsPolicy {
    param([string]$File)
    $text = Get-Content $File -Raw
    $violations = @()
    if ($text -match '(?i)(api[_-]?key|password)\s*=\s*"[^"]{8,}"') {
        $violations += "SEC-001: Hardcoded secret in code"
    }
    if ($text -match 'ghp_[A-Za-z0-9]{20,}') {
        $violations += "SEC-002: GitHub token pattern in code"
    }
    return $violations
}

function Test-S3Policy {
    param([string]$File)
    $text = Get-Content $File -Raw
    $violations = @()
    if ($text -match 'acl\s*=\s*"public-read"') {
        $violations += "S3-001: Public S3 bucket forbidden"
    }
    if ($text -notmatch 'server_side_encryption') {
        $violations += "S3-002: S3 encryption missing"
    }
    return $violations
}

$pyFile = if ($Variant -eq "compliant") { "good-config.py" } else { "bad-config.py" }
$tfFile = if ($Variant -eq "compliant") { "good-s3.tf" } else { "bad-s3.tf" }

$allViolations = @()
$allViolations += Test-SecretsPolicy (Join-Path $Samples $pyFile)
$allViolations += Test-S3Policy (Join-Path $Samples $tfFile)

Write-Host "Files checked:" -ForegroundColor Gray
Write-Host "  $pyFile" -ForegroundColor Gray
Write-Host "  $tfFile" -ForegroundColor Gray
Write-Host ""

foreach ($v in $allViolations) {
    Write-Host "VIOLATION: $v" -ForegroundColor Red
}

$pass = ($allViolations.Count -eq 0)
$evidence = @{
    demo = "demo5-pr-compliance"
    variant = $Variant
    timestamp = (Get-Date).ToString("o")
    controls = @(
        @{ id = "SEC-001"; description = "No hardcoded secrets"; pass = -not ($allViolations -match "SEC-001") }
        @{ id = "SEC-002"; description = "No GitHub tokens in code"; pass = -not ($allViolations -match "SEC-002") }
        @{ id = "S3-001"; description = "No public S3 buckets"; pass = -not ($allViolations -match "S3-001") }
        @{ id = "S3-002"; description = "S3 encryption required"; pass = -not ($allViolations -match "S3-002") }
    )
    merge_allowed = $pass
}
$evidencePath = Join-Path $EvidenceDir "compliance-$Variant.json"
$evidence | ConvertTo-Json -Depth 5 | Set-Content $evidencePath

Write-Host ""
if ($pass) {
    Write-Host "RESULT: PASS - merge allowed" -ForegroundColor Green
    Write-Host "Evidence: $evidencePath" -ForegroundColor Gray
} else {
    Write-Host "RESULT: BLOCKED - merge blocked until fixed" -ForegroundColor Red
    Write-Host "SAY: Bot comments on PR with control ID + fix steps" -ForegroundColor Gray
    Write-Host "Evidence: $evidencePath" -ForegroundColor Gray
}

Write-Host ""
Write-Host "GitHub: open PR with these files -> .github/workflows/pr-compliance.yml runs" -ForegroundColor Gray
exit $(if ($pass) { 0 } else { 1 })
