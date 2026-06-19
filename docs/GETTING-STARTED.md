# Getting Started

## Prerequisites

| Tool | Install |
|------|---------|
| Docker Desktop | https://www.docker.com/products/docker-desktop/ |
| kubectl | `choco install kubernetes-cli` or official docs |
| kind | `choco install kind` |
| Helm 3 | `choco install kubernetes-helm` |

## One command (recommended)

```powershell
cd D:\enlight-lab-platform
.\scripts\start-platform.ps1
```

Takes **10-20 minutes** first time (pulls Helm images). Then open:

| Service | URL | Login |
|---------|-----|-------|
| App | http://localhost:30800/health | — |
| Grafana | http://localhost:3000 | admin / enlight-admin |
| Live Demo UI | http://localhost:30900 | Primary client demo |
| ArgoCD | http://localhost:8082 | admin password below |

ArgoCD admin password:

```powershell
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | ForEach-Object { [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($_)) }
```

## Manager demo

```powershell
.\start-demo-control.bat
```

Open http://localhost:30900. Presenter guide: `docs/CLIENT-DEMO-EXPLAINED.pdf`

Legacy script:

```powershell
.\scripts\run-manager-demo.ps1
```

## Policy check only (no cluster, 30 seconds)

```powershell
.\demos\demo2-chat-to-deploy\scripts\run-demo.ps1 -Variant non-compliant
.\demos\demo2-chat-to-deploy\scripts\run-demo.ps1 -Variant compliant
```

## Teardown

```powershell
.\scripts\stop-platform.ps1
.\foundation\scripts\99-destroy.ps1
```

## Production path (real EKS)

```powershell
cd foundation\terraform\eks
terraform init
terraform apply
aws eks update-kubeconfig --region us-east-1 --name enlight-lab
cd ..\..
.\foundation\scripts\02-install-platform.ps1
```

Destroy after demo: `terraform destroy`

## Floci (Demo 4 only — not for K8s)

```powershell
cd floci
docker compose up -d
```

See `floci/README.md`
