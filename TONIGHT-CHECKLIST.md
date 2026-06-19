# Tonight / Before 12PM — Do in this order

## 1. Go live (10-15 min)

```powershell
cd D:\enlight-lab-platform
.\scripts\go-live.ps1
```

Or double-click: **go-live.bat**

## 2. Test everything

```powershell
.\scripts\test-all.ps1
```

All should say **PASS**. Or double-click: **test-all.bat**

## 3. Rehearse demo twice

```powershell
.\scripts\run-12pm-demo.ps1
```

Or: **run-12pm-demo.bat**

## 4. YOU connect MCPs (15 min)

Read: **docs\MCP-CONNECT-GUIDE.md**

- GitHub token + push repo
- Claude Desktop config
- Restart Claude

## 5. Before 12PM (30 min early)

```powershell
.\scripts\go-live.ps1
.\scripts\test-all.ps1
```

Open tab: http://localhost:30800/health

## 6. If broken

```powershell
.\scripts\force-cleanup-docker.ps1
.\scripts\repair-cluster.ps1
```

## What works without MCP

- Live app /health
- Full pipeline block/pass
- Kyverno live block
- Demo 1 failure + rollback scripts

## What needs MCP (you connect)

- Claude triggers GitHub Actions
- AI explains failures (k8sgpt/HolmesGPT)
