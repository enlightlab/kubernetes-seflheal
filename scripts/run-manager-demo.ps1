# Manager demo - full story in ~12 minutes ($0 cloud)
#Requires -Version 5.1
$ErrorActionPreference = "Stop"
$PlatformRoot = Split-Path -Parent $PSScriptRoot
$Demo2 = Join-Path $PlatformRoot "demos\demo2-chat-to-deploy\scripts\run-demo.ps1"
$Ctx = "kind-enlight-lab"

function Wait-Enter($msg) {
    Write-Host ""
    Write-Host $msg -ForegroundColor Yellow
    Read-Host "Press Enter to continue"
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  ENLIGHT LAB - MANAGER DEMO" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# --- Part 1: Story ---
Write-Host ""
Write-Host "[1/6] OPENING" -ForegroundColor Green
Write-Host @"

SAY: "This is Enlight Lab — five DevOps demos on one platform.
We unified our earlier PoCs: delivery pipeline + policy + cost.
Today runs at ZERO cloud cost on local Kubernetes.
Production path is the same stack on EKS."

"@ -ForegroundColor White

Wait-Enter "Part 1 done. Next: policy gate (CI layer)."

# --- Part 2: OPA block ---
Write-Host ""
Write-Host "[2/6] DEMO 2 - POLICY BLOCK (bad deploy)" -ForegroundColor Green
Write-Host 'SAY: "A bad manifest never reaches the cluster. OPA blocks it in CI."' -ForegroundColor White
& $Demo2 -Variant non-compliant

Wait-Enter "Show the red VIOLATION lines. Next: policy pass."

# --- Part 3: OPA pass ---
Write-Host ""
Write-Host "[3/6] DEMO 2 - POLICY PASS (good deploy)" -ForegroundColor Green
Write-Host 'SAY: "Compliant manifest passes — this is what we deploy."' -ForegroundColor White
& $Demo2 -Variant compliant

Wait-Enter "Next: live app on cluster."

# --- Part 4: Live app ---
Write-Host ""
Write-Host "[4/6] LIVE APP" -ForegroundColor Green
kubectl config use-context $Ctx 2>$null
kubectl get pods -n enlight-staging
Write-Host ""
Write-Host "Open: http://localhost:30800/health" -ForegroundColor Cyan
Write-Host 'SAY: "FastAPI is live. Health check returns ok."' -ForegroundColor White

try {
    $r = Invoke-RestMethod -Uri "http://localhost:30800/health" -TimeoutSec 5
    Write-Host "Health response: $($r | ConvertTo-Json -Compress)" -ForegroundColor Green
} catch {
    Write-Host "Health check failed — run .\scripts\start-platform.ps1 first" -ForegroundColor Red
}

Wait-Enter "Next: cluster policy block (Kyverno)."

# --- Part 5: Kyverno live block ---
Write-Host ""
Write-Host "[5/6] CLUSTER POLICY BLOCK (Kyverno)" -ForegroundColor Green
Write-Host 'SAY: "Same rules at the cluster door — admission control."' -ForegroundColor White
$violation = Join-Path $PlatformRoot "foundation\policies\kyverno\test-violation-pod.yaml"
kubectl apply -f $violation 2>&1
Write-Host ""
Write-Host "Expected: Error / denied by policy enlight-disallow-latest or enlight-require-limits" -ForegroundColor Yellow

Wait-Enter "Next: observability + closing."

# --- Part 6: Dashboards ---
Write-Host ""
Write-Host "[6/6] OBSERVABILITY + CLOSE" -ForegroundColor Green
Write-Host @"
Open Grafana:  http://localhost:3000  (admin / enlight-admin)
Open ArgoCD:   http://localhost:8080

SAY: "Grafana for SLOs and dashboards. ArgoCD for GitOps self-heal.
AI tools (k8sgpt, HolmesGPT) explain incidents — ArgoCD rolls back.
Next: Claude triggers GitHub Actions, then EKS for production demos."

OUTCOMES:
  1. Policy gate blocks bad deploys with clear reasons
  2. Compliant app deployed and healthy
  3. Cluster admission enforces same rules
  4. Observability stack running
  5. Same architecture scales to EKS at demo time

"@ -ForegroundColor White

Write-Host "Demo complete." -ForegroundColor Green
