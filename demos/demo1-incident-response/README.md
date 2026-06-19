# Outage response (Demo 1)



**UI name:** Outage response on http://localhost:30900



**Story:** App breaks → AI explains why → platform rolls back automatically.



## Buttons



| Button | What happens |

|--------|----------------|

| **Simulate outage** | Bad image on staging app (on purpose) |

| **Explain with AI** | k8sgpt summarizes failure in activity log |

| **Auto-fix app** | GitOps rollback + healthy deployment |



**Show client:** Deployments at http://localhost:8082 (`fastapi-staging` red then green).



After heal, click **Refresh** on Live Demo if dashboard still shows fail (port-forward tunnel).



## Scripts



```powershell

.\scripts\inject-failure.ps1

.\scripts\heal-rollback.ps1

```



## Presenter script



See **Outage response** in [docs/CLIENT-DEMO-EXPLAINED.pdf](../../docs/CLIENT-DEMO-EXPLAINED.pdf)


