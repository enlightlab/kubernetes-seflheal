# Kube Self-Heal Demo

Standalone web app for the **outage response** story only: simulate a failure, explain it with **k8sgpt**, then recover with **GitOps** (ArgoCD).

No other demos (release safety, cloud guard, onboarding, etc.) — built for a focused manager presentation.

## What it does

| Step | Button | What happens |
|------|--------|--------------|
| 1 | Simulate outage | Sets a bad container image; staging app goes unhealthy |
| 2 | Explain with AI | Runs `k8sgpt analyze --namespace enlight-staging` |
| 3 | Auto-fix app | Applies known-good manifest + re-enables ArgoCD self-heal |

**Golden rule:** AI explains. GitOps fixes. Two separate clicks.

## Prerequisites

1. **Kubernetes cluster** from the main platform (one-time setup):

   ```bat
   cd D:\enlight-lab-platform
   scripts\go-live.bat
   ```

   This creates `kind-enlight-lab` with `enlight-staging` and ArgoCD.

2. **k8sgpt** installed and on PATH (for step 2).

3. **Python 3.10+** (UI server).

## Quick start

```bat
cd D:\devops-selfheal

REM Port-forwards (app :30800, ArgoCD :8082)
go-live.bat

REM In a second terminal — web UI on :30901
start-selfheal-ui.bat
```

Open **http://localhost:30901** in your browser.

## Project layout

```
devops-selfheal/
├── go-live.bat              # Port-forwards only
├── start-selfheal-ui.bat    # Launch web UI
├── scripts/
│   ├── go-live.ps1
│   ├── start-selfheal-ui.ps1
│   ├── port-forward-all.ps1
│   └── stop-port-forwards.ps1
└── web/
    ├── server.py            # FastAPI routes
    ├── actions.py           # kubectl + k8sgpt logic
    └── static/index.html    # Client-facing UI
```

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `ENLIGHT_LAB_ROOT` | `D:\enlight-lab-platform` | Path to heal overlay (`demos/demo2-chat-to-deploy/overlays/local`) |

## Links during demo

- **Demo UI:** http://localhost:30901
- **App health:** http://localhost:30800/health
- **ArgoCD app:** http://localhost:8082/applications/argocd/fastapi-staging

## Relation to full platform

This project reuses the same cluster and manifests as `D:\enlight-lab-platform` but exposes only Demo 1 through a dedicated UI. The full five-demo control panel remains at http://localhost:30900 in the main repo.

## Azure DevOps

This folder aligns with the `devops-selfheal` repo on Azure DevOps. Initialize git here when ready to push.
