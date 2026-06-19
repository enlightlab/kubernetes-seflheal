# Enlight Lab — Manager Demo Cheat Sheet

**Date:** Tomorrow's presentation  
**Style:** Browser UI only — paste commands to Cursor (AI runs them behind the scenes)  
**Project:** `D:\enlight-lab-platform`  
**Repo:** https://github.com/kirtiprasad2003/enlight-lab-platform

---

## Before the meeting (you only — hide terminal)

Run once, 15–30 minutes before the call. Minimize the PowerShell window.

```powershell
cd D:\enlight-lab-platform
.\scripts\go-live.bat
```

**Quick check:** Open http://localhost:30800/health — must show `{"status":"ok"}`  
If connection refused → run `go-live.bat` again.

---

## Browser tabs to open (share screen on these)

| # | URL | Login / notes |
|---|-----|----------------|
| 1 | http://localhost:30800 | Enlight Lab dashboard (main visual) |
| 2 | http://localhost:30800/health | JSON health check |
| 3 | http://localhost:8082 | ArgoCD → open app **fastapi-staging** |
| 4 | http://localhost:3000 | Grafana — user `admin`, password `enlight-admin` |
| 5 | https://github.com/kirtiprasad2003/enlight-lab-platform/actions | GitHub Actions tab |
| 6 | https://platform.robusta.dev | Robusta incidents (optional) |

**Do NOT share:** PowerShell, kubectl, or Cursor terminal.

---

## Opening line (30 seconds)

> "You saw two earlier PoCs — delivery and policy separately. **Enlight Lab unifies them** on one platform: pipeline, policy gates, GitOps, and monitoring. Today it runs at **zero cloud cost** locally; production uses the same design on EKS."

---

## Part 1 — Live platform (browser only, ~2 min)

**Show:**
1. http://localhost:30800 — dashboard UI
2. http://localhost:8082 — ArgoCD, app **fastapi-staging** green / Synced
3. http://localhost:30800/health — `{"status":"ok"}`

**Say:**
> "The app is live on Kubernetes. ArgoCD keeps it in sync with Git. Health checks pass."

**Cursor command:** None — browser only.

---

## Part 2 — Demo 2: BLOCK bad deploy (~2 min)

**What happens:** CI policy gate rejects a bad manifest before it reaches the cluster.

**Paste to Cursor:**
```
Dispatch chat-to-deploy non-compliant on kirtiprasad2003/enlight-lab-platform main
```

**Show in browser:** GitHub Actions → workflow **Chat to Deploy** → latest run → **red / failed** on **policy-check** step.

**Look for:** Violation messages (forbidden `:latest` tag, unapproved registry, missing CPU/memory limits).

**Say:**
> "A bad configuration is stopped in CI — wrong image, no resource limits, forbidden tags. It never touches production."

---

## Part 3 — Demo 2: PASS good deploy (~2 min)

**Paste to Cursor:**
```
Dispatch chat-to-deploy compliant on kirtiprasad2003/enlight-lab-platform main
```

**Show in browser:**
1. GitHub Actions → **green** run, all jobs pass
2. ArgoCD → still healthy
3. http://localhost:30800/health → still `ok`

**Say:**
> "Compliant config passes policy and the app stays healthy. This is what we deploy."

---

## Part 4 — Demo 1: Incident response (~4 min)

### Step 4a — Break the app (on purpose)

**Paste to Cursor:**
```
Run demo 1: inject failure on fastapi in enlight-staging
```

**Show in browser:**
- ArgoCD → pod turns **red** / degraded / OutOfSync
- Robusta (if alert appears) → incident notification

**Say:**
> "Something broke in the cluster — on purpose for the demo. In production this could be a bad image push or config drift."

### Step 4b — AI explains (do not fix yet)

**Paste to Cursor:**
```
Use k8sgpt to explain the fastapi failure in enlight-staging
```

**Alternative if k8sgpt unavailable:**
```
Explain why the fastapi pod is failing in enlight-staging namespace
```

**Say:**
> "AI diagnoses the incident — ImagePullBackOff, bad image tag, etc. Explanation is separate from the fix."

### Step 4c — Heal / rollback

**Paste to Cursor:**
```
Heal demo 1 — rollback fastapi to last good image in enlight-staging
```

**Show in browser:**
- ArgoCD → recovers to green
- http://localhost:30800/health → `ok` again

**Say:**
> "GitOps rolls back to the last known good version. AI explains; ArgoCD fixes."

---

## Part 5 — Observability (~1 min)

**Show:** http://localhost:3000 (Grafana)

**Say:**
> "Prometheus and Grafana give us SLOs and metrics — this feeds rollback decisions and Demo 1 automation in production."

**Cursor command:** None.

---

## Part 6 — Demo 5: PR compliance (optional, ~2 min)

**Best:** Show a real PR with failed then fixed checks.

**Paste to Cursor:**
```
Show me the latest PR compliance workflow run on enlight-lab-platform
```

**Or create demo PR:**
```
Create a demo PR with non-compliant sample files for demo 5, then show the failed checks
```

**Show in browser:** GitHub PR → Checks tab → blocked vs passed.

**Say:**
> "Every pull request is scanned for secrets and insecure infrastructure — bad PRs cannot merge."

**If no PR ready:** Skip UI and say: "Same compliance bot runs on every PR in GitHub."

---

## Part 7 — Demo 3: Internal developer platform (~1 min)

**Paste to Cursor:**
```
Run demo 3 scaffold demo-api and show me what was created
```

**Show:** Folder `workload/scaffolded/demo-api` in repo (catalog-info.yaml, k8s manifests with limits).

**Say:**
> "In production this is Backstage — one click scaffolds a compliant service bundle: catalog entry, Kubernetes manifests, monitoring hooks."

---

## Part 8 — Demo 4: Drift & cost (mention only, ~30 sec)

**No browser UI for this demo** — mention briefly:

> "Demo 4 watches cloud infrastructure. If someone changes AWS outside Terraform, we detect drift, estimate cost impact, and reconcile back to Git. We run that locally with a simulated AWS."

**Skip unless asked.**

---

## Closing (~30 sec)

> "Five demos on one platform: CI policy, GitOps, monitoring, AI incident response, and PR compliance. Demos 1 and 2 are production-depth today; 3–5 use the same foundation locally. Zero cloud cost now — same architecture on EKS when we go to production."

---

## Full command list (copy-paste order)

```
# Part 1 — browser only (dashboard + ArgoCD + /health)

# Part 2 — BLOCK
Dispatch chat-to-deploy non-compliant on kirtiprasad2003/enlight-lab-platform main

# Part 3 — PASS
Dispatch chat-to-deploy compliant on kirtiprasad2003/enlight-lab-platform main

# Part 4 — Incident
Run demo 1: inject failure on fastapi in enlight-staging
Use k8sgpt to explain the fastapi failure in enlight-staging
Heal demo 1 — rollback fastapi to last good image in enlight-staging

# Part 5 — Grafana (browser only)

# Part 6 — PR compliance (optional)
Show me the latest PR compliance workflow run on enlight-lab-platform

# Part 7 — IDP scaffold
Run demo 3 scaffold demo-api and show me what was created
```

---

## If something breaks

| Problem | Fix (you run privately) |
|---------|-------------------------|
| http://localhost:30800 connection refused | `.\scripts\go-live.bat` |
| ArgoCD blank / error | `.\scripts\fix-dashboards.ps1` then `go-live.bat` |
| App broken after Demo 1 | Paste: `Heal demo 1 — rollback fastapi` |
| Dashboard shows JSON not HTML | Rebuild UI (see below) |
| GitHub Actions not triggering | Check internet; paste dispatch command again |

**Rebuild dashboard UI (private, before demo):**
```powershell
cd D:\enlight-lab-platform
docker build -t enlight-fastapi:demo-pass workload/fastapi
kind load docker-image enlight-fastapi:demo-pass --name enlight-lab
kubectl rollout restart deployment/fastapi -n enlight-staging
.\scripts\go-live.bat
```

---

## What to say if asked "Is it complete?"

> "Foundation and all five demos are runnable locally. Demos 1 and 2 are production-depth; 3–5 are demonstrated on the same platform. Remaining work is polish and automation, not architecture."

---

## Likely manager questions

**Q: Why not AWS today?**  
A: Local kind = $0 while building. EKS Terraform is ready; we apply only for production demo windows.

**Q: What does the AI actually do?**  
A: Explains incidents (k8sgpt/Holmes). GitOps (ArgoCD) performs the rollback — always separate steps.

**Q: What is Floci?**  
A: Local simulated AWS for Demo 4 drift detection — not used for Kubernetes.

---

*Enlight Lab — Manager Demo Cheat Sheet — generated for local kind presentation*
