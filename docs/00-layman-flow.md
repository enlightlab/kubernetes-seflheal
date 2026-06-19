# Enlight Lab — Simple Flow (Layman)

## The whole thing in one picture

```text
YOU (plain English in Claude)
        |
        v
AUTOMATED PIPELINE (GitHub Actions)
        |
        v
RULES CHECK (OPA) -----> FAIL? Stop + show reason
        |
        v PASS
DEPLOY (ArgoCD) --------> Live app on EKS
        |
        v
IF SOMETHING BREAKS:
   AI explains (read-only)  |  System rolls back (ArgoCD)
   TWO SEPARATE STEPS — never mixed
```

## Who does what

| Role | Plain English |
|------|---------------|
| **You + Claude** | Give orders in normal language |
| **GitHub Actions** | Runs test, build, policy, deploy steps |
| **OPA** | Bouncer — blocks bad configs with a reason |
| **ArgoCD** | Waiter — puts the right version live; self-heals mistakes |
| **k8sgpt / HolmesGPT** | Doctor — explains problems, does NOT fix |
| **EKS** | The restaurant — where apps run |

## Five demos = five stories

| # | Name | One sentence |
|---|------|--------------|
| 2 | Chat-to-Deploy | Say "deploy to staging" → pipeline runs → bad blocked, good goes live |
| 1 | AI Incident Response | App breaks → AI explains → auto-rollback → postmortem draft |
| 4 | Drift & Cost Sentinel | Manual AWS change detected → cost shown → fix PR opened |
| 5 | PR Compliance Bot | Bad PR blocked at merge → fix → evidence report |
| 3 | Backstage IDP | Portal scaffolds new service → all changes via PRs |

## Demo 2 flow (first to build)

```text
"Deploy feature branch to staging"
              |
              v
        test + build
              |
              v
         OPA check
         /        \
     BLOCK        PASS
        |            |
   show reason    ArgoCD deploy
                  staging /health OK
```

## What to build first

```text
1. foundation/     EKS + ArgoCD + OPA + Prometheus/Grafana
2. demo2/          Chat-to-Deploy (pipeline + policy gate)
3. demo1/          Failure script + AI RCA + rollback
4. demo4/          Drift + cost + reconciliation PR
5. demo5/          PR checks + compliance bot
6. demo3/          Backstage (last)
```
