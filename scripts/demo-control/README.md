# Live Demo UI (Demo Control Center)



**One browser UI for all five demos** — the only screen to share with clients.



## Start



```powershell

cd D:\enlight-lab-platform

.\scripts\go-live.bat

.\start-demo-control.bat

```



**Open:** http://localhost:30900 → click **Refresh**



## UI sections (top to bottom)



| Section | Buttons | Client story |

|---------|---------|--------------|

| Guided walkthrough | 10 steps | Full scripted demo |

| Release safety | Block unsafe release / Approve safe release | Pipeline blocks bad deploys |

| Outage response | Simulate outage / Explain with AI / Auto-fix app | Break, explain, recover |

| Cloud config guard | Set baseline / Catch drift / Fix drift | Git vs live cloud governance |

| Code review security | Risky PR / Safe PR / View results | PR security scans |

| New service onboarding | Create new app / Submit for review / Go live | Main platform story |



## Presenter guide



Plain-language explanation of every button:



- PDF: [docs/CLIENT-DEMO-EXPLAINED.pdf](../../docs/CLIENT-DEMO-EXPLAINED.pdf)

- Regenerate: `.\generate-client-demo-guide.bat`



## Quick links bar



| Link | URL |

|------|-----|

| App dashboard | :30800 |

| Developer portal | :30800/idp |

| Deployments (GitOps) | :8082 |

| Monitoring | :3000 |

| Build pipelines | GitHub Actions |



## Stop



```powershell

.\scripts\stop-platform.ps1

```


