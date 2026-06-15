"""Demo action runners - host-side only (kubectl, GitHub, local scripts)."""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEMO3 = ROOT / "demos" / "demo3-backstage-idp" / "scripts"
DEMO4 = ROOT / "demos" / "demo4-drift-cost" / "scripts"
DEMO5 = ROOT / "demos" / "demo5-pr-compliance" / "scripts"
FLOCI_STACK = ROOT / "floci" / "start-floci-stack.ps1"
REPO = "kirtiprasad2003/enlight-lab-platform"
CTX = "kind-enlight-lab"
_floci_ensure_busy = False
_floci_last_ensure = 0.0


def _run(
    cmd: list[str],
    cwd: Path | None = None,
    timeout: int = 180,
    env: dict[str, str] | None = None,
) -> tuple[int, str]:
    try:
        p = subprocess.run(
            cmd,
            cwd=cwd or ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        out = (p.stdout or "") + (p.stderr or "")
        return p.returncode, out.strip()
    except subprocess.TimeoutExpired:
        return 1, "Command timed out"
    except Exception as e:
        return 1, str(e)


def _powershell(script: str, timeout: int = 180) -> tuple[int, str]:
    return _run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        timeout=timeout,
    )


def _kubectl(*args: str) -> tuple[int, str]:
    return _run(["kubectl", "--context", CTX, *args])


def _app_url_reachable(url: str = "http://localhost:30800/health", timeout: int = 3) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def _ensure_port_forward() -> None:
    """Restart host port-forwards; inject/heal often kills the :30800 tunnel."""
    script = ROOT / "scripts" / "port-forward-all.ps1"
    _run(["powershell", "-NoProfile", "-File", str(script)], timeout=60)
    for _ in range(5):
        time.sleep(2)
        if _app_url_reachable():
            return


def _github_token() -> str:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        return token
    mcp = Path.home() / ".cursor" / "mcp.json"
    if mcp.exists():
        data = json.loads(mcp.read_text(encoding="utf-8"))
        return data["mcpServers"]["github"]["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"]
    raise RuntimeError("GitHub token not found (set GITHUB_TOKEN or configure mcp.json)")


def _github_request(method: str, path: str, body: dict | None = None) -> dict:
    token = _github_token()
    url = f"https://api.github.com{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read().decode().strip()
        if not raw:
            return {"status": resp.status, "message": "ok"}
        return json.loads(raw)


def _poll_workflow(workflow_file: str, wait_s: int = 60) -> dict:
    time.sleep(8)
    runs = _github_request("GET", f"/repos/{REPO}/actions/workflows/{workflow_file}/runs?per_page=1")
    run = runs["workflow_runs"][0]
    run_id = run["id"]
    deadline = time.time() + wait_s
    while time.time() < deadline:
        run = _github_request("GET", f"/repos/{REPO}/actions/runs/{run_id}")
        if run["status"] == "completed":
            break
        time.sleep(3)
    jobs = _github_request("GET", f"/repos/{REPO}/actions/runs/{run_id}/jobs")
    return {
        "url": run["html_url"],
        "conclusion": run.get("conclusion"),
        "jobs": [{"name": j["name"], "conclusion": j["conclusion"]} for j in jobs["jobs"]],
    }


def platform_status() -> dict:
    checks: dict[str, str] = {}
    _, pod_ready = _kubectl(
        "get", "pods", "-n", "enlight-staging", "-l", "app=fastapi",
        "-o", "jsonpath={.items[0].status.containerStatuses[0].ready}",
    )
    if pod_ready == "true" and not _app_url_reachable():
        _ensure_port_forward()

    for name, url in [
        ("dashboard", "http://localhost:30800/"),
        ("health", "http://localhost:30800/health"),
        ("idp", "http://localhost:30800/idp"),
        ("argocd", "http://localhost:8082"),
        ("grafana", "http://localhost:3000/login"),
    ]:
        try:
            urllib.request.urlopen(url, timeout=5)
            checks[name] = "ok"
        except Exception:
            checks[name] = "fail"
    _, out = _kubectl("get", "application", "fastapi-staging", "-n", "argocd",
                      "-o", "jsonpath={.status.sync.status}/{.status.health.status}")
    checks["fastapi_staging"] = out or "unknown"
    _, out2 = _kubectl("get", "application", "demo-api", "-n", "argocd",
                       "-o", "jsonpath={.status.sync.status}/{.status.health.status}")
    checks["demo_api"] = out2 or "n/a"
    return checks


def demo2_dispatch(variant: str) -> dict:
    _github_request(
        "POST",
        f"/repos/{REPO}/actions/workflows/chat-to-deploy.yml/dispatches",
        {"ref": "main", "inputs": {"variant": variant, "image_tag": "demo-pass"}},
    )
    result = _poll_workflow("chat-to-deploy.yml")
    result["variant"] = variant
    result["dispatched"] = True
    result["open_url"] = result.get("url") or f"https://github.com/{REPO}/actions"
    if result.get("conclusion") is None:
        result["conclusion"] = "in_progress"
        result["message"] = "Workflow running on GitHub - open link to watch"
    return result


def demo1_inject() -> dict:
    patch = '{"spec":{"syncPolicy":{"automated":{"selfHeal":false}}}}'
    patch_file = Path(os.environ.get("TEMP", "/tmp")) / "argocd-patch.json"
    patch_file.write_text(patch, encoding="utf-8")
    _kubectl("patch", "application", "fastapi-staging", "-n", "argocd", "--type", "merge",
             f"--patch-file={patch_file}")
    _kubectl("set", "image", "deployment/fastapi", "api=enlight-fastapi:DOES-NOT-EXIST", "-n", "enlight-staging")
    time.sleep(12)
    _, pods = _kubectl("get", "pods", "-n", "enlight-staging", "-l", "app=fastapi", "--no-headers")
    _, argo = _kubectl("get", "application", "fastapi-staging", "-n", "argocd",
                       "-o", "jsonpath={.status.sync.status}/{.status.health.status}")
    return {
        "pods": pods,
        "argocd": argo,
        "open_url": "http://localhost:8082/applications/argocd/fastapi-staging",
    }


def demo1_explain() -> dict:
    _, out = _run(["k8sgpt", "analyze", "--namespace", "enlight-staging"], timeout=60)
    findings = []
    for line in out.splitlines():
        if "Error:" in line or "Pod enlight" in line or "Service enlight" in line:
            findings.append(line.strip())
    return {"findings": findings[:6], "raw": out[-2000:]}


def demo1_heal() -> dict:
    overlay = ROOT / "demos" / "demo2-chat-to-deploy" / "overlays" / "local"
    _run(["kubectl", "--context", CTX, "apply", "-k", str(overlay)], timeout=60)
    _kubectl("set", "image", "deployment/fastapi", "api=enlight-fastapi:demo-pass", "-n", "enlight-staging")
    _kubectl("rollout", "restart", "deployment/fastapi", "-n", "enlight-staging")
    _kubectl("rollout", "status", "deployment/fastapi", "-n", "enlight-staging", "--timeout=90s")
    patch_file = Path(os.environ.get("TEMP", "/tmp")) / "argocd-heal.json"
    patch_file.write_text('{"spec":{"syncPolicy":{"automated":{"selfHeal":true}}}}', encoding="utf-8")
    _kubectl("patch", "application", "fastapi-staging", "-n", "argocd", "--type", "merge",
             f"--patch-file={patch_file}")
    time.sleep(5)
    _ensure_port_forward()
    _, argo = _kubectl("get", "application", "fastapi-staging", "-n", "argocd",
                       "-o", "jsonpath={.status.sync.status}/{.status.health.status}")
    health = "unreachable"
    if _app_url_reachable(timeout=5):
        with urllib.request.urlopen("http://localhost:30800/health", timeout=5) as r:
            health = r.read().decode()
    return {
        "argocd": argo,
        "health": health,
        "app_reachable": health != "unreachable",
        "open_url": "http://localhost:30800/health",
        "message": "App recovered" if health != "unreachable" else "Pod OK but run Refresh to restore browser tunnel",
    }


def _floci_reachable() -> bool:
    code, _ = _run(
        ["aws", "s3", "ls", "--endpoint-url", "http://localhost:4566"],
        env=_demo4_env(),
        timeout=8,
    )
    return code == 0


def _ensure_floci_stack() -> bool:
    """Start local cloud sandbox if Demo 4 backend is offline."""
    global _floci_ensure_busy, _floci_last_ensure
    if _floci_reachable():
        return True
    now = time.time()
    if _floci_ensure_busy or (now - _floci_last_ensure < 20):
        return False
    if not FLOCI_STACK.exists():
        return False
    _floci_ensure_busy = True
    _floci_last_ensure = now
    try:
        code, _ = _run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(FLOCI_STACK)],
            timeout=300,
        )
        return code == 0 and _floci_reachable()
    finally:
        _floci_ensure_busy = False


def _demo4_client_hint(floci_ok: bool, actual_acl: str, in_sync: bool, status: str) -> str:
    if not floci_ok:
        return "Cloud environment is starting — click Set secure baseline in a moment."
    if actual_acl == "unknown":
        return "Click Set secure baseline to begin this demo."
    if status == "drifted":
        return "Unauthorized change detected — click Fix drift automatically."
    if in_sync:
        return "All good — click Catch config drift to show what happens when someone changes settings."
    return "Click Set secure baseline to begin."


def _demo4_env() -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "AWS_ENDPOINT_URL": "http://localhost:4566",
            "AWS_DEFAULT_REGION": "us-east-1",
            "AWS_ACCESS_KEY_ID": "test",
            "AWS_SECRET_ACCESS_KEY": "test",
        }
    )
    return env


def _demo4_bucket_acl() -> str:
    code, out = _run(
        [
            "aws",
            "s3api",
            "get-bucket-acl",
            "--bucket",
            "enlight-demo",
            "--endpoint-url",
            "http://localhost:4566",
            "--output",
            "json",
        ],
        env=_demo4_env(),
        timeout=20,
    )
    if code != 0:
        return "unknown"
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return "unknown"
    for grant in data.get("Grants", []):
        uri = grant.get("Grantee", {}).get("URI", "")
        if uri.endswith("AllUsers"):
            return "public-read"
    return "private"


def demo4_infrastructure_status(*, auto_start: bool = True) -> dict:
    if auto_start and not _floci_reachable():
        _ensure_floci_stack()
    desired_acl = "private"
    actual_acl = _demo4_bucket_acl()
    in_sync = actual_acl == desired_acl
    floci_code, _ = _run(
        ["aws", "s3", "ls", "--endpoint-url", "http://localhost:4566"],
        env=_demo4_env(),
        timeout=15,
    )
    floci_ok = floci_code == 0
    status = "in_sync"
    violation = None
    cost_delta = 0
    if actual_acl == "public-read":
        status = "drifted"
        violation = "Storage was changed to public outside the approved Git process"
        cost_delta = 15
    elif actual_acl == "unknown":
        status = "unknown"

    return {
        "bucket": "enlight-demo",
        "provider": "Local cloud sandbox (no AWS charges)",
        "floci_ok": floci_ok,
        "desired_acl": desired_acl,
        "actual_acl": actual_acl,
        "encryption": "AES256 (required by Terraform)",
        "in_sync": in_sync,
        "status": status,
        "violation": violation,
        "cost_delta_usd": cost_delta,
        "terraform_source": "foundation/terraform/demo4/main.tf",
        "client_hint": _demo4_client_hint(floci_ok, actual_acl, in_sync, status),
        "demo_ready": floci_ok and actual_acl != "unknown",
    }


def demo4_run(phase: str) -> dict:
    script = DEMO4 / "run-demo.ps1"
    code, out = _run(
        ["powershell", "-NoProfile", "-File", str(script), "-Phase", phase],
        timeout=120,
    )
    drift = "DRIFT DETECTED" in out
    reconciled = "RECONCILED" in out
    infra = demo4_infrastructure_status()
    plan_snippet = ""
    if "Terraform plan" in out or "terraform plan" in out.lower():
        for line in out.splitlines():
            if any(k in line for k in ("#", "acl", "public", "private", "will be", "must be")):
                plan_snippet += line + "\n"
        plan_snippet = plan_snippet.strip()[:800]

    result = {
        "phase": phase,
        "ok": code == 0,
        "drift": drift,
        "reconciled": reconciled,
        "log": out[-2500:],
        **infra,
    }
    if plan_snippet:
        result["terraform_plan"] = plan_snippet
    if drift:
        result["message"] = "Unauthorized cloud change detected — review the panel above"
    if reconciled:
        result["message"] = "Settings restored — cloud now matches Git"
    return result


def _poll_pr_checks(pr_number: int, wait_s: int = 90) -> dict:
    deadline = time.time() + wait_s
    conclusion = "in_progress"
    check_url = f"https://github.com/{REPO}/pull/{pr_number}/checks"
    while time.time() < deadline:
        pr = _github_request("GET", f"/repos/{REPO}/pulls/{pr_number}")
        head_sha = pr["head"]["sha"]
        checks = _github_request("GET", f"/repos/{REPO}/commits/{head_sha}/check-runs?per_page=20")
        runs = checks.get("check_runs", [])
        if runs:
            states = {r.get("conclusion") or r.get("status") for r in runs}
            if "in_progress" not in states and "queued" not in states and "pending" not in states:
                conclusion = "failure" if "failure" in states or "cancelled" in states else "success"
                break
        time.sleep(4)
    return {"conclusion": conclusion, "check_url": check_url, "pr_number": pr_number}


def demo5_status() -> dict:
    runs = _github_request("GET", f"/repos/{REPO}/actions/workflows/pr-compliance.yml/runs?per_page=5")
    items = [
        {
            "url": r["html_url"],
            "conclusion": r["conclusion"],
            "created": r["created_at"],
            "branch": r.get("head_branch"),
        }
        for r in runs.get("workflow_runs", [])
    ]
    open_url = f"https://github.com/{REPO}/actions/workflows/pr-compliance.yml"
    prs = _github_request("GET", f"/repos/{REPO}/pulls?state=open&per_page=5")
    demo_prs = [p for p in prs if p.get("head", {}).get("ref", "").startswith("demo5/")]
    if demo_prs:
        open_url = f"{demo_prs[0]['html_url']}/checks"
    return {"runs": items, "open_url": open_url, "message": "Use Block/Pass buttons for a fresh PR run"}


def demo5_pr(variant: str) -> dict:
    script = DEMO5 / "create-pr.ps1"
    code, out = _run(
        ["powershell", "-NoProfile", "-File", str(script), "-Variant", variant],
        timeout=180,
    )
    pr_url = None
    pr_number = None
    m = re.search(r"https://github.com/\S+/pull/(\d+)", out)
    if m:
        pr_url = m.group(0)
        pr_number = int(m.group(1))
    if code != 0 and not pr_number:
        raise RuntimeError(out[-1500:] or f"create-pr failed (exit {code})")
    result: dict = {
        "variant": variant,
        "dispatched": True,
        "open_url": pr_url or f"https://github.com/{REPO}/pulls",
        "log": out[-2000:],
    }
    if pr_number:
        polled = _poll_pr_checks(pr_number)
        result.update(polled)
        result["open_url"] = polled["check_url"]
        if polled["conclusion"] == "in_progress":
            result["message"] = "PR created — checks still running on GitHub"
    return result


def _idp_last_service() -> str | None:
    f = ROOT / "workload" / "scaffolded" / ".last-idp-service"
    if f.exists():
        return f.read_text(encoding="utf-8").strip() or None
    return None


def demo3_catalog() -> dict:
    scaffolded = ROOT / "workload" / "scaffolded"
    services: list[dict] = []
    if scaffolded.exists():
        for d in sorted(scaffolded.iterdir()):
            if not d.is_dir() or d.name.startswith("."):
                continue
            name = d.name
            _, argo = _kubectl(
                "get", "application", name, "-n", "argocd",
                "-o", "jsonpath={.status.sync.status}/{.status.health.status}",
            )
            services.append({"name": name, "argocd": argo or "not deployed"})
    last = _idp_last_service()
    return {"last_service": last, "services": services}


def demo3_run(phase: str, service: str = "auto") -> dict:
    script = DEMO3 / "run-demo.ps1"
    if phase in ("runbook", "investigate"):
        ps = DEMO3 / ("runbook-pr.ps1" if phase == "runbook" else "investigate-pr.ps1")
        code, out = _run(
            ["powershell", "-NoProfile", "-File", str(ps), "-ServiceName", service]
            + (["-Action", "scale-up"] if phase == "runbook" else []),
            timeout=180,
        )
    elif phase == "pr":
        code, out = _run(
            ["powershell", "-NoProfile", "-File", str(DEMO3 / "create-pr.ps1"), "-ServiceName", service],
            timeout=180,
        )
    else:
        code, out = _run(
            ["powershell", "-NoProfile", "-File", str(script), "-Phase", phase, "-ServiceName", service],
            timeout=180,
        )
    pr_url = None
    pr_number = None
    m = re.search(r"https://github.com/\S+/pull/(\d+)", out)
    if m:
        pr_url = m.group(0)
        pr_number = int(m.group(1))
    svc_match = re.search(r"SERVICE_NAME:\s*(\S+)", out)
    resolved = svc_match.group(1) if svc_match else (_idp_last_service() or service)
    if code != 0 and not pr_number:
        raise RuntimeError(out[-1500:] or f"Demo 3 {phase} failed (exit {code})")

    already_live = "already live" in out.lower() or "live via gitops" in out.lower()
    _, argo = _kubectl(
        "get", "application", resolved, "-n", "argocd",
        "-o", "jsonpath={.status.sync.status}/{.status.health.status}",
    )
    catalog = demo3_catalog()
    result: dict = {
        "phase": phase,
        "ok": code == 0,
        "log": out[-2500:],
        "service": resolved,
        "argocd": argo or "n/a",
        "already_live": already_live,
        "catalog": catalog["services"],
        "open_url": pr_url or f"http://localhost:8082/applications/argocd/{resolved}",
    }
    if pr_number:
        result["pr_number"] = pr_number
        result["open_url"] = f"{pr_url}/checks"
        result["message"] = f"PR #{pr_number} for {resolved} — merge then Deploy"
    elif phase == "scaffold":
        result["message"] = f"New service {resolved} — next: Create PR"
    elif phase == "deploy":
        result["message"] = f"{resolved} in ArgoCD: {argo or 'registering'}"
    return result
