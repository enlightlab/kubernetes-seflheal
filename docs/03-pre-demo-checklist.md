# Pre-Demo Checklist

Run before every live demo.

- [ ] Cluster warm 30 min early: `kubectl get nodes`
- [ ] Rehearse exact Claude phrasing 5+ times
- [ ] Recorded fallback ready for each live segment
- [ ] Claude ↔ k8sgpt ↔ HolmesGPT MCP tested same day
- [ ] Build caches warm, container images small
- [ ] Run `.\scripts\pre-demo-checklist.ps1`
- [ ] `terraform destroy` after demo window

```powershell
.\scripts\pre-demo-checklist.ps1
```
