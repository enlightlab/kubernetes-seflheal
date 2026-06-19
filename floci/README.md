# Floci — Local Cloud Sandbox (Demo 4)



Powers **Cloud config guard** on Demo Control (`http://localhost:30900`).



**Do NOT open `:4566` in a browser** — it is an API only. **Do NOT use for EKS** — Kubernetes runs on `kind`.



## Start



```powershell

cd D:\enlight-lab-platform\floci

.\start-floci-stack.ps1

```



Or it starts automatically when you run `.\scripts\go-live.bat`.



## Client demo



Use **Demo Control only** (`http://localhost:30900`):



1. **Set secure baseline**

2. **Catch config drift**

3. **Fix drift automatically**



## Stop



```powershell

cd D:\enlight-lab-platform\floci

docker compose down

```


