# Enlight Lab Brief — Full Compliance Checklist

**What the brief requires vs what you have now.**

Legend: ✅ Working | 🟡 Partial | ❌ Not built | 🏠 Local substitute (not brief-exact)

---

## Shared foundation (required for ALL demos)

| Brief requirement | Brief says | Your status | Local substitute | Still needed for full compliance |
|-------------------|------------|-------------|------------------|----------------------------------|
| **EKS cluster** | terraform-aws-modules/eks, pre-warm, destroy after | ❌ Terraform only | 🏠 kind cluster | `terraform apply` on AWS before demo day |
| **FastAPI workload** | tiangolo/full-stack-fastapi-template | 🟡 Minimal FastAPI (/health) | Same app, simplified | Optional: swap to full template |
| **GitHub Actions CI/CD** | Pipeline for all demos | 🟡 Workflow file exists | Not running live | Push repo + secrets + test workflow |
| **ArgoCD GitOps** | Self-heal enabled | 🟡 Installed on kind | 🏠 kind not EKS | Wire live sync to staging URL on EKS |
| **OPA / Gatekeeper** | Policy gate | 🟡 OPA in CI + Kyverno on cluster | 🏠 Kyverno instead of Gatekeeper | Add Gatekeeper OR document Kyverno as equivalent |
| **Prometheus + Grafana** | Observability | 🟡 Installed, often slow on laptop | 🏠 Same stack on kind | Stable Grafana + SLO dashboard for Demo 1 |
| **k8sgpt MCP** | v0.4.14+, read-only diagnose | ❌ | — | Install in Claude Desktop + test |
| **HolmesGPT MCP** | Read-only RCA | ❌ | — | Install in Claude Desktop + test |
| **Claude Desktop + MCP** | AI layer | ❌ GitHub MCP not wired | — | GitHub token + workflow_dispatch test |

**Golden rule (brief):** AI explains → ArgoCD/GitHub Actions fixes. **Two separate visible steps.** ❌ Not demonstrated end-to-end yet.

---

## Demo 2 — Chat-to-Deploy (build FIRST)

| Brief requirement | Status | Notes |
|-------------------|--------|-------|
| Claude command triggers pipeline | ❌ | Need GitHub MCP + workflow_dispatch |
| Stages: test → build → OPA → deploy | 🟡 | Workflow defined; ECR push not wired |
| Non-compliant deploy blocked + reason | ✅ | `run-demo.ps1 -Variant non-compliant` |
| Compliant deploy → live staging URL | 🟡 | `/health` works locally; not via full pipeline |
| No kubectl on screen | 🟡 | Demo scripts use kubectl; brief says no manual kubectl |
| Recorded fallback | ❌ | Record screen captures |
| One script per demo | ✅ | `run-demo.ps1` |

---

## Demo 1 — AI Incident Response

| Brief requirement | Status |
|-------------------|--------|
| Robusta detection/alerting | ❌ |
| HolmesGPT + k8sgpt RCA in Claude | ❌ |
| ArgoCD self-heal rollback | 🟡 ArgoCD installed, rollback not scripted |
| Rollback on **SLO breach** (not just pod crash) | ❌ |
| Scripted failure (bad image / OOM) | ❌ |
| Claude postmortem draft | ❌ |
| Failure → RCA in under 60 seconds | ❌ |
| One reproducible script | ❌ |

---

## Demo 4 — Drift & Cost Sentinel

| Brief requirement | Status |
|-------------------|--------|
| driftctl or terraform plan-diff | ❌ |
| Cost estimate on drifted resource | ❌ |
| Claude explains change + cost delta | ❌ |
| Reconciliation PR | ❌ |
| Floci / local AWS | 🟡 `floci/docker-compose.yml` scaffolded |

---

## Demo 5 — PR-Gated Compliance Bot

| Brief requirement | Status |
|-------------------|--------|
| OPA/Kyverno in PR checks | ❌ |
| Claude comments on PR (control + fix) | ❌ |
| Merge blocked until fixed | ❌ |
| Hardcoded secret blocked | ❌ |
| Evidence artifact (check → control) | ❌ |

---

## Demo 3 — Backstage IDP (build LAST)

| Brief requirement | Status |
|-------------------|--------|
| Backstage catalog + templates | ❌ |
| Claude agent scaffolds service | ❌ |
| publish:github action | ❌ |
| ServiceMonitor + Grafana + alerts pre-wired | ❌ |
| Secrets + policy in template | ❌ |
| Runbook + incident via PRs | ❌ |

---

## Pre-demo checklist (brief)

| Item | Status |
|------|--------|
| Cluster warm 30 min early | 🟡 kind works when Docker stable |
| Rehearse phrasing 5+ times | ❌ Your task |
| Recorded fallback per segment | ❌ |
| Claude ↔ k8sgpt ↔ HolmesGPT tested same day | ❌ |
| Build caches warm | 🟡 |
| terraform destroy after demo | ❌ EKS not provisioned |

---

## Honest completion score

| Area | % complete |
|------|------------|
| Shared foundation | ~45% |
| Demo 2 | ~50% |
| Demo 1 | ~5% |
| Demo 4 | ~10% |
| Demo 5 | ~5% |
| Demo 3 | ~0% |
| **Overall brief** | **~25–30%** |

---

## Local vs brief-exact

| You asked for local | Brief exact |
|---------------------|-------------|
| kind | **EKS** (pre-warmed) |
| Kyverno | OPA/Gatekeeper (both acceptable if documented) |
| Manual scripts | Claude → GitHub Actions → ArgoCD |
| Laptop Grafana | Same, but needs SLO for Demo 1 |

**Local is for building cheaply. Final demo to manager per brief needs EKS + full wiring.**

---

## Build order to reach 100% (follow brief)

1. **Finish Demo 2 on EKS** — GitHub push, MCP, Actions, ArgoCD live URL
2. **Demo 1** — Robusta, failure script, SLO alert, rollback, postmortem
3. **Demo 4** — driftctl + Infracost/Floci + reconciliation PR
4. **Demo 5** — PR bot + evidence JSON
5. **Demo 3** — Backstage last

---

## What you can show manager TODAY (honest)

✅ Policy gate (OPA) block/pass  
✅ Live app `/health` on local Kubernetes  
✅ Kyverno cluster block  
✅ Architecture + roadmap + Terraform ready  
❌ Not yet: full five demos exactly as brief

**Say:** "Foundation and Demo 2 policy path are proven locally. Remaining demos and EKS production path are scheduled per build order."
