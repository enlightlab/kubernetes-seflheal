# Simple Demo Guide — What Everything Means

## What is this project?

A **fake mini cloud on your laptop** that:
1. Runs a small app (FastAPI)
2. **Blocks bad deploys** (security rules)
3. **Allows good deploys**
4. Can **break and fix** the app (Demo 1)

**No AWS money. No kubectl on screen for manager** — you run scripts.

---

## The 3 buttons you need

| Button | When |
|--------|------|
| `go-live.bat` | **Before demo** — starts everything |
| `run-12pm-demo.bat` | **During demo** — walks you step by step |
| `test-all.bat` | **Check** — all should say PASS |

---

## What each PART means (your demo)

### PART 0 — Opening
**You say:** "This is Enlight Lab — one platform for five DevOps demos. Runs locally at zero cloud cost."

### PART 1 — Live app
**Open browser:** http://localhost:30800/health  
**Should show:** `{"status":"ok"}`  
**You say:** "The app is live on Kubernetes."

If it fails → run `go-live.bat` first.

### PART 2 — Bad deploy BLOCKED
**What happens:** Script shows red **VIOLATION** lines  
**You say:** "A bad configuration is stopped before it reaches the cluster — wrong image, no limits, latest tag forbidden."

**This is SUCCESS** — blocking is the point!

### PART 3 — Good deploy PASS
**What happens:** `PIPELINE SUCCESS` and `/health` ok  
**You say:** "A compliant deploy passes policy and the app stays healthy."

### PART 4 — Kyverno
**What happens:** May create a pod OR show denied error  
**You say:** "Same rules enforced at the cluster door."

### PART 5 — Break and fix (optional)
1. `inject-failure.ps1` — app breaks (bad image)
2. **You say:** "AI explains the failure" (or skip if no MCP)
3. `heal-rollback.ps1` — app healthy again
4. **You say:** "System rolled back to last good version."

---

## What you already did successfully

From your last run:
- PART 2: BLOCKED bad deploy (VIOLATIONS shown) ✓
- PART 3: PASS + health ok ✓
- PART 4: Kyverno test ran ✓
- PART 5: Failure injected (timeout message is OK — pod is broken on purpose)

**You are ready for the manager.**

---

## If confused — only remember this

```
go-live     = turn everything on
run-12pm    = your script on stage
/health ok  = app works
VIOLATION   = good (bad deploy stopped)
PASS        = good (good deploy allowed)
heal        = fix broken app
```

---

## One problem from your run

"Run go-live first" on PART 1 but later health worked = port-forward was slow.  
**Fix:** Always run `go-live.bat` 10 minutes before demo.

Demo 1 timeout = **normal** — app is supposed to fail. Run `heal-rollback.ps1` after.
