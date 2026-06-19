# START HERE (simplest path)

## Your cluster is broken if you see:
- `enlight-lab-worker NotReady`
- `kind load` hangs
- Kyverno webhook errors

## Fix in ONE step

Double-click: **`repair-cluster.bat`**

Or PowerShell:

```powershell
cd D:\enlight-lab-platform
.\scripts\repair-cluster.ps1
```

Wait 10-15 minutes. Then open: http://localhost:30800/health

---

## Normal start (when cluster is healthy)

```powershell
.\scripts\start-after-reboot.ps1
```

---

## Quick demo without cluster

```powershell
.\demos\demo2-chat-to-deploy\scripts\run-demo.ps1 -Variant non-compliant
.\demos\demo2-chat-to-deploy\scripts\run-demo.ps1 -Variant compliant
```
