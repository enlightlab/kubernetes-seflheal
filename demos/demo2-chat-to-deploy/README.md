# Release safety (Demo 2)



**UI name:** Release safety on http://localhost:30900



**Story:** Unsafe releases are blocked in CI before they reach the cluster.



## Buttons



| Button | What happens |

|--------|----------------|

| **Block unsafe release** | Dispatches bad manifest → GitHub Actions fails policy checks |

| **Approve safe release** | Dispatches good manifest → checks pass |



**Show client:** Build pipelines on GitHub — red (block) or green (pass).



## Run from terminal (optional)



```powershell

.\scripts\run-demo.ps1 -Variant non-compliant

.\scripts\run-demo.ps1 -Variant compliant

```



## Presenter script



See **Release safety** in [docs/CLIENT-DEMO-EXPLAINED.pdf](../../docs/CLIENT-DEMO-EXPLAINED.pdf)


