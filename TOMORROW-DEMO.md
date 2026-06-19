# Tomorrow Demo - Tonight Checklist

## Tonight (60 min)

### 1. Start platform (10 min)
```powershell
cd D:\enlight-lab-platform
.\scripts\free-ram-for-demo.ps1
.\scripts\go-live.bat
.\scripts\test-all.bat
```

### 2. Demo 4 baseline ONCE (first time only, 5 min)
```powershell
.\demos\demo4-drift-cost\scripts\run-demo.ps1 -Phase baseline
```

### 3. Full rehearsal (20 min)
```powershell
.\run-tomorrow-demo.bat
```

### 4. Open browser tabs (keep open)
- http://localhost:30800/health
- http://localhost:8082
- http://localhost:3000
- https://github.com/kirtiprasad2003/enlight-lab-platform/actions
- https://platform.robusta.dev

---

## Tomorrow morning (30 min before)

```powershell
.\scripts\go-live.bat
.\scripts\test-all.bat
```

Check all 5 tabs load.

---

## During meeting

Run: `.\run-tomorrow-demo.bat` OR paste commands to Cursor.

### Cursor commands (optional)
```
Dispatch chat-to-deploy non-compliant on kirtiprasad2003/enlight-lab-platform main
Dispatch chat-to-deploy compliant on kirtiprasad2003/enlight-lab-platform main
Run demo 1 end to end
```

---

## Demo order (20 min)

| Part | Demo | What to show |
|------|------|--------------|
| 0 | Intro | Two PoCs -> one platform |
| 1 | Live | /health + ArgoCD |
| 2 | Demo 2 | BLOCK violations |
| 3 | Demo 2 | PASS |
| 4 | Demo 1 | break -> AI -> heal |
| 5 | Demo 4 | drift -> reconcile |
| 6 | Demo 5 | PR blocked -> pass |
| 7 | Demo 3 | scaffold new service |

---

## If something breaks

| Problem | Fix |
|---------|-----|
| /health fails | `go-live.bat` |
| ArgoCD blank | `fix-dashboards.bat` |
| Demo 4 fails | run `baseline` again first |
| App broken | `heal-rollback.ps1` |

---

## What to say if asked "complete?"

> Foundation and all five demos are runnable locally. Demos 1 and 2 are production-depth; 3-5 are demonstrated via local scripts on the same platform. Remaining work is polish and automation, not architecture.
