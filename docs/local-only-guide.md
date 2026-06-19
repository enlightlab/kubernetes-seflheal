# Enlight Lab — 100% Local Guide

Everything runs on your laptop. **No AWS. No EKS bills.**

## Local replacements

| Cloud (brief) | Your local version |
|---------------|-------------------|
| EKS | **kind** cluster (`enlight-lab`) |
| ECR | **Docker image** loaded into kind |
| AWS S3/Lambda (Demo 4) | **Floci** (`floci/docker-compose.yml`) |
| ArgoCD on cloud | **ArgoCD** on kind |
| OPA / Kyverno | Same — on your laptop |
| Prometheus / Grafana | Same — on kind |

**Skip for now:** `foundation/terraform/eks/` — only for real demo day on AWS.

---

## One-time install

1. Docker Desktop
2. kubectl
3. kind (`choco install kind`)
4. Helm 3 (`choco install kubernetes-helm`)

---

## Every session (local workflow)

```powershell
# 1. Start Docker Desktop — wait for Engine running
docker info

# 2. Start platform
cd D:\enlight-lab-platform
.\scripts\start-platform.ps1
# OR after reboot:
.\scripts\start-after-reboot.ps1
# OR double-click: start-platform.bat

# 3. Open browser
#    http://localhost:30800/health
#    http://localhost:3000  (Grafana: admin / enlight-admin)
#    http://localhost:8080  (ArgoCD)

# 4. Manager demo
.\scripts\run-manager-demo.ps1
```

---

## Local demos you can run today

### Demo 2 — Policy gate (no cluster even needed)

```powershell
.\demos\demo2-chat-to-deploy\scripts\run-demo.ps1 -Variant non-compliant
.\demos\demo2-chat-to-deploy\scripts\run-demo.ps1 -Variant compliant
```

### Demo 2 — Live deploy on local cluster

```powershell
kubectl get pods -n enlight-staging
kubectl apply -f foundation\policies\kyverno\test-violation-pod.yaml   # expect BLOCK
```

### Demo 4 — AWS emulator (Floci, local)

```powershell
cd floci
docker compose up -d

$env:AWS_ENDPOINT_URL="http://localhost:4566"
$env:AWS_ACCESS_KEY_ID="test"
$env:AWS_SECRET_ACCESS_KEY="test"
aws s3 mb s3://enlight-demo --endpoint-url http://localhost:4566
```

---

## What is NOT required locally

- AWS account
- `terraform apply` for EKS
- Paying for cloud

---

## Optional (still “local first”)

| Item | Notes |
|------|--------|
| GitHub | Free repo to host code; Actions runs on GitHub servers (free tier) |
| Claude Desktop | Local app; MCP triggers GitHub remotely |
| `act` tool | Run GitHub Actions workflows on your PC (advanced) |

---

## Teardown (free up RAM)

```powershell
.\scripts\stop-platform.ps1
.\foundation\scripts\99-destroy.ps1
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Docker not running | Open Docker Desktop |
| Connection refused kubectl | `.\scripts\start-platform.ps1` |
| .ps1 opens in editor | Use `.bat` files or PowerShell with `.\` |
| Slow / timeout | Close other apps; Grafana needs RAM |
