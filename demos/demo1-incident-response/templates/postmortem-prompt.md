# Paste this into Claude after Demo 1 failure (with k8sgpt/HolmesGPT connected)

```
Using the incident data from enlight-staging namespace deployment fastapi:

1. Summarize what failed in plain English (root cause).
2. Timeline: detection -> diagnosis -> rollback -> recovery.
3. What policy or guardrail would prevent recurrence.
4. Draft a short postmortem (no blame, action items only).

Incident context: ImagePullBackOff after bad image tag inject-failure.ps1.
Rollback: heal-rollback.ps1 restored enlight-fastapi:demo-pass.
```
