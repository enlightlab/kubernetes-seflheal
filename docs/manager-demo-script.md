# Manager Demo Script (~12 min)

## Before the meeting

```powershell
cd D:\enlight-lab-platform
.\scripts\start-platform.ps1
```

Open tabs:

- http://localhost:30800/health
- http://localhost:3000 (Grafana)
- http://localhost:8080 (ArgoCD)

---

## Opening (30 sec)

> "You saw two earlier PoCs — delivery and policy separately. **Enlight Lab unifies them** on one platform: pipeline, policy gates, GitOps, and monitoring. Today it runs at **zero cloud cost** locally; production uses the same design on EKS."

---

## Part 1 — Policy block (2 min)

```powershell
.\demos\demo2-chat-to-deploy\scripts\run-demo.ps1 -Variant non-compliant
```

> "This is Demo 2's CI policy gate. Bad manifests are blocked **before** they touch the cluster. You see exactly why: missing limits, wrong registry, forbidden latest tag."

---

## Part 2 — Policy pass (1 min)

```powershell
.\demos\demo2-chat-to-deploy\scripts\run-demo.ps1 -Variant compliant
```

> "Compliant config passes — that's what we deploy."

---

## Part 3 — Live app (2 min)

Open http://localhost:30800/health

> "FastAPI is running on Kubernetes. Automated health validation."

```powershell
kubectl get pods -n enlight-staging
```

---

## Part 4 — Cluster policy (2 min)

```powershell
kubectl apply -f foundation\policies\kyverno\test-violation-pod.yaml
```

> "Same rules at the cluster door — Kyverno admission control. Defense in depth: CI **and** cluster."

---

## Part 5 — Observability (2 min)

Grafana http://localhost:3000 — admin / enlight-admin

> "Prometheus + Grafana for SLOs — feeds Demo 1 rollback triggers."

ArgoCD http://localhost:8080

> "ArgoCD GitOps with self-heal — our rollback engine. AI explains incidents; **ArgoCD fixes** — always separate steps."

---

## Outcomes (1 min)

1. Policy blocks bad deploys with human-readable reasons  
2. Compliant workload live and healthy  
3. GitOps + monitoring running  
4. Clear path to EKS + Claude-triggered pipeline  
5. Five-demo roadmap on one foundation  

---

## Likely questions

**Q: Why not AWS today?**  
A: Local kind = $0 while building. EKS Terraform is ready; we apply only for demo windows.

**Q: What about Floci?**  
A: For Demo 4 AWS drift (S3 etc.), not for Kubernetes.

**Q: What's next?**  
A: Push to GitHub, Claude MCP triggers Actions, Demo 1 failure + auto-rollback.

---

## Closing

> "Enlight Lab is Phase 2 after the PoCs — integrated, scripted, and demo-ready. Foundation works today; EKS is the production switch."
