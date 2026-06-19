"""Kubernetes self-heal demo — Oracle OKE or local kind."""
from __future__ import annotations

import os
import subprocess
import time
import urllib.request
from pathlib import Path

import config as cfg


_SA_TOKEN = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
_SA_CA = Path("/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")


def _in_cluster_kubeconfig() -> Path | None:
    """Write a kubeconfig using the pod ServiceAccount (kubectl defaults to localhost:8080 without one)."""
    if not cfg.IN_CLUSTER:
        return None
    host = os.environ.get("KUBERNETES_SERVICE_HOST")
    port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
    if not host or not _SA_TOKEN.exists() or not _SA_CA.exists():
        return None
    ns_path = Path("/var/run/secrets/kubernetes.io/serviceaccount/namespace")
    namespace = ns_path.read_text().strip() if ns_path.exists() else "default"
    cfg_path = Path("/tmp/kube-in-cluster-config")
    cfg_path.write_text(
        f"""apiVersion: v1
kind: Config
clusters:
- cluster:
    certificate-authority: {_SA_CA}
    server: https://{host}:{port}
  name: in-cluster
contexts:
- context:
    cluster: in-cluster
    user: in-cluster-user
    namespace: {namespace}
  name: in-cluster
current-context: in-cluster
users:
- name: in-cluster-user
  user:
    token: {_SA_TOKEN.read_text().strip()}
""",
        encoding="utf-8",
    )
    return cfg_path


def _run(cmd: list[str], timeout: int = 120) -> tuple[int, str]:
    try:
        env = os.environ.copy()
        kubeconfig = cfg.KUBECONFIG or None
        if not kubeconfig and cfg.IN_CLUSTER:
            ic = _in_cluster_kubeconfig()
            if ic:
                kubeconfig = str(ic)
        if kubeconfig:
            env["KUBECONFIG"] = kubeconfig
        elif cfg.IN_CLUSTER:
            env.pop("KUBECONFIG", None)
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()
    except subprocess.TimeoutExpired:
        return 1, "Command timed out"
    except Exception as e:
        return 1, str(e)


def _kubectl_cmd(*args: str) -> list[str]:
    cmd = ["kubectl"]
    if cfg.KUBE_CONTEXT and not cfg.IN_CLUSTER:
        cmd.extend(["--context", cfg.KUBE_CONTEXT])
    cmd.extend(args)
    return cmd


def _kubectl(*args: str) -> tuple[int, str]:
    return _run(_kubectl_cmd(*args))


def _kubectl_must(*args: str, action: str = "kubectl command") -> str:
    code, out = _kubectl(*args)
    if code != 0:
        detail = _kubectl_value(code, out, out or "unknown error")
        raise RuntimeError(f"{action} failed: {detail}")
    return out


def _valid_browser_url(url: str) -> bool:
    u = (url or "").strip()
    if not u.startswith(("http://", "https://")):
        return False
    low = u.lower()
    if any(x in low for x in ("change_me", "example.com", ".svc.cluster.local", "localhost")):
        return False
    # Require a host after scheme (not bare paths like /health)
    rest = u.split("://", 1)[-1]
    host = rest.split("/", 1)[0]
    if not host:
        return False
    if host.replace(".", "").isdigit():
        return True
    return "." in host


def resolved_public_links() -> dict[str, str]:
    """Fill browser links from LoadBalancer services; never return placeholder URLs."""
    links = dict(cfg.public_links())
    for key, val in list(links.items()):
        if not _valid_browser_url(val):
            links[key] = ""

    if not _valid_browser_url(links.get("app_health", "")):
        host = _lb_host(cfg.NAMESPACE, cfg.DEPLOYMENT_NAME)
        if host:
            links["app_health"] = f"http://{host}/health"
            links["app_dashboard"] = f"http://{host}"

    if not _valid_browser_url(links.get("app_health", "")):
        ui_host = _lb_host("selfheal", "selfheal-ui")
        if ui_host:
            links["app_health"] = f"http://{ui_host}/staging/health"
            links["app_dashboard"] = f"http://{ui_host}/staging/"

    if not _valid_browser_url(links.get("argocd", "")):
        argo_host = _lb_host(cfg.ARGOCD_NAMESPACE, "argocd-server")
        if argo_host:
            links["argocd"] = f"https://{argo_host}"
            links["argocd_app"] = (
                f"https://{argo_host}/applications/{cfg.ARGOCD_NAMESPACE}/{cfg.ARGOCD_APP}"
            )
    elif not _valid_browser_url(links.get("argocd_app", "")):
        base = links["argocd"].rstrip("/")
        links["argocd_app"] = f"{base}/applications/{cfg.ARGOCD_NAMESPACE}/{cfg.ARGOCD_APP}"

    for key in links:
        if not _valid_browser_url(links[key]):
            links[key] = ""
    return links


def _pod_running_ready() -> bool:
    code, out = _kubectl(
        "get", "pods", "-n", cfg.NAMESPACE, "-l", cfg.POD_LABEL, "--no-headers",
    )
    if code != 0 or not out.strip():
        return False
    for line in out.strip().splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[1] == "1/1" and parts[2] == "Running":
            return True
    return False


def _pod_troubleshoot() -> str:
    code, out = _kubectl(
        "get", "pods", "-n", cfg.NAMESPACE, "-l", cfg.POD_LABEL,
        "-o", "wide", "--no-headers",
    )
    hint = out.strip() or "no pods found"
    code2, ev = _kubectl(
        "get", "events", "-n", cfg.NAMESPACE,
        "--field-selector", "involvedObject.kind=Pod",
        "--sort-by=.lastTimestamp",
    )
    if code2 == 0 and ev.strip():
        lines = ev.strip().splitlines()
        hint += " | " + lines[-1]
    return hint


def _argocd_trigger_sync() -> tuple[int, str]:
    patch = _patch_file(
        "argocd-sync-op.json",
        '{"operation":{"initiatedBy":{"username":"selfheal-ui"},"sync":{"revision":"HEAD"}}}',
    )
    return _kubectl(
        "patch", "application", cfg.ARGOCD_APP, "-n", cfg.ARGOCD_NAMESPACE,
        "--type", "merge", f"--patch-file={patch}",
    )


def _argocd_sync_status() -> str:
    code, out = _kubectl(
        "get", "application", cfg.ARGOCD_APP, "-n", cfg.ARGOCD_NAMESPACE,
        "-o", "jsonpath={.status.sync.status}/{.status.health.status}",
    )
    return _kubectl_value(code, out)


def _argocd_wait_synced(timeout: int = 90) -> str:
    deadline = time.time() + timeout
    last = _argocd_sync_status()
    while time.time() < deadline:
        if last.startswith("Synced/"):
            return last
        time.sleep(3)
        _argocd_refresh()
        last = _argocd_sync_status()
    return last


def _argocd_refresh() -> None:
    _kubectl(
        "annotate", "application", cfg.ARGOCD_APP, "-n", cfg.ARGOCD_NAMESPACE,
        "argocd.argoproj.io/refresh=hard", "--overwrite",
    )


def _argocd_set_automated(enabled: bool) -> tuple[int, str]:
    body = (
        '{"spec":{"syncPolicy":{"automated":{"prune":true,"selfHeal":true}}}}'
        if enabled
        else '{"spec":{"syncPolicy":{"automated":null}}}'
    )
    patch = _patch_file("argocd-sync.json", body)
    return _kubectl(
        "patch", "application", cfg.ARGOCD_APP, "-n", cfg.ARGOCD_NAMESPACE,
        "--type", "merge", f"--patch-file={patch}",
    )


def _argocd_restore_gitops_policy() -> tuple[int, str]:
    """Re-apply Application spec so auto-sync + ignoreDifferences survive the demo."""
    manifest = cfg.ARGOCD_APP_MANIFEST
    if manifest.exists():
        return _kubectl("apply", "-f", str(manifest))
    return _argocd_set_automated(True)


def _wait_app_ready(timeout: int = 60) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _pod_running_ready():
            return
        time.sleep(3)
    raise RuntimeError(
        "App pod did not become ready. "
        + _pod_troubleshoot()
    )


def _cluster_offline(out: str) -> bool:
    low = out.lower()
    return any(
        s in low
        for s in (
            "connection refused",
            "unable to connect",
            "no connection could be made",
            "the server is currently unable",
            "dial tcp",
            "context was not found",
            "localhost:8080",
            "invalid configuration",
            "no configuration has been provided",
        )
    )


def _cluster_reachable() -> tuple[bool, str]:
    code, out = _kubectl("get", "nodes", "--request-timeout=10s", "--no-headers")
    if code == 0 and out.strip():
        return True, ""
    if _cluster_offline(out):
        if cfg.DEPLOY_TARGET == "local":
            return False, (
                "Kubernetes cluster is offline. Start Docker Desktop, then run "
                "go-live.bat in D:\\enlight-lab-platform (or D:\\devops-selfheal)."
            )
        return False, (
            "Cannot reach Oracle OKE cluster. Check kubeconfig, OKE cluster state, "
            "and that this pod/service account has cluster access."
        )
    if "forbidden" in out.lower() or "cannot get" in out.lower():
        return False, (
            "Kubernetes API denied access for the selfheal-ui service account. "
            "Apply deploy/k8s/selfheal-ui.yaml RBAC or the demo cluster-admin binding."
        )
    return False, f"Cannot reach cluster ({cfg.KUBE_CONTEXT or 'default context'}). Check kubectl access."


def _kubectl_value(code: int, out: str, fallback: str = "unknown") -> str:
    text = (out or "").strip()
    if code != 0 or not text or _cluster_offline(text):
        return "cluster offline" if _cluster_offline(text) else fallback
    if any(x in text for x in ("Unhandled Error", "memcache.go", "Unable to connect")):
        return "cluster offline"
    return text


def _reachable(url: str, timeout: int = 5) -> bool:
    if not url:
        return False
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def _gitops_ui_state(sync_health: str) -> str:
    """ok = Synced+Healthy, warn = OutOfSync but Healthy (normal after demo heal), fail = else."""
    text = (sync_health or "").strip()
    if not text or "cluster offline" in text.lower():
        return "fail"
    parts = text.split("/", 1)
    sync = parts[0] if parts else ""
    health = parts[1] if len(parts) > 1 else ""
    if health == "Healthy" and sync == "Synced":
        return "ok"
    if health == "Healthy":
        return "warn"
    return "fail"


def _argocd_reachable() -> bool:
    if cfg.IN_CLUSTER:
        code, _ = _kubectl(
            "get", "application", cfg.ARGOCD_APP, "-n", cfg.ARGOCD_NAMESPACE,
        )
        return code == 0
    return _reachable(cfg.ARGOCD_CHECK_URL)


def _ensure_port_forward() -> None:
    if not cfg.USE_PORT_FORWARD:
        return
    script = cfg.ROOT / "scripts" / "port-forward-all.ps1"
    if not script.exists():
        return
    _run(["powershell", "-NoProfile", "-File", str(script)], timeout=60)
    for _ in range(5):
        time.sleep(2)
        if _reachable(cfg.APP_HEALTH_CHECK_URL):
            return


def _patch_file(name: str, body: str) -> Path:
    path = Path(os.environ.get("TEMP", "/tmp")) / name
    path.write_text(body, encoding="utf-8")
    return path


def platform_status() -> dict:
    ok, cluster_msg = _cluster_reachable()
    checks: dict = {
        "cluster": "ok" if ok else "fail",
        "cluster_message": cluster_msg,
        **cfg.runtime_info(),
        "links": resolved_public_links(),
    }

    if cfg.USE_PORT_FORWARD and not _reachable(cfg.APP_HEALTH_CHECK_URL):
        _ensure_port_forward()

    for name, url in [
        ("app_health", cfg.APP_HEALTH_CHECK_URL),
        ("app_dashboard", cfg.APP_DASHBOARD_CHECK_URL),
    ]:
        checks[name] = "ok" if _reachable(url) else "fail"
    checks["deployments"] = "ok" if _argocd_reachable() else "fail"

    if not ok:
        checks["pod"] = "cluster offline"
        checks["gitops_app"] = "cluster offline"
        return checks

    code, pods = _kubectl(
        "get", "pods", "-n", cfg.NAMESPACE, "-l", cfg.POD_LABEL, "--no-headers",
    )
    checks["pod"] = _kubectl_value(code, pods, "no pods")

    code, argo = _kubectl(
        "get", "application", cfg.ARGOCD_APP, "-n", cfg.ARGOCD_NAMESPACE,
        "-o", "jsonpath={.status.sync.status}/{.status.health.status}",
    )
    checks["gitops_app"] = _kubectl_value(code, argo)
    checks["gitops_ui_state"] = _gitops_ui_state(checks["gitops_app"])
    return checks


def _deployment_replicas() -> int:
    code, out = _kubectl(
        "get", "deployment", cfg.DEPLOYMENT_NAME, "-n", cfg.NAMESPACE,
        "-o", "jsonpath={.spec.replicas}",
    )
    if code != 0 or not out.strip().isdigit():
        return -1
    return int(out.strip())


def _clear_crash_override() -> None:
    patch = _patch_file(
        "deploy-clear-cmd.json",
        '[{"op":"remove","path":"/spec/template/spec/containers/0/command"},'
        '{"op":"remove","path":"/spec/template/spec/containers/0/args"}]',
    )
    _kubectl(
        "patch", "deployment", cfg.DEPLOYMENT_NAME, "-n", cfg.NAMESPACE,
        "--type", "json", f"--patch-file={patch}",
    )


def _pause_argocd_autosync() -> None:
    patch = _patch_file(
        "argocd-no-heal.json",
        '{"spec":{"syncPolicy":{"automated":null}}}',
    )
    code, patch_out = _kubectl(
        "patch", "application", cfg.ARGOCD_APP, "-n", cfg.ARGOCD_NAMESPACE,
        "--type", "merge", f"--patch-file={patch}",
    )
    if code != 0:
        raise RuntimeError(_kubectl_value(code, patch_out, "Failed to pause ArgoCD auto-sync"))


def simulate_outage() -> dict:
    ok, cluster_msg = _cluster_reachable()
    if not ok:
        raise RuntimeError(cluster_msg)

    _pause_argocd_autosync()
    mode = cfg.OUTAGE_MODE
    mode_note = ""

    if mode == "instant":
        _kubectl_must(
            "scale", f"deployment/{cfg.DEPLOYMENT_NAME}",
            "-n", cfg.NAMESPACE, "--replicas=0",
            action="Scale staging app to zero",
        )
        mode_note = "Scaled to 0 replicas — app down in seconds (faster than waiting on ArgoCD Progressing)."
    elif mode == "crash":
        _clear_crash_override()
        patch = _patch_file(
            "deploy-crash.json",
            '[{"op":"add","path":"/spec/template/spec/containers/0/command",'
            '"value":["/bin/sh","-c","exit 1"]},'
            '{"op":"replace","path":"/spec/template/spec/containers/0/args","value":[]}]',
        )
        code, patch_out = _kubectl(
            "patch", "deployment", cfg.DEPLOYMENT_NAME, "-n", cfg.NAMESPACE,
            "--type", "json", f"--patch-file={patch}",
        )
        if code != 0:
            raise RuntimeError(_kubectl_value(code, patch_out, "Failed to inject crash command"))
        _kubectl("delete", "pods", "-n", cfg.NAMESPACE, "-l", cfg.POD_LABEL, "--wait=false")
        mode_note = "Crash loop injected — pod shows CrashLoopBackOff in ~15s."
    else:
        mode = "image"
        code, set_out = _kubectl(
            "set", "image",
            f"deployment/{cfg.DEPLOYMENT_NAME}",
            f"{cfg.CONTAINER_NAME}={cfg.BAD_IMAGE}",
            "-n", cfg.NAMESPACE,
        )
        if code != 0:
            raise RuntimeError(_kubectl_value(code, set_out, "Failed to set bad image on deployment"))
        mode_note = "Bad image set — ErrImagePull can take 1–2 min; use demo status cards meanwhile."

    # Brief wait so health/pod status updates before we return.
    deadline = time.time() + (6 if mode == "instant" else 18)
    while time.time() < deadline:
        if mode == "instant" and _deployment_replicas() == 0:
            break
        if mode != "instant" and not _pod_running_ready():
            break
        time.sleep(2)

    code, pods = _kubectl(
        "get", "pods", "-n", cfg.NAMESPACE, "-l", cfg.POD_LABEL, "--no-headers",
    )
    code2, argo = _kubectl(
        "get", "application", cfg.ARGOCD_APP, "-n", cfg.ARGOCD_NAMESPACE,
        "-o", "jsonpath={.status.sync.status}/{.status.health.status}",
    )
    links = resolved_public_links()
    app_down = not _reachable(cfg.APP_HEALTH_CHECK_URL)
    tips = [
        "App health card above should show Down within seconds.",
        "Open Staging dashboard — health metric turns red.",
        "ArgoCD may stay Progressing during image pull; this page is the live view.",
    ]
    if links.get("app_dashboard"):
        tips.insert(1, f"Staging UI: {links['app_dashboard']}")
    if links.get("app_health"):
        tips.insert(2, f"Health API: {links['app_health']}")

    return {
        "mode": mode,
        "pods": _kubectl_value(code, pods, "no pods"),
        "argocd": _kubectl_value(code2, argo, "unknown"),
        "app_down": app_down,
        "tips": tips,
        "open_url": links.get("argocd_app", cfg.PUBLIC_ARGOCD_APP_URL),
        "staging_url": links.get("app_dashboard", cfg.PUBLIC_APP_DASHBOARD_URL),
        "health_url": links.get("app_health", cfg.PUBLIC_APP_HEALTH_URL),
        "message": "Outage simulated — staging app is down on purpose. " + mode_note,
    }


def _kubectl_events_findings(namespace: str, limit: int = 8) -> list[str]:
    code, out = _kubectl(
        "get", "events", "-n", namespace,
        "--sort-by=.lastTimestamp",
        "--field-selector", "type!=Normal",
    )
    if code != 0 or not out.strip():
        return []
    lines = [ln.strip() for ln in out.strip().splitlines() if ln.strip()]
    if len(lines) > 1:
        lines = lines[1:]  # skip header
    return lines[-limit:]


def explain_with_ai() -> dict:
    ok, cluster_msg = _cluster_reachable()
    if not ok:
        raise RuntimeError(cluster_msg)

    # No --explain: uses built-in analyzers only (no OpenAI key required).
    code, out = _run(
        [cfg.K8SGPT_BIN, "analyze", "--namespace", cfg.NAMESPACE, "--no-cache"],
        timeout=cfg.K8SGPT_TIMEOUT,
    )
    if code != 0 and "openai" in out.lower():
        code, out = _run(
            [cfg.K8SGPT_BIN, "analyze", "--namespace", cfg.NAMESPACE, "--no-cache", "--backend", "noopai"],
            timeout=cfg.K8SGPT_TIMEOUT,
        )

    findings: list[str] = []
    for line in out.splitlines():
        s = line.strip()
        if not s:
            continue
        if any(k in s for k in ("Error", "BackOff", "ErrImagePull", "failed", "Problem", "ImagePull")):
            findings.append(s)
    if not findings and out:
        findings = [ln.strip() for ln in out.splitlines() if ln.strip()][:8]
    if not findings:
        findings = _kubectl_events_findings(cfg.NAMESPACE)

    used_fallback = code != 0 and bool(findings)
    return {
        "ok": code == 0 or bool(findings),
        "findings": findings[:8],
        "raw": out[-2500:],
        "message": (
            "AI analysis complete"
            if findings
            else "No issues found — run Simulate outage first, or configure k8sgpt (see below)"
        ),
        "hint": (
            ""
            if code == 0
            else "Tip: for full AI explain, run in Cloud Shell: "
            "kubectl -n selfheal exec deploy/selfheal-ui -- k8sgpt auth add -b noopai"
        ) if not used_fallback else "",
    }


def auto_fix() -> dict:
    ok, cluster_msg = _cluster_reachable()
    if not ok:
        raise RuntimeError(cluster_msg)

    argo_note = ""
    heal_error: Exception | None = None
    try:
        # On OKE, ArgoCD owns manifests from enlight-lab-platform overlays/oci.
        # kubectl apply of bundled staging-app drifts labels/resources and stays OutOfSync.
        staging = cfg.STAGING_APP_PATH
        if cfg.DEPLOY_TARGET != "oci" and staging.exists():
            code, apply_out = _run(_kubectl_cmd("apply", "-f", str(staging)), timeout=60)
            if code != 0:
                _kubectl(
                    "delete", "deployment", cfg.DEPLOYMENT_NAME, "-n", cfg.NAMESPACE,
                    "--ignore-not-found",
                )
                code, apply_out = _run(_kubectl_cmd("apply", "-f", str(staging)), timeout=60)
            if code != 0:
                raise RuntimeError(f"Apply staging app failed: {apply_out}")

        _kubectl_must(
            "scale", f"deployment/{cfg.DEPLOYMENT_NAME}",
            "-n", cfg.NAMESPACE, "--replicas=1",
            action="Scale staging app up",
        )
        _clear_crash_override()
        _kubectl_must(
            "set", "image",
            f"deployment/{cfg.DEPLOYMENT_NAME}",
            f"{cfg.CONTAINER_NAME}={cfg.GOOD_IMAGE}",
            "-n", cfg.NAMESPACE,
            action="Restore good image",
        )
        _kubectl("delete", "pods", "-n", cfg.NAMESPACE, "-l", cfg.POD_LABEL, "--wait=false")
        _kubectl_must(
            "rollout", "status", f"deployment/{cfg.DEPLOYMENT_NAME}",
            "-n", cfg.NAMESPACE, "--timeout=90s",
            action="Wait for rollout",
        )
    except Exception as e:
        heal_error = e
    finally:
        # Simulate outage turns auto-sync off; always restore GitOps policy after heal.
        restore_code, restore_out = _argocd_restore_gitops_policy()
        if restore_code != 0:
            argo_note += f" (ArgoCD restore skipped: {(restore_out or '')[:100]})"
        _argocd_refresh()
        sync_code, sync_out = _argocd_trigger_sync()
        if sync_code != 0:
            argo_note += f" (ArgoCD sync skipped: {(sync_out or '')[:80]})"
        else:
            argo = _argocd_wait_synced(timeout=90)
            if not argo.startswith("Synced/"):
                argo_note += " (GitOps still reconciling — refresh ArgoCD in ~30s)"

    if heal_error is not None:
        raise heal_error

    _ensure_port_forward()
    code, argo = _kubectl(
        "get", "application", cfg.ARGOCD_APP, "-n", cfg.ARGOCD_NAMESPACE,
        "-o", "jsonpath={.status.sync.status}/{.status.health.status}",
    )
    health = "unreachable"
    if _reachable(cfg.APP_HEALTH_CHECK_URL):
        with urllib.request.urlopen(cfg.APP_HEALTH_CHECK_URL, timeout=5) as r:
            health = r.read().decode()
    try:
        links = resolved_public_links()
    except Exception:
        links = {}
    recovered = _pod_running_ready() and health != "unreachable"
    return {
        "argocd": _kubectl_value(code, argo),
        "health": health,
        "app_reachable": recovered,
        "open_url": links.get("app_health", cfg.PUBLIC_APP_HEALTH_URL),
        "message": (
            ("App recovered — health check passed" if recovered else f"Heal applied — {_pod_troubleshoot()}")
            + argo_note
        ),
    }
