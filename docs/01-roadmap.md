# Build Roadmap

## Phase 0 — Foundation (shared by all demos)

| # | Task | Folder | Status |
|---|------|--------|--------|
| 0.1 | EKS cluster Terraform | `foundation/terraform/eks/` | Scaffolded |
| 0.2 | FastAPI workload | `workload/fastapi/` | Done |
| 0.3 | ArgoCD + self-heal | `gitops/argocd/` | Scaffolded |
| 0.4 | OPA policies (CI) | `foundation/policies/opa/` | Done |
| 0.5 | Gatekeeper (cluster) | `foundation/policies/gatekeeper/` | Scaffolded |
| 0.6 | Prometheus + Grafana | `foundation/helm/monitoring/` | Planned |
| 0.7 | Install script | `foundation/scripts/02-install-platform.ps1` | Scaffolded |
| 0.8 | Claude MCP setup | `docs/02-mcp-setup.md` | Planned |

## Phase 1 — Demo 2: Chat-to-Deploy (CURRENT)

| # | Task | Status |
|---|------|--------|
| 2.1 | Compliant + violating manifests | Done |
| 2.2 | OPA policy check script | Done |
| 2.3 | GitHub Actions workflow | Done |
| 2.4 | Claude → workflow_dispatch | Planned |
| 2.5 | Live EKS staging deploy | Needs cluster |

## Phase 2 — Demo 1: AI Incident Response

| # | Task | Status |
|---|------|--------|
| 1.1 | Robusta install | Planned |
| 1.2 | Failure injection script | Planned |
| 1.3 | HolmesGPT + k8sgpt MCP | Planned |
| 1.4 | SLO-based rollback trigger | Planned |
| 1.5 | Postmortem prompt template | Planned |

## Phase 3 — Demo 4: Drift & Cost Sentinel

Reuse patterns from `cloud-cost-k8s-poc` + driftctl or terraform plan job.

## Phase 4 — Demo 5: PR-Gated Compliance Bot

OPA in PR checks + Claude comments + evidence JSON artifact.

## Phase 5 — Demo 3: Backstage IDP (last)

Golden-path template with observability, secrets, policy pre-wired.

## Suggested timeline

| Week | Deliverable |
|------|-------------|
| W1 | Foundation live on EKS + Demo 2 end-to-end |
| W2 | Demo 1 scripted failure + rollback |
| W3 | Demo 4 + Demo 5 |
| W4 | Demo 3 Backstage |
