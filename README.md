# Enlight Lab Platform



**Five DevOps demos. One foundation. $0 cloud cost for local demos.**



Unified platform: GitOps, policy gates, observability, IDP golden path, and cloud governance.



---



## Client demo (browser only)



**Share this URL:** http://localhost:30900



```powershell

cd D:\enlight-lab-platform

.\scripts\go-live.bat

.\start-demo-control.bat

```



Open http://localhost:30900 and click **Refresh**.



| URL | Purpose |

|-----|---------|

| **http://localhost:30900** | **Live Demo UI (primary - share with client)** |

| http://localhost:30800 | App dashboard |

| http://localhost:30800/idp | Developer portal |

| http://localhost:8082 | Deployments (GitOps / ArgoCD) |

| http://localhost:3000 | Monitoring (Grafana) |



**Presenter guide (plain language):** [docs/CLIENT-DEMO-EXPLAINED.pdf](docs/CLIENT-DEMO-EXPLAINED.pdf)



Regenerate PDF: `.\generate-client-demo-guide.bat`



---



## Demos in the Live Demo UI



| UI section | What it proves |

|------------|----------------|

| **Release safety** | CI blocks unsafe deploys |

| **Outage response** | AI explains outages; platform auto-fixes |

| **Cloud config guard** | Git vs live drift detect and auto-fix |

| **Code review security** | Every PR scanned for secrets and bad config |

| **New service onboarding** | Create app, review, go live (main story) |



Details per demo: [demos/README.md](demos/README.md)



---



## What works today



| Feature | Status |

|---------|--------|

| Live Demo UI (`:30900`) | All five demos via buttons |

| Local Kubernetes (kind) | `go-live.bat` |

| FastAPI workload | `/health` on `:30800` |

| OPA policy gate (CI) | Block + pass |

| ArgoCD + self-heal | `:8082` |

| Prometheus + Grafana | `:3000` |

| Floci cloud sandbox (Demo 4 backend) | Auto-starts with `go-live` |

| GitHub Actions + PR compliance | Live |



---



## Quick start (operators)



```powershell

cd D:\enlight-lab-platform

.\scripts\go-live.bat

.\scripts\test-all.ps1 -Quick

```



Full guide: [docs/GETTING-STARTED.md](docs/GETTING-STARTED.md)



---



## Architecture



```text

Live Demo UI (:30900) --> scripts on laptop (kubectl, GitHub, Terraform)

GitHub Actions --> OPA policy gate --> ArgoCD GitOps --> kind cluster

Cloud config guard --> Terraform + Floci sandbox (:4566, API only)

```



**Golden rule:** AI explains. Git/ArgoCD fixes. Two separate steps.



---



## Project layout



```text

enlight-lab-platform/

├── scripts/demo-control/    # Live Demo UI (FastAPI on :30900)

├── scripts/go-live.bat      # One command before demo

├── demos/                   # demo1-5 scripts

├── floci/                   # Local cloud sandbox (Demo 4 backend)

├── workload/fastapi/        # App + dashboard + IDP portal

├── gitops/argocd/

└── docs/

    ├── CLIENT-DEMO-EXPLAINED.pdf

    └── MANAGER-DEMO-CHEATSHEET.md

```



---



## Floci (Demo 4 only)



Local AWS API emulator on `:4566` — **not a browser UI**. Powers **Cloud config guard** on `:30900`.



See [floci/README.md](floci/README.md). **Do not use Floci for Kubernetes** — use `kind`.



---



## Teardown



```powershell

.\scripts\stop-platform.ps1

cd floci && docker compose down

.\foundation\scripts\99-destroy.ps1

```


