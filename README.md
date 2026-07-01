# Kube Self-Heal Demo

Standalone web app for the **outage response** story: simulate a failure, explain it with **k8sgpt**, then recover with **GitOps** (ArgoCD). Built for client demos on **Oracle OKE** and local **kind**.

## What it does

| Step | Button | What happens |
|------|--------|--------------|
| 1 | Simulate outage | Sets bad image — ArgoCD Progressing → Degraded (~1–2 min) |
| 2 | Explain with AI | Runs `k8sgpt analyze` on the staging namespace |
| 3 | Auto-fix app | Restores good deployment + re-enables ArgoCD sync |

**Golden rule:** AI explains. GitOps fixes. Two separate clicks.

## Oracle OKE (production demo)

| Item | Value |
|------|-------|
| Demo UI | `http://<selfheal-ui-LB-IP>/` |
| Staging app | `http://<selfheal-ui-LB-IP>/staging/` |
| Deploy | `deploy/oci/README.md` |
| Terraform | `infra/oci/` |

```bash
# Cloud Shell
kubectl apply -f deploy/k8s/selfheal-ui.yaml
kubectl -n selfheal get svc selfheal-ui
```

Build and push image:

```powershell
cd D:\devops-selfheal
docker build --platform linux/amd64 -f deploy/Dockerfile -t bom.ocir.io/<tenancy>/selfheal-ui:latest .
docker push bom.ocir.io/<tenancy>/selfheal-ui:latest
```

## Local kind (development)

```bat
cd D:\devops-selfheal
go-live.bat
start-selfheal-ui.bat
```

Open **http://localhost:30901**

## Project layout

```
devops-selfheal/
├── web/                 # FastAPI demo UI (actions.py, static/)
├── deploy/
│   ├── Dockerfile
│   ├── k8s/             # selfheal-ui, staging-app, ArgoCD app
│   └── oci/             # OKE deploy + fix scripts
├── infra/oci/           # Terraform (VCN, OKE, OCIR)
├── scripts/             # Local port-forwards
└── demos/               # Enlight Lab platform demos (legacy in repo)
```

## Configuration

See `deploy/oci/env.example` and `deploy/k8s/selfheal-ui.yaml` ConfigMap.

| Variable | Purpose |
|----------|---------|
| `OUTAGE_MODE` | `image` (default), `instant`, or `crash` |
| `GOOD_IMAGE` / `BAD_IMAGE` | OCIR image refs for heal / break |
| `DEPLOY_TARGET` | `oci` or `local` |

## Azure DevOps

Repo: https://dev.azure.com/enlight-lab/devops/_git/devops-selfheal

## Relation to enlight-lab-platform

This repo also contains the broader **Enlight Lab** demo platform under `demos/`, `workload/`, etc. The **self-heal client demo** lives in `web/` + `deploy/` at the repo root.
