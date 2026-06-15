# Cloud config guard (Demo 4)



**UI name:** Cloud config guard on http://localhost:30900



**Story:** Git is the contract for cloud settings. Detect when live drifts from Git; restore automatically.



## Three buttons (in order)



| Button | What happens | What client sees |

|--------|----------------|------------------|

| **Set secure baseline** | Terraform apply creates private, encrypted bucket `enlight-demo` | MATCHES GIT (green) |

| **Catch config drift** | Simulates someone making storage public outside Git | OUT OF SYNC (red) |

| **Fix drift automatically** | Terraform apply restores private + encrypted | MATCHES GIT (green) |



## Run from terminal (optional)



```powershell

.\scripts\run-demo.ps1 -Phase baseline

.\scripts\run-demo.ps1 -Phase drift

.\scripts\run-demo.ps1 -Phase reconcile

```



## Backend



- Terraform: `foundation/terraform/demo4/main.tf`

- Local cloud sandbox: [floci/README.md](../../floci/README.md) (`:4566` API only - not a browser URL)



## Presenter script



See **Cloud config guard** section in [docs/CLIENT-DEMO-EXPLAINED.pdf](../../docs/CLIENT-DEMO-EXPLAINED.pdf)


