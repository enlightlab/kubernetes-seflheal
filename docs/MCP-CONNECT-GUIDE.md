# MCP Connect Guide — YOU do this (15 min)

After `go-live.ps1` works, connect these in **Claude Desktop**.

Config file (Windows):
```
%APPDATA%\Claude\claude_desktop_config.json
```

---

## 1. GitHub MCP (Demo 2 — Chat-to-Deploy)

**Create token:** GitHub → Settings → Developer settings → Personal access tokens  
**Scopes:** `repo`, `workflow`

```json
"github": {
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-github"],
  "env": {
    "GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_YOUR_TOKEN"
  }
}
```

**Push repo first:**
```powershell
cd D:\enlight-lab-platform
git add .
git commit -m "Enlight Lab demo ready"
git remote add origin https://github.com/YOUR_USER/enlight-lab-platform.git
git push -u origin main
```

**Test in Claude:**
```
List workflows in repo YOUR_USER/enlight-lab-platform
```

**Trigger pipeline:**
```
Dispatch workflow chat-to-deploy.yml on enlight-lab-platform branch main with inputs variant=non-compliant image_tag=demo-pass
```

---

## 2. k8sgpt MCP (Demo 1 — diagnose only)

Install k8sgpt: https://docs.k8sgpt.dev/

```json
"k8sgpt": {
  "command": "k8sgpt",
  "args": ["serve", "--mcp"]
}
```

Ensure kubectl context: `kind-enlight-lab`

**Test in Claude after inject-failure.ps1:**
```
Use k8sgpt to analyze pods in enlight-staging namespace and explain the failure in plain English
```

---

## 3. HolmesGPT MCP (Demo 1 — RCA)

Follow HolmesGPT MCP install docs for your version.

**Test:**
```
Investigate the fastapi deployment failure in enlight-staging. Read-only analysis only.
```

---

## 4. Full claude_desktop_config.json example

```json
{
  "mcpServers": {
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {
        "GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_XXXX"
      }
    }
  }
}
```

Add k8sgpt/Holmes when installed. **Restart Claude Desktop** after edits.

---

## 5. Demo flow with MCP connected

| Step | Who acts |
|------|----------|
| "Deploy feature branch to staging" | Claude → GitHub Actions |
| Policy fails | GitHub log shows OPA reason |
| Failure injected | k8sgpt/Holmes explain |
| Rollback | You run `heal-rollback.ps1` (stands in for ArgoCD auto-heal until SLO wired) |

---

## 6. Tonight checklist

- [ ] `.\scripts\go-live.ps1` — all PASS
- [ ] `.\scripts\test-all.ps1` — all PASS
- [ ] GitHub repo pushed
- [ ] GitHub MCP green in Claude
- [ ] Rehearse `.\scripts\run-12pm-demo.ps1` twice
- [ ] Record screen backup of each part
