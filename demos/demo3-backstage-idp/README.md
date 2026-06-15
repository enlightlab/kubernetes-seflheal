# New service onboarding (Demo 3)



**UI name:** New service onboarding on http://localhost:30900



**Story:** Developer portal scaffolds a new service → PR for review → deploy with monitoring pre-wired. **Main platform demo.**



## Buttons



| Button | What happens |

|--------|----------------|

| **Create new app** | New `svc-TIMESTAMP` with K8s, CI, Terraform, ArgoCD, monitoring |

| **Submit for review** | Opens GitHub PR with CI checks |

| **Go live** | Registers app in ArgoCD and deploys |



Each **Create new app** run produces a **new** service name (not the same app every time).



**Show client:** Developer portal http://localhost:30800/idp + Deployments http://localhost:8082



## Scripts



```powershell

.\scripts\run-demo.ps1 -Phase scaffold

.\scripts\run-demo.ps1 -Phase pr

.\scripts\run-demo.ps1 -Phase deploy

```



## Presenter script



See **New service onboarding** in [docs/CLIENT-DEMO-EXPLAINED.pdf](../../docs/CLIENT-DEMO-EXPLAINED.pdf)


