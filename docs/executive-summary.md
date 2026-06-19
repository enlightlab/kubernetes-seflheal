# Executive Summary — Enlight Lab Platform

**Audience:** Engineering leadership  
**Cloud cost today:** $0 (local Kubernetes)  
**Status:** Demo-ready for Demo 2 + foundation

---

## Problem

Teams need to prove **modern DevOps** without burning cloud budget during build:

- Deploy by conversation (AI + pipeline)
- Policy before production (not after)
- Observable, rollback-ready platforms
- Five stakeholder demos on **one** architecture

## What we built

| Capability | Tool | Demo value |
|------------|------|------------|
| **GitOps deploy + self-heal** | ArgoCD | Rollback engine for Demo 1 |
| **Policy at CI + cluster** | OPA + Kyverno | Demo 2 block/pass + Demo 5 PR gates |
| **Workload** | FastAPI on Kubernetes | Shared app for all demos |
| **Observability** | Prometheus + Grafana | SLOs and dashboards |
| **Zero-cost dev** | kind cluster | Manager demos without AWS bill |
| **Production path** | Terraform EKS | Same manifests, real cluster on demo day |
| **AWS drift (future)** | Floci emulator | Demo 4 without S3 charges |

## Live evidence today

1. **Bad deploy blocked** — OPA shows exact violations (limits, image, `:latest`)
2. **Good deploy live** — `/health` returns `{"status":"ok"}`
3. **Cluster admission** — Kyverno rejects `nginx:latest` pod live
4. **Dashboards** — Grafana + ArgoCD UI running

## One-command demo

```powershell
.\scripts\start-platform.ps1
.\scripts\run-manager-demo.ps1
```

## Roadmap (five demos)

| Order | Demo | Status |
|-------|------|--------|
| — | Foundation | **Live (local)** |
| 1 | Chat-to-Deploy | **Policy + deploy working** |
| 2 | AI Incident Response | Planned |
| 3 | Drift & Cost (Floci) | Scaffolded |
| 4 | PR Compliance Bot | Planned |
| 5 | Backstage IDP | Planned |

## Ask from leadership

- Approval to run **one EKS cluster** during demo windows (pre-warm, destroy after)
- GitHub org repo for `enlight-lab-platform` + Claude MCP workflow token
- 2-week sprint to complete Demos 1 + GitHub Actions + Claude trigger

## One-line pitch

> "We built a zero-cost Enlight Lab platform that blocks bad deploys, runs a live app with GitOps and observability, and scales to EKS for executive demos — without re-showing the old PoCs."
