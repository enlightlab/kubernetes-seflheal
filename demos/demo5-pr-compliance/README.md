# Code review security (Demo 5)



**UI name:** Code review security on http://localhost:30900



**Story:** Every pull request is scanned. Secrets and unsafe config cannot merge.



## Buttons



| Button | What happens |

|--------|----------------|

| **Open risky change request** | New GitHub PR with secrets + bad S3 → checks fail |

| **Open safe change request** | New GitHub PR with clean config → checks pass |

| **View check results** | Latest workflow status |



**Normal:** One PR, two workflow runs (service CI + compliance bot).



## Scripts



```powershell

.\scripts\create-pr.ps1 -Variant non-compliant

.\scripts\create-pr.ps1 -Variant compliant

```



## Presenter script



See **Code review security** in [docs/CLIENT-DEMO-EXPLAINED.pdf](../../docs/CLIENT-DEMO-EXPLAINED.pdf)


