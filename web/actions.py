"""Kubernetes self-heal demo — Oracle OKE or local kind."""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

import config as cfg

StepCallback = Callable[[dict], None] | None
_active_step_cb: StepCallback = None


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


def _run(cmd: list[str], timeout: int = 120, extra_env: dict[str, str] | None = None) -> tuple[int, str]:
    try:
        env = os.environ.copy()
        if extra_env:
            env.update(extra_env)
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


def _lb_host(namespace: str, service: str) -> str:
    """LoadBalancer IP or hostname for a Service (empty if pending)."""
    for jp in (
        "{.status.loadBalancer.ingress[0].ip}",
        "{.status.loadBalancer.ingress[0].hostname}",
    ):
        code, out = _kubectl(
            "get", "svc", service, "-n", namespace, "-o", f"jsonpath={jp}",
        )
        if code == 0 and out.strip():
            return out.strip()
    return ""


def _kubectl_secret_data(namespace: str, secret: str, key: str) -> str:
    """Read and base64-decode one key from a Kubernetes secret."""
    code, out = _kubectl(
        "get", "secret", secret, "-n", namespace,
        "-o", f"jsonpath={{.data.{key}}}",
    )
    if code != 0 or not out.strip():
        return ""
    try:
        return base64.b64decode(out.strip()).decode("utf-8").strip()
    except Exception:
        return ""


def resolved_argocd_credentials() -> dict[str, str | bool]:
    """Live Argo CD login for demo UI — prefer synced demo secret over stale env."""
    user = cfg.ARGOCD_DISPLAY_USER or "admin"
    password = ""
    source = "none"

    password = _kubectl_secret_data("selfheal", "argocd-demo-login", "password")
    if password:
        source = "demo-login-secret"
    if not password:
        password = _kubectl_secret_data(cfg.ARGOCD_NAMESPACE, "argocd-initial-admin-secret", "password")
        if password:
            source = "initial-admin-secret"
    if not password and cfg.ARGOCD_DISPLAY_PASSWORD:
        password = cfg.ARGOCD_DISPLAY_PASSWORD.strip()
        source = "env"

    return {
        "argocd_user": user,
        "argocd_password": password,
        "argocd_password_set": bool(password),
        "argocd_password_source": source,
        "argocd_password_hint": (
            ""
            if password and source == "demo-login-secret"
            else "Run deploy/oci/setup-argocd-demo-login.sh in Cloud Shell to reset admin password."
        ),
    }


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


def _argocd_app_exists() -> bool:
    code, _ = _kubectl("get", "application", cfg.ARGOCD_APP, "-n", cfg.ARGOCD_NAMESPACE)
    return code == 0


def _wait_argocd_app_status(timeout: int = 120, want_health: str = "Healthy") -> str:
    """Poll until Application reports health (returns sync/health string)."""
    deadline = time.time() + timeout
    last = "Unknown/Unknown"
    while time.time() < deadline:
        if not _argocd_app_exists():
            time.sleep(3)
            continue
        code, sh = _kubectl(
            "get", "application", cfg.ARGOCD_APP, "-n", cfg.ARGOCD_NAMESPACE,
            "-o", "jsonpath={.status.sync.status}/{.status.health.status}",
        )
        last = _kubectl_value(code, sh, last)
        if want_health in last:
            return last
        _argocd_refresh()
        time.sleep(4)
    return last


def _register_argocd_app() -> tuple[int, str]:
    manifest = cfg.ARGOCD_APP_MANIFEST
    if not manifest.exists():
        return 1, f"Application manifest not found: {manifest}"
    return _kubectl("apply", "-f", str(manifest))


def _unregister_argocd_app() -> tuple[int, str]:
    """Remove fastapi-staging from Argo CD (prune deletes staging workloads)."""
    return _kubectl(
        "delete", "application", cfg.ARGOCD_APP,
        "-n", cfg.ARGOCD_NAMESPACE,
        "--wait=false",
        "--ignore-not-found",
    )


def _staging_pod_summary() -> str:
    code, pods = _kubectl(
        "get", "pods", "-n", cfg.NAMESPACE, "-l", cfg.POD_LABEL, "--no-headers",
    )
    return _kubectl_value(code, pods, "no pods")


def _staging_workloads_exist() -> bool:
    for resource in ("deployment", "service"):
        code, _ = _kubectl("get", resource, cfg.DEPLOYMENT_NAME, "-n", cfg.NAMESPACE)
        if code == 0:
            return True
    code, pods = _kubectl(
        "get", "pods", "-n", cfg.NAMESPACE, "-l", cfg.POD_LABEL, "--no-headers",
    )
    text = _kubectl_value(code, pods, "")
    return bool(text.strip()) and text not in ("no pods", "cluster offline", "unknown")


def _delete_staging_workloads() -> None:
    """Remove staging Deployment/Service/Pods even if Argo CD prune did not run."""
    _kubectl(
        "scale", f"deployment/{cfg.DEPLOYMENT_NAME}",
        "-n", cfg.NAMESPACE, "--replicas=0", "--ignore-not-found",
    )
    _kubectl(
        "delete", "deployment", cfg.DEPLOYMENT_NAME,
        "-n", cfg.NAMESPACE, "--wait=false", "--ignore-not-found",
    )
    _kubectl(
        "delete", "service", cfg.DEPLOYMENT_NAME,
        "-n", cfg.NAMESPACE, "--wait=false", "--ignore-not-found",
    )
    _kubectl(
        "delete", "replicaset", "-n", cfg.NAMESPACE, "-l", cfg.POD_LABEL,
        "--wait=false", "--ignore-not-found",
    )
    _kubectl(
        "delete", "pods", "-n", cfg.NAMESPACE, "-l", cfg.POD_LABEL,
        "--force", "--grace-period=0", "--ignore-not-found",
    )


def _staging_is_clean() -> bool:
    return (
        not _argocd_app_exists()
        and not _staging_workloads_exist()
        and not _reachable(cfg.APP_HEALTH_CHECK_URL)
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


def _wait_rollout_with_steps(
    timeline: list[dict[str, str]],
    *,
    timeout: int = 120,
    title: str = "Waiting for rollout to complete",
) -> None:
    """Poll rollout status and stream progress (avoids silent 120s SSE gaps)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        code, out = _kubectl(
            "rollout", "status", f"deployment/{cfg.DEPLOYMENT_NAME}",
            "-n", cfg.NAMESPACE, "--timeout=8s",
        )
        if code == 0:
            return
        detail = _staging_pod_summary()
        if out.strip() and "cluster offline" not in out.lower():
            detail = out.splitlines()[-1][:160]
        _timeline_step(timeline, title, detail, phase="k8s", pause=False)
        time.sleep(4)
    raise RuntimeError(f"Rollout timed out after {timeout}s. {_pod_troubleshoot()}")


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
            "Cannot reach Kubernetes cluster. Check kubeconfig, cluster state, "
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


def platform_status(*, health_timeout: int = 2, resolve_links: bool = False) -> dict:
    ok, cluster_msg = _cluster_reachable()
    checks: dict = {
        "cluster": "ok" if ok else "fail",
        "cluster_message": cluster_msg,
        **cfg.runtime_info(),
        "links": resolved_public_links() if resolve_links else cfg.public_links(),
    }

    if cfg.USE_PORT_FORWARD and not _reachable(cfg.APP_HEALTH_CHECK_URL):
        _ensure_port_forward()

    for name, url in [
        ("app_health", cfg.APP_HEALTH_CHECK_URL),
        ("app_dashboard", cfg.APP_DASHBOARD_CHECK_URL),
    ]:
        checks[name] = "ok" if _reachable(url, timeout=health_timeout) else "fail"
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
    checks["argocd_app_exists"] = _argocd_app_exists()
    checks["workloads_exist"] = _staging_workloads_exist()
    checks["app_reachable"] = checks.get("app_health") == "ok"
    pod_ok = _pod_running_ready()
    gitops_parts = (checks["gitops_app"] or "").split("/", 1)
    gitops_health = gitops_parts[1] if len(gitops_parts) > 1 else ""
    checks["staging_deployed"] = (
        checks["argocd_app_exists"]
        and checks["app_reachable"]
        and pod_ok
        and gitops_health == "Healthy"
    )
    checks["staging_clean"] = (
        not checks["argocd_app_exists"]
        and not checks["workloads_exist"]
        and not checks["app_reachable"]
    )
    if checks["staging_deployed"]:
        checks["staging_status_message"] = (
            f"fastapi-staging is live — Argo CD {checks['gitops_app']}. Continue to Step 2."
        )
    elif checks["staging_clean"]:
        checks["staging_status_message"] = (
            "Clean slate — staging is down and not in Argo CD. Step 1 deploys from GitHub."
        )
    elif not checks["argocd_app_exists"] and checks["workloads_exist"]:
        checks["staging_status_message"] = (
            "GitOps app removed but staging workloads still exist — run Reset again."
        )
    elif not checks["argocd_app_exists"]:
        checks["staging_status_message"] = (
            "fastapi-staging is not registered in Argo CD — Step 1 will deploy the full GitOps app."
        )
    elif not checks["app_reachable"] or not pod_ok or gitops_health != "Healthy":
        checks["staging_status_message"] = (
            "Outage in progress — staging app is down on purpose. Continue to Step 3 Explain, then Step 4 Auto-fix."
        )
    else:
        checks["staging_status_message"] = ""
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


def _timeline_step(
    timeline: list[dict[str, str]],
    title: str,
    detail: str = "",
    *,
    pause: bool = True,
    phase: str = "",
) -> None:
    cb = _active_step_cb
    ph = phase or _infer_phase(title)
    if cb:
        cb({"title": title, "detail": detail, "status": "running", "phase": ph})
    if pause and cfg.DEMO_STEP_PAUSE > 0:
        time.sleep(cfg.DEMO_STEP_PAUSE)
    entry = {"title": title, "detail": detail, "status": "done", "phase": ph}
    timeline.append(entry)
    if cb:
        cb({**entry})


def _infer_phase(title: str) -> str:
    t = title.lower()
    if any(x in t for x in ("k8sgpt", "holmes", "ai ", "ai-", "diagnos", "explain", "analyz")):
        return "ai"
    if any(x in t for x in ("health", "recover", "complete")):
        return "health"
    if any(x in t for x in ("argocd", "gitops", "auto-sync")):
        return "argocd"
    if any(x in t for x in ("git", "manifest", "syncing")):
        return "git"
    if any(x in t for x in ("outage", "bad", "crash", "invalid image", "simulate")):
        return "break"
    if any(x in t for x in ("remove", "reset", "tear down", "unregister", "prune", "uninstall")):
        return "argocd"
    return "k8s"


def _with_step_stream(on_step: StepCallback, fn: Callable[[], dict]) -> dict:
    global _active_step_cb
    prev = _active_step_cb
    _active_step_cb = on_step
    try:
        return fn()
    finally:
        _active_step_cb = prev


def reset_staging(on_step: StepCallback = None) -> dict:
    return _with_step_stream(on_step, _reset_staging_impl)


def _reset_staging_impl() -> dict:
    """Full teardown: remove GitOps app and staging workloads until health is down."""
    ok, cluster_msg = _cluster_reachable()
    if not ok:
        raise RuntimeError(cluster_msg)

    timeline: list[dict[str, str]] = []
    _timeline_step(
        timeline,
        "Connecting to Kubernetes cluster",
        "Preparing full reset of fastapi-staging",
        phase="k8s",
    )
    _timeline_step(
        timeline,
        "Unregistering fastapi-staging from Argo CD",
        "Removes the GitOps application from the control plane",
        phase="argocd",
    )
    code, out = _unregister_argocd_app()
    if code != 0 and "not found" not in out.lower():
        raise RuntimeError(_kubectl_value(code, out, "Failed to remove fastapi-staging"))

    _timeline_step(
        timeline,
        "Deleting staging Deployment, Service, and pods",
        f"namespace {cfg.NAMESPACE} · ensures app is fully torn down",
        phase="k8s",
        pause=False,
    )
    _delete_staging_workloads()

    _timeline_step(
        timeline,
        "Waiting for staging workloads to disappear",
        _staging_pod_summary(),
        phase="k8s",
        pause=False,
    )
    for _ in range(30):
        app_gone = not _argocd_app_exists()
        workloads_gone = not _staging_workloads_exist()
        pods = _staging_pod_summary()
        health_down = not _reachable(cfg.APP_HEALTH_CHECK_URL)
        if app_gone and workloads_gone and health_down:
            _timeline_step(
                timeline,
                "Staging is fully down",
                f"{cfg.NAMESPACE} — no GitOps app, no pods, health check failed",
                phase="health",
                pause=False,
            )
            break
        _timeline_step(
            timeline,
            "Tearing down staging",
            f"Argo CD: {'removed' if app_gone else 'removing…'} · "
            f"Workloads: {'gone' if workloads_gone else 'removing…'} · "
            f"Pods: {pods} · Health: {'down' if health_down else 'still up…'}",
            phase="k8s",
            pause=False,
        )
        time.sleep(3)
    else:
        _delete_staging_workloads()
        time.sleep(3)
        _delete_staging_workloads()
        _timeline_step(
            timeline,
            "Teardown finishing",
            _staging_pod_summary(),
            phase="k8s",
            pause=False,
        )

    clean = _staging_is_clean()
    _timeline_step(
        timeline,
        "Reset complete — ready for Step 1" if clean else "Reset finished — verify staging is down",
        "Deploy will register fastapi-staging from GitHub and bring workloads back"
        if clean
        else "Some resources may still be terminating — refresh status before deploy",
        phase="git",
        pause=False,
    )
    links = resolved_public_links()
    return {
        "message": (
            "Clean slate — staging is down. Click Deploy fastapi-staging for a full GitOps deploy."
            if clean
            else "Reset ran — verify staging is down, then click Deploy fastapi-staging."
        ),
        "timeline": timeline,
        "app_reachable": _reachable(cfg.APP_HEALTH_CHECK_URL),
        "argocd_app_exists": _argocd_app_exists(),
        "workloads_exist": _staging_workloads_exist(),
        "staging_clean": clean,
        "links": links,
    }


def deploy_application(on_step: StepCallback = None) -> dict:
    return _with_step_stream(on_step, _deploy_application_impl)


def _deploy_application_impl() -> dict:
    """Step 1: Register fastapi-staging in Argo CD and sync workloads from GitHub."""
    ok, cluster_msg = _cluster_reachable()
    if not ok:
        raise RuntimeError(cluster_msg)

    timeline: list[dict[str, str]] = []
    _timeline_step(timeline, "Connecting to Kubernetes cluster", cfg.KUBE_CONTEXT or "in-cluster ServiceAccount")

    app_exists = _argocd_app_exists()
    if not app_exists:
        _timeline_step(
            timeline,
            "Registering Argo CD Application fastapi-staging",
            "GitHub → overlays/oci → namespace enlight-staging",
        )
        reg_code, reg_out = _register_argocd_app()
        if reg_code != 0:
            raise RuntimeError(f"Failed to register fastapi-staging: {reg_out}")
        _timeline_step(timeline, "GitOps controller accepting new application", cfg.ARGOCD_APP)
        _wait_argocd_app_status(timeout=90, want_health="")
        app_exists = _argocd_app_exists()
        if not app_exists:
            raise RuntimeError("fastapi-staging did not appear in Argo CD after apply")
    else:
        _timeline_step(
            timeline,
            "fastapi-staging already registered in Argo CD",
            "Refreshing sync policy and manifest from Git",
        )

    _timeline_step(
        timeline,
        "Applying GitOps sync policy",
        f"Application {cfg.ARGOCD_APP} — auto-sync enabled",
    )
    restore_code, restore_out = _argocd_restore_gitops_policy()
    if restore_code != 0:
        raise RuntimeError(f"ArgoCD app setup failed: {restore_out}")
    _argocd_set_automated(True)
    _timeline_step(
        timeline,
        "Syncing manifests from GitHub",
        "Namespace, Deployment, Service, and health probes",
    )
    _argocd_refresh()
    sync_code, _ = _argocd_trigger_sync()
    if sync_code == 0:
        _argocd_wait_synced(timeout=90)
    _timeline_step(timeline, "Setting known-good container image", cfg.GOOD_IMAGE)
    _clear_crash_override()
    _kubectl_must(
        "scale", f"deployment/{cfg.DEPLOYMENT_NAME}",
        "-n", cfg.NAMESPACE, "--replicas=1",
        action="Scale staging to one replica",
    )
    _kubectl_must(
        "set", "image",
        f"deployment/{cfg.DEPLOYMENT_NAME}",
        f"{cfg.CONTAINER_NAME}={cfg.GOOD_IMAGE}",
        "-n", cfg.NAMESPACE,
        action="Apply good image",
    )
    _timeline_step(
        timeline,
        "Kubernetes scheduling pod on worker node",
        f"namespace {cfg.NAMESPACE} · label {cfg.POD_LABEL}",
    )
    _wait_rollout_with_steps(
        timeline,
        title="Waiting for staging pod to become ready",
    )
    _timeline_step(timeline, "Running application health check", cfg.APP_HEALTH_CHECK_URL)
    health = "unreachable"
    if _reachable(cfg.APP_HEALTH_CHECK_URL):
        with urllib.request.urlopen(cfg.APP_HEALTH_CHECK_URL, timeout=8) as r:
            health = r.read().decode()[:200]
    code, argo = _kubectl(
        "get", "application", cfg.ARGOCD_APP, "-n", cfg.ARGOCD_NAMESPACE,
        "-o", "jsonpath={.status.sync.status}/{.status.health.status}",
    )
    code2, pods = _kubectl(
        "get", "pods", "-n", cfg.NAMESPACE, "-l", cfg.POD_LABEL, "--no-headers",
    )
    links = resolved_public_links()
    healthy = _pod_running_ready() and health != "unreachable"
    _timeline_step(
        timeline,
        "Deployment complete" if healthy else "Deployment finished — verify status",
        "Open staging dashboard to show the live app" if healthy else _pod_troubleshoot(),
        pause=False,
    )
    return {
        "message": (
            "fastapi-staging deployed and healthy — GitOps app live in Argo CD."
            if healthy
            else f"fastapi-staging sync applied — {_pod_troubleshoot()}"
        ),
        "timeline": timeline,
        "app_reachable": healthy,
        "health": health,
        "pods": _kubectl_value(code2, pods, "no pods"),
        "argocd": _kubectl_value(code, argo),
        "architecture": [
            "Managed Kubernetes cluster — worker nodes in your cloud region",
            "ArgoCD — GitOps controller syncs manifests from GitHub",
            f"Namespace {cfg.NAMESPACE} — isolated staging workload",
            "Container registry — images pulled at deploy time",
            "selfheal-ui — this demo orchestrates kubectl + k8sgpt from the cluster",
        ],
        "open_url": links.get("app_dashboard") or links.get("app_health"),
        "staging_url": links.get("app_dashboard"),
        "health_url": links.get("app_health"),
        "argocd_url": links.get("argocd_app") or links.get("argocd"),
    }


def simulate_outage(on_step: StepCallback = None) -> dict:
    return _with_step_stream(on_step, _simulate_outage_impl)


def _simulate_outage_impl() -> dict:
    ok, cluster_msg = _cluster_reachable()
    if not ok:
        raise RuntimeError(cluster_msg)

    timeline: list[dict[str, str]] = []
    _timeline_step(
        timeline,
        "Preparing incident simulation",
        "Safe for staging — production is not touched",
    )
    _timeline_step(timeline, "Pausing ArgoCD auto-sync", "So GitOps won't heal before you run Explain + Auto-fix")
    _pause_argocd_autosync()
    mode = cfg.OUTAGE_MODE
    mode_note = ""

    if mode == "instant":
        _timeline_step(timeline, "Scaling deployment to zero replicas", "Fast outage — no pods remain")
        _kubectl_must(
            "scale", f"deployment/{cfg.DEPLOYMENT_NAME}",
            "-n", cfg.NAMESPACE, "--replicas=0",
            action="Scale staging app to zero",
        )
        mode_note = "Scaled to 0 replicas — app down in seconds."
    elif mode == "crash":
        _timeline_step(timeline, "Injecting crash-loop command into container", "Pod will exit immediately on start")
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
        _timeline_step(timeline, "Waiting for CrashLoopBackOff", "ArgoCD should show Degraded")
        mode_note = "Crash loop injected — pod shows CrashLoopBackOff in ~15s."
    else:
        mode = "image"
        _timeline_step(
            timeline,
            "Patching deployment with invalid container image",
            cfg.BAD_IMAGE,
        )
        code, set_out = _kubectl(
            "set", "image",
            f"deployment/{cfg.DEPLOYMENT_NAME}",
            f"{cfg.CONTAINER_NAME}={cfg.BAD_IMAGE}",
            "-n", cfg.NAMESPACE,
        )
        if code != 0:
            raise RuntimeError(_kubectl_value(code, set_out, "Failed to set bad image on deployment"))
        _timeline_step(
            timeline,
            "Kubernetes rolling out the bad spec",
            "Old pod terminates → new pod tries to pull image → ErrImagePull",
        )
        _timeline_step(
            timeline,
            "Waiting for failure signals",
            "ArgoCD Progressing → Degraded in ~1–2 minutes",
        )
        mode_note = "Bad image deployed — ErrImagePull / ImagePullBackOff expected."

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
    _timeline_step(
        timeline,
        "Outage active — application is down",
        _kubectl_value(code, pods, "no pods"),
        pause=False,
    )
    tips = [
        "App health card should show Down.",
        "Open Staging dashboard — metrics turn red.",
        "Open ArgoCD (credentials on this page) to show OutOfSync / Degraded.",
    ]
    if links.get("app_dashboard"):
        tips.insert(1, f"Staging UI: {links['app_dashboard']}")
    if links.get("app_health"):
        tips.insert(2, f"Health API: {links['app_health']}")

    return {
        "mode": mode,
        "timeline": timeline,
        "pods": _kubectl_value(code, pods, "no pods"),
        "argocd": _kubectl_value(code2, argo, "unknown"),
        "app_down": app_down,
        "tips": tips,
        "open_url": links.get("argocd_app", cfg.PUBLIC_ARGOCD_APP_URL),
        "staging_url": links.get("app_dashboard", cfg.PUBLIC_APP_DASHBOARD_URL),
        "health_url": links.get("app_health", cfg.PUBLIC_APP_HEALTH_URL),
        "message": "Outage simulated — staging app is down on purpose. " + mode_note,
    }


def _kubectl_events_findings(namespace: str, limit: int = 8, pod_name: str = "") -> list[str]:
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
    if pod_name:
        pod_lines = [ln for ln in lines if pod_name in ln]
        lines = pod_lines if pod_lines else []
    return lines[-limit:]


def _staging_is_healthy(ctx: dict) -> bool:
    replicas = ctx.get("replicas", -1)
    ready = ctx.get("ready_replicas", 0)
    if replicas <= 0 or ready < replicas:
        return False
    pod_line = (ctx.get("pod_line") or "").lower()
    if "running" not in pod_line or "1/1" not in pod_line:
        return False
    reason = (ctx.get("pod_reason") or "").lower()
    return reason not in ("errimagepull", "imagepullbackoff", "crashloopbackoff", "error")


_K8SGPT_NOISE = (
    "kube-root-ca.crt",
    "is not used by any pods",
    "is not mounted",
    "unused configmap",
    "no problem detected",
)

_K8SGPT_PRIORITY = (
    "errimagepull",
    "imagepullbackoff",
    "crashloopbackoff",
    "backoff",
    "failed",
    "error",
    "unhealthy",
    "deployment",
    "pod",
    "container",
    "replica",
)


def _is_noisy_k8sgpt_line(text: str) -> bool:
    t = text.lower()
    if any(n in t for n in _K8SGPT_NOISE):
        return True
    return "configmap" in t and ("not used" in t or "unused" in t)


def _k8sgpt_priority(line: str) -> int:
    t = line.lower()
    for i, kw in enumerate(_K8SGPT_PRIORITY):
        if kw in t:
            return i
    return 99


def _incident_context() -> dict:
    """Cluster facts for plain-English explain (not raw k8sgpt noise)."""
    ctx: dict = {
        "replicas": -1,
        "ready_replicas": 0,
        "image": "",
        "pod_name": "",
        "pod_phase": "",
        "pod_reason": "",
        "pod_message": "",
        "pod_line": "",
        "events": [],
    }
    code, out = _kubectl(
        "get", "deployment", cfg.DEPLOYMENT_NAME, "-n", cfg.NAMESPACE,
        "-o", "jsonpath={.spec.replicas}|{.status.readyReplicas}|{.spec.template.spec.containers[0].image}",
    )
    if code == 0 and out.strip():
        parts = out.split("|", 2)
        if parts[0].strip().isdigit():
            ctx["replicas"] = int(parts[0].strip())
        if len(parts) > 1 and parts[1].strip().isdigit():
            ctx["ready_replicas"] = int(parts[1].strip())
        elif len(parts) > 1 and not parts[1].strip():
            ctx["ready_replicas"] = 0
        if len(parts) > 2:
            ctx["image"] = parts[2].strip()

    code, pod_line = _kubectl(
        "get", "pods", "-n", cfg.NAMESPACE, "-l", cfg.POD_LABEL, "--no-headers",
    )
    ctx["pod_line"] = _kubectl_value(code, pod_line, "no pods")
    if code == 0 and pod_line.strip():
        ctx["pod_name"] = pod_line.strip().split()[0]

    code, pod_detail = _kubectl(
        "get", "pods", "-n", cfg.NAMESPACE, "-l", cfg.POD_LABEL,
        "-o", "jsonpath={.items[0].status.phase}|{range .items[0].status.containerStatuses[*]}"
        "{.state.waiting.reason}|{.state.waiting.message}|{.state.terminated.reason}|"
        "{.state.terminated.message}{end}",
    )
    if code == 0 and pod_detail.strip():
        parts = [p for p in pod_detail.split("|") if p]
        if parts:
            ctx["pod_phase"] = parts[0]
        for i in range(1, len(parts), 2):
            if parts[i]:
                ctx["pod_reason"] = parts[i]
                if i + 1 < len(parts):
                    ctx["pod_message"] = parts[i + 1]
                break

    ctx["events"] = _kubectl_events_findings(cfg.NAMESPACE, limit=6, pod_name=ctx["pod_name"])
    return ctx


def _plain_language_explain(ctx: dict) -> tuple[str, list[str], str, str]:
    """Return (headline, bullet facts, root_cause label, simple_paragraph for clients)."""
    replicas = ctx["replicas"]
    ready = ctx["ready_replicas"]
    image = ctx["image"] or "unknown"
    reason = (ctx["pod_reason"] or "").strip()
    reason_l = reason.lower()
    bad_tag = cfg.BAD_IMAGE.split(":")[-1].lower() if cfg.BAD_IMAGE else ""
    image_l = image.lower()

    if replicas == 0:
        return (
            "The staging app has zero running pods — nothing is serving traffic.",
            [
                "We simulated an outage by scaling the FastAPI deployment to 0 replicas.",
                "ArgoCD and the demo UI are still running; only the staging workload is down.",
                "Health checks at /staging/health fail until you run Auto-fix.",
            ],
            "Scaled to 0 replicas (instant outage mode)",
            "In simple terms: we deliberately shut off every running copy of your app — like closing all store "
            "locations at once. Kubernetes shows zero pods, so the staging website stops responding. "
            "This is a safe demo outage; ArgoCD and the control UI are still up.",
        )

    if "errimagepull" in reason_l or "imagepullbackoff" in reason_l or "does-not-exist" in image_l or (
        bad_tag and bad_tag in image_l
    ):
        msg = ctx["pod_message"] or "Registry rejected or could not find the image tag."
        return (
            "The staging app cannot start — Kubernetes cannot pull the container image.",
            [
                f"Deployment is set to image: {image}",
                f"Pod state: {reason or 'ImagePullBackOff'} — {msg[:160]}",
                "This is the intentional demo outage (bad image). GitOps still tracks the good image in Git.",
                "Next step: click Auto-fix to restore the known-good image and sync ArgoCD.",
            ],
            reason or "ErrImagePull / ImagePullBackOff",
            "In simple terms: we pointed the app at a container image that does not exist in the registry — "
            "like giving a delivery driver a wrong address. Kubernetes keeps retrying but the pod never starts, "
            "so clients see the app as down. Your Git repo still has the correct image; only the live cluster is wrong.",
        )

    if "crashloopbackoff" in reason_l or reason_l == "error":
        return (
            "The staging app pod keeps crashing and cannot stay running.",
            [
                f"Pod {ctx['pod_name'] or 'fastapi'} is in {reason or 'CrashLoopBackOff'}.",
                ctx["pod_message"][:160] if ctx["pod_message"] else "Container exits immediately after start (demo crash injection).",
                "The Service has no healthy endpoints, so the app URL returns errors.",
                "Next step: Auto-fix clears the crash override and rolls back to a good deploy.",
            ],
            reason or "CrashLoopBackOff",
            "In simple terms: the app container starts, immediately crashes, and Kubernetes keeps restarting it "
            "in a loop. No stable pod means no traffic can be served — the staging URL will fail until we roll back.",
        )

    if replicas > 0 and ready == 0:
        return (
            "The staging deployment exists but no pods are ready — the app is down.",
            [
                f"Desired replicas: {replicas}, ready: {ready}.",
                f"Latest pod status: {ctx['pod_line'] or 'unknown'}.",
                "Check ArgoCD for Progressing/Degraded while the bad rollout fails.",
            ],
            "No ready replicas",
            "In simple terms: Kubernetes is trying to run the app but none of the pods became healthy. "
            "The deployment exists on paper, yet nothing is ready to serve requests — ArgoCD will show Degraded.",
        )

    if _staging_is_healthy(ctx):
        return (
            "The staging app is healthy — workload recovered successfully.",
            [
                f"Deployment image: {image}",
                f"Pods: {ctx.get('pod_line') or 'unknown'}",
                f"Replicas ready: {ready}/{replicas}.",
                "Argo CD is Synced / Healthy — auto-fix completed.",
            ],
            "Healthy",
            "In simple terms: the app is running normally again. Kubernetes is serving the good image, "
            "the pod is Ready, and GitOps is in sync. No fix is needed unless you want to simulate another outage.",
        )

    return (
        "The staging workload may still be unhealthy — review pod status below.",
        [
            f"Deployment image: {image}",
            f"Pods: {ctx['pod_line'] or 'none listed'}",
        ],
        reason or "Workload unhealthy",
        "In simple terms: something in the staging workload is not healthy. "
        "Review the ArgoCD tree and technical evidence below, then run Auto-fix to restore service.",
    )


def _filter_k8sgpt_findings(lines: list[str]) -> list[str]:
    kept = [ln for ln in lines if ln.strip() and not _is_noisy_k8sgpt_line(ln)]
    kept.sort(key=_k8sgpt_priority)
    return kept[:6]



def _argocd_app_tree(ctx: dict) -> dict:
    """Live Argo CD application tree for the explain panel (mirrors Argo CD resource view)."""
    sync_status, health_status = "Unknown", "Unknown"
    repo_url, repo_path = "", ""
    code, sh = _kubectl(
        "get", "application", cfg.ARGOCD_APP, "-n", cfg.ARGOCD_NAMESPACE,
        "-o", "jsonpath={.status.sync.status}|{.status.health.status}|"
        "{.spec.source.repoURL}|{.spec.source.path}|{.spec.destination.namespace}",
    )
    dest_ns = cfg.NAMESPACE
    if code == 0 and sh.strip():
        parts = sh.split("|", 4)
        if len(parts) > 0 and parts[0]:
            sync_status = parts[0]
        if len(parts) > 1 and parts[1]:
            health_status = parts[1]
        if len(parts) > 2:
            repo_url = parts[2]
        if len(parts) > 3:
            repo_path = parts[3]
        if len(parts) > 4 and parts[4]:
            dest_ns = parts[4]

    repo_short = repo_url.replace("https://github.com/", "").replace(".git", "") if repo_url else "GitHub"

    replicas = ctx.get("replicas", -1)
    ready = ctx.get("ready_replicas", 0)
    pod_name = ctx.get("pod_name") or f"{cfg.DEPLOYMENT_NAME}-…"
    pod_reason = (ctx.get("pod_reason") or ctx.get("pod_phase") or "Unknown").strip()
    image = ctx.get("image") or cfg.GOOD_IMAGE

    deploy_health = "Healthy"
    if replicas == 0 or (replicas > 0 and ready == 0):
        deploy_health = "Degraded"
    elif pod_reason.lower() not in ("running", "completed", ""):
        deploy_health = "Degraded"

    pod_health = "Healthy" if pod_reason.lower() == "running" else "Degraded"
    if replicas == 0:
        pod_health = "Missing"

    svc_health = "Healthy"
    code_svc, _ = _kubectl("get", "service", "fastapi", "-n", cfg.NAMESPACE)
    if code_svc != 0:
        svc_health = "Unknown"

    tree_summary = (
        f"Argo CD app «{cfg.ARGOCD_APP}» is {sync_status} / {health_status}. "
        f"The failing resource is usually the Pod ({pod_reason}) under Deployment {cfg.DEPLOYMENT_NAME}."
    )

    return {
        "app_name": cfg.ARGOCD_APP,
        "sync_status": sync_status,
        "health_status": health_status,
        "source_repo": repo_short,
        "source_path": repo_path or "overlays/oci",
        "destination_namespace": dest_ns,
        "tree_summary": tree_summary,
        "resources": [
            {
                "kind": "Application",
                "name": cfg.ARGOCD_APP,
                "namespace": cfg.ARGOCD_NAMESPACE,
                "health": health_status,
                "sync": sync_status,
                "depth": 0,
            },
            {
                "kind": "Namespace",
                "name": dest_ns,
                "health": "Healthy",
                "depth": 1,
            },
            {
                "kind": "Deployment",
                "name": cfg.DEPLOYMENT_NAME,
                "namespace": dest_ns,
                "health": deploy_health,
                "detail": f"{ready}/{max(replicas, 0)} ready · {image.split('/')[-1][:48]}",
                "depth": 2,
            },
            {
                "kind": "Pod",
                "name": pod_name,
                "namespace": dest_ns,
                "health": pod_health,
                "detail": pod_reason,
                "depth": 3,
                "highlight": True,
            },
            {
                "kind": "Service",
                "name": "fastapi",
                "namespace": dest_ns,
                "health": svc_health,
                "detail": "ClusterIP · port 80",
                "depth": 2,
            },
        ],
    }


def _strip_ansi(text: str) -> str:
    import re
    return re.sub(r"\x1b\[[0-9;]*m", "", text or "").strip()


def _holmes_prompt(ctx: dict) -> str:
    return (
        f"Demo outage in namespace {cfg.NAMESPACE}, deployment {cfg.DEPLOYMENT_NAME}. "
        f"Image: {ctx.get('image') or 'unknown'}. Pod: {ctx.get('pod_line') or 'unknown'}. "
        f"Detail: {ctx.get('pod_reason') or 'unknown'}. "
        "Use at most 2 kubectl read-only commands, then answer in 4 plain-English sentences: "
        "root cause, what happened, and recommended GitOps fix."
    )


def _holmes_failure_hint(code: int, text: str) -> str:
    hint = text or "HolmesGPT did not return output"
    low = hint.lower()
    if "connection refused" in low or "name or service not known" in low or "failed to establish" in low:
        return (
            "Robusta Holmes service not reachable. Install Robusta on OKE: "
            "bash deploy/oci/setup-robusta-holmes.sh in Cloud Shell."
        )
    if code in (137, -9) or "oomkilled" in low or "out of memory" in low:
        return (
            "HolmesGPT ran out of memory in the selfheal-ui pod. "
            "Run: kubectl -n selfheal patch deployment selfheal-ui --type=json "
            '-p=\'[{"op":"replace","path":"/spec/template/spec/containers/0/resources/limits/memory","value":"1536Mi"}]\''
        )
    if code == 127 or re.search(r"(^|/)holmes[:\\s].*not found", low):
        return "HolmesGPT CLI not installed — rebuild image with holmesgpt in requirements.txt"
    if "timed out" in low or code == 1 and "timeout" in low:
        return (
            f"HolmesGPT exceeded {cfg.HOLMES_TIMEOUT}s — try HOLMES_TIMEOUT=300 and HOLMES_MAX_STEPS=10 "
            "in selfheal-ui-config, then re-run Step 3."
        )
    if "api_key" in low or "api key" in low or "unauthorized" in low or "authentication" in low:
        return (
            "HolmesGPT auth failed — check secret k8sgpt-ai gemini-api-key is the key only "
            "(AIza or AQ. prefix, ~40–80 chars, no export/bash text). Recreate with read -s or a temp file."
        )
    if code != 0 and hint:
        return hint[-2000:]
    return hint[-2000:]


def _holmes_http_base_url() -> str:
    url = cfg.HOLMES_HTTP_URL.rstrip("/")
    if url.endswith("/api/chat"):
        return url[: -len("/api/chat")]
    return url


def _holmes_http_available() -> bool:
    probe = f"{_holmes_http_base_url()}/api/model"
    try:
        urllib.request.urlopen(probe, timeout=8)
        return True
    except Exception:
        return False


def _run_holmes_via_http(prompt: str) -> tuple[bool, str, str]:
    """Call in-cluster Holmes server (Robusta Helm) — uses Robusta Cloud AI, no LLM keys in selfheal-ui."""
    url = cfg.HOLMES_HTTP_URL
    payload = {
        "ask": prompt,
        "model": cfg.HOLMES_HTTP_MODEL,
        "behavior_controls": {
            "todowrite_instructions": False,
            "todowrite_reminder": False,
        },
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=cfg.HOLMES_HTTP_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw)
            analysis = (data.get("analysis") or "").strip()
            if analysis:
                return True, analysis[:4000], raw[-8000:]
            return False, "", raw[-2000:] or "Holmes HTTP returned empty analysis"
    except urllib.error.HTTPError as exc:
        err = exc.read().decode("utf-8", errors="replace") if exc.fp else str(exc)
        return False, "", err[-2000:]
    except Exception as exc:
        return False, "", str(exc)[-2000:]


def _run_holmes_cli_prompt(prompt: str, max_steps: int | None = None) -> tuple[bool, str, str]:
    if shutil.which(cfg.HOLMES_BIN) is None:
        return False, "", "HolmesGPT CLI not installed — rebuild image with holmesgpt in requirements.txt"
    model = cfg.resolved_holmes_model()
    steps = max_steps if max_steps is not None else int(
        os.environ.get("HOLMES_MAX_STEPS", getattr(cfg, "HOLMES_MAX_STEPS", 10))
    )
    cmd = [
        cfg.HOLMES_BIN,
        "ask",
        prompt,
        "--model",
        model,
        "--max-steps",
        str(steps),
        "--no-interactive",
    ]
    holmes_env: dict[str, str] = {}
    if model.startswith("gemini/"):
        holmes_env["TOOL_SCHEMA_NO_PARAM_OBJECT_IF_NO_PARAMS"] = "true"
    holmes_env["HOLMES_TOOLSET_PREREQ_TIMEOUT_SECONDS"] = "8"
    code, out = _run(cmd, timeout=cfg.HOLMES_TIMEOUT, extra_env=holmes_env)
    text = _strip_ansi(out)
    if code != 0 or not text:
        return False, "", _holmes_failure_hint(code, text)
    blocks = [b.strip() for b in text.split("\n\n") if b.strip()]
    summary = blocks[-1] if blocks else text
    if len(summary) < 80 and len(blocks) > 1:
        summary = "\n\n".join(blocks[-2:])
    return True, summary[:4000], text[-8000:]


def _run_holmes_cli(ctx: dict) -> tuple[bool, str, str]:
    return _run_holmes_cli_prompt(_holmes_prompt(ctx))


def _holmes_detail_snippet(text: str, max_len: int = 160) -> str:
    if not text:
        return "No output"
    clean = _strip_ansi(text)
    low = clean.lower()
    if "traceback" in low or "warnings.warn" in low:
        if "timed out" in low:
            return "Command timed out — increase HOLMES_TIMEOUT or reduce HOLMES_MAX_STEPS"
        if "404" in clean and "not found" in low:
            return "Holmes model not found — set HOLMES_MODEL=gemini/gemini-3.5-flash"
        return "HolmesGPT did not complete — use HOLMES_MODE=cli with your Gemini key"
    return clean[:max_len]


def _run_holmes_investigation(ctx: dict) -> tuple[bool, str, str]:
    """Run HolmesGPT agentic RCA (read-only). Returns ok, summary text, raw output."""
    if not cfg.HOLMES_ENABLED:
        return False, "", "HolmesGPT disabled"

    mode = (cfg.HOLMES_MODE or "cli").strip().lower()
    has_llm_key = any(
        os.environ.get(k, "").strip()
        for k in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY")
    )

    # Prefer CLI + Gemini when configured (Robusta HTTP often unavailable on demo clusters).
    if mode == "cli" or has_llm_key:
        return _run_holmes_cli(ctx)

    if mode in ("robusta", "auto"):
        if _holmes_http_available():
            ok, summary, raw = _run_holmes_via_http(_holmes_prompt(ctx))
            if ok:
                return ok, summary, raw
            if mode == "robusta":
                return False, "", _holmes_failure_hint(1, raw)
        return _run_holmes_cli(ctx)

    return _run_holmes_cli(ctx)


def holmes_snapshot() -> dict:
    """Live cluster facts for the Holmes chat sidebar (no LLM)."""
    ctx = _incident_context()
    tree = _argocd_app_tree(ctx)
    healthy = _staging_is_healthy(ctx)
    return {
        "ok": True,
        "healthy": healthy,
        "holmes_enabled": cfg.HOLMES_ENABLED,
        "model": cfg.resolved_holmes_model(),
        "namespace": cfg.NAMESPACE,
        "deployment": cfg.DEPLOYMENT_NAME,
        "image": ctx.get("image", ""),
        "image_short": (ctx.get("image") or "").split("/")[-1],
        "pod_line": ctx.get("pod_line", ""),
        "pod_name": ctx.get("pod_name", ""),
        "pod_reason": ctx.get("pod_reason", ""),
        "replicas": ctx.get("replicas", -1),
        "ready_replicas": ctx.get("ready_replicas", 0),
        "argocd_sync": tree.get("sync_status", "Unknown"),
        "argocd_health": tree.get("health_status", "Unknown"),
        "tree_summary": tree.get("tree_summary", ""),
    }


def _holmes_cluster_facts(ctx: dict, tree: dict) -> str:
    return "\n".join([
        f"namespace: {cfg.NAMESPACE}",
        f"deployment: {cfg.DEPLOYMENT_NAME}",
        f"image: {ctx.get('image') or 'unknown'}",
        f"pod: {ctx.get('pod_line') or 'none'}",
        f"pod_reason: {ctx.get('pod_reason') or 'none'}",
        f"replicas_ready: {ctx.get('ready_replicas', 0)}/{ctx.get('replicas', -1)}",
        f"staging_healthy: {_staging_is_healthy(ctx)}",
        f"argocd_sync: {tree.get('sync_status', 'Unknown')}",
        f"argocd_health: {tree.get('health_status', 'Unknown')}",
    ])


def _try_holmes_fast_answer(message: str, ctx: dict, tree: dict) -> str | None:
    """Instant accurate answers from live kubectl — no LLM latency."""
    q = message.lower().strip()
    healthy = _staging_is_healthy(ctx)
    image = ctx.get("image") or "unknown"
    image_short = image.split("/")[-1]
    pod_line = ctx.get("pod_line") or "no pods"
    sync = tree.get("sync_status", "Unknown")
    argo_health = tree.get("health_status", "Unknown")
    ready = ctx.get("ready_replicas", 0)
    replicas = ctx.get("replicas", -1)

    if re.match(r"^(hi|hello|hey)\b", q):
        status = "healthy and running" if healthy else "not fully healthy"
        return (
            f"Hello! I'm HolmesGPT on **{cfg.NAMESPACE}**. Right now fastapi looks **{status}**.\n\n"
            f"- **Pod:** {pod_line}\n"
            f"- **Image:** `{image_short}`\n"
            f"- **Argo CD:** {sync} / {argo_health}\n\n"
            "Ask me to investigate deeper, summarize for a client, or explain an outage."
        )

    health_q = any(w in q for w in ("healthy", "health", "running", "status", " up", "down"))
    deep_q = any(w in q for w in ("why", "wrong", "broke", "fix", "investigate", "explain", "root cause"))
    if health_q and not deep_q:
        if healthy:
            return (
                "**Yes — your staging pod looks healthy.**\n\n"
                f"- **Pod:** {pod_line}\n"
                f"- **Image:** `{image}`\n"
                f"- **Replicas ready:** {ready}/{replicas}\n"
                f"- **Argo CD:** {sync} / {argo_health}\n\n"
                "This answer uses **live kubectl state** (not stale warning events). "
                "Ask a follow-up if you want a deeper Holmes investigation."
            )
        reason = ctx.get("pod_reason") or "not ready"
        return (
            "**No — the staging workload is not healthy.**\n\n"
            f"- **Pod:** {pod_line}\n"
            f"- **Likely issue:** {reason}\n"
            f"- **Image:** `{image}`\n"
            f"- **Argo CD:** {sync} / {argo_health}\n\n"
            "Say *investigate the outage* for a full HolmesGPT RCA."
        )

    if "image" in q and any(w in q for w in ("what", "which", "tag", "using")):
        return f"The deployment is using:\n\n`{image}`\n\n**Pod snapshot:** {pod_line}"

    if any(w in q for w in ("argo", "gitops", "sync")):
        return (
            f"**Argo CD app `{cfg.ARGOCD_APP}`**\n\n"
            f"- **Sync:** {sync}\n"
            f"- **Health:** {argo_health}\n"
            f"- **Namespace:** {cfg.NAMESPACE}\n\n"
            f"{tree.get('tree_summary', '')}"
        )

    if any(w in q for w in ("recovered", "outage", "still broken", "still down")):
        if healthy:
            return (
                "**Recovery looks complete.** The current pod is Running and replicas are ready.\n\n"
                f"- {pod_line}\n"
                f"- Argo CD: {sync} / {argo_health}\n\n"
                "Older ErrImagePull events may still appear in history — ignore them unless the **current** pod is failing."
            )
        return (
            "**Outage still active** on the live workload.\n\n"
            f"- {pod_line}\n"
            f"- Issue: {ctx.get('pod_reason') or 'unknown'}\n\n"
            "Run **Auto-fix** on the guided demo or ask me to investigate root cause."
        )

    return None


def holmes_chat(message: str, on_step: StepCallback = None) -> dict:
    """Free-form HolmesGPT Q&A — separate from the guided demo Step 3."""
    text = (message or "").strip()
    if not text:
        raise ValueError("Message is required")
    if not cfg.HOLMES_ENABLED:
        raise RuntimeError(
            "HolmesGPT is disabled. Set HOLMES_ENABLED=true and configure a Gemini key in secret k8sgpt-ai."
        )
    ok_cluster, cluster_msg = _cluster_reachable()
    if not ok_cluster:
        raise RuntimeError(cluster_msg)

    def step(title: str, detail: str = "", phase: str = "cluster") -> None:
        if on_step:
            on_step({"title": title, "detail": detail, "phase": phase})

    step("Reading live cluster state", f"namespace {cfg.NAMESPACE}", "cluster")
    ctx = _incident_context()
    tree = _argocd_app_tree(ctx)
    healthy = _staging_is_healthy(ctx)

    step("Checking telemetry", "Argo CD + pod status", "cluster")
    fast = _try_holmes_fast_answer(text, ctx, tree)
    if fast:
        step("Answer ready", "Live cluster telemetry (instant)", "done")
        return {
            "ok": True,
            "reply": fast,
            "error": "",
            "source": "telemetry",
            "model": cfg.resolved_holmes_model(),
            "context": {
                "namespace": cfg.NAMESPACE,
                "deployment": cfg.DEPLOYMENT_NAME,
                "image": ctx.get("image", ""),
                "pod_line": ctx.get("pod_line", ""),
                "healthy": healthy,
                "argocd_sync": tree.get("sync_status"),
                "argocd_health": tree.get("health_status"),
            },
            "raw": "",
        }

    step("Running HolmesGPT deep scan", "Agentic RCA — read-only kubectl", "ai")
    chat_steps = int(os.environ.get("HOLMES_CHAT_MAX_STEPS", "8"))
    facts = _holmes_cluster_facts(ctx, tree)
    prompt = (
        f"User question: {text}\n\n"
        f"LIVE CLUSTER FACTS (authoritative — trust over stale events):\n{facts}\n\n"
        "If facts show staging_healthy: true, state clearly the app is healthy. "
        "Use at most 2 read-only kubectl commands only if facts are insufficient. "
        "Reply in plain English with sections: **Status**, **Evidence**, **Recommendation**."
    )
    holmes_ok, reply, raw = _run_holmes_cli_prompt(prompt, max_steps=chat_steps)
    model = cfg.resolved_holmes_model()
    step(
        "HolmesGPT complete" if holmes_ok else "HolmesGPT could not finish",
        (reply[:100] + "…") if holmes_ok and len(reply) > 100 else (reply or _holmes_detail_snippet(raw)),
        "done" if holmes_ok else "error",
    )
    return {
        "ok": holmes_ok,
        "reply": reply if holmes_ok else "",
        "error": "" if holmes_ok else _holmes_detail_snippet(raw, 400),
        "source": "holmes",
        "model": model,
        "context": {
            "namespace": cfg.NAMESPACE,
            "deployment": cfg.DEPLOYMENT_NAME,
            "image": ctx.get("image", ""),
            "pod_line": ctx.get("pod_line", ""),
            "healthy": healthy,
            "argocd_sync": tree.get("sync_status"),
            "argocd_health": tree.get("health_status"),
        },
        "raw": raw[-2000:] if raw else "",
    }


def explain_with_ai(on_step: StepCallback = None) -> dict:
    return _with_step_stream(on_step, _explain_with_ai_impl)


def _explain_with_ai_impl() -> dict:
    ok, cluster_msg = _cluster_reachable()
    if not ok:
        raise RuntimeError(cluster_msg)

    timeline: list[dict[str, str]] = []
    _timeline_step(timeline, "Starting AI-assisted diagnosis", "Read-only — no changes to the cluster")
    _timeline_step(timeline, "Reading deployment spec and replica status", f"deployment/{cfg.DEPLOYMENT_NAME}")
    ctx = _incident_context()
    _timeline_step(timeline, "Inspecting pod state and container waiting reasons", ctx["pod_line"] or "no pods")
    _timeline_step(timeline, "Collecting recent Kubernetes warning events", cfg.NAMESPACE)

    summary, what_happened, root_cause, simple_explanation = _plain_language_explain(ctx)

    _timeline_step(timeline, "Loading Argo CD application tree", cfg.ARGOCD_APP)
    argocd_tree = _argocd_app_tree(ctx)

    _timeline_step(timeline, "Running k8sgpt analyzers on namespace", "Filters noise — focuses on workload failures")
    code, out = _run(
        [cfg.K8SGPT_BIN, "analyze", "--namespace", cfg.NAMESPACE, "--no-cache"],
        timeout=cfg.K8SGPT_TIMEOUT,
    )
    if code != 0 and "openai" in out.lower():
        code, out = _run(
            [cfg.K8SGPT_BIN, "analyze", "--namespace", cfg.NAMESPACE, "--no-cache", "--backend", "noopai"],
            timeout=cfg.K8SGPT_TIMEOUT,
        )

    _timeline_step(
        timeline,
        "k8sgpt analysis complete",
        "Open-source Kubernetes scanners — findings in technical evidence below",
        phase="ai",
        pause=False,
    )

    holmes_ok = False
    holmes_summary = ""
    holmes_raw = ""
    if cfg.HOLMES_ENABLED:
        _timeline_step(
            timeline,
            "Running HolmesGPT investigation",
            "Agentic RCA — read-only cluster analysis",
            phase="ai",
        )
        try:
            holmes_ok, holmes_summary, holmes_raw = _run_holmes_investigation(ctx)
        except Exception as exc:
            holmes_raw = str(exc)
        detail = (
            (holmes_summary[:120] + "…")
            if holmes_ok and len(holmes_summary) > 120
            else (holmes_summary or _holmes_detail_snippet(holmes_raw) or "No output")
        )
        _timeline_step(
            timeline,
            "HolmesGPT investigation complete" if holmes_ok else "HolmesGPT unavailable",
            detail,
            phase="ai",
            pause=False,
        )

    raw_lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    priority_lines = [
        s for s in raw_lines
        if any(k in s for k in ("Error", "BackOff", "ErrImagePull", "failed", "Problem", "ImagePull", "CrashLoop"))
    ]
    findings = _filter_k8sgpt_findings(priority_lines or raw_lines)
    if not findings:
        findings = _filter_k8sgpt_findings(ctx["events"])

    if _staging_is_healthy(ctx):
        findings = [
            f for f in findings
            if not any(x in f.lower() for x in ("errimagepull", "imagepullbackoff", "does-not-exist"))
        ]

    technical: list[str] = []
    for ev in ctx["events"][:3]:
        if ev not in findings and not _is_noisy_k8sgpt_line(ev):
            technical.append(ev)
    for f in findings:
        if f not in technical:
            technical.append(f)

    if holmes_ok and holmes_summary:
        simple_explanation = holmes_summary
        if root_cause and root_cause.lower() not in holmes_summary.lower():
            summary = f"{root_cause} — HolmesGPT correlated live cluster signals."

    _timeline_step(timeline, "Building plain-English summary for your client", root_cause, pause=False)

    used_fallback = code != 0 and bool(findings)
    has_signal = bool(what_happened) or bool(technical)
    healthy = _staging_is_healthy(ctx)
    next_step = (
        "No action required — staging is healthy. Use Step 2 to simulate another outage, or Reset for a from-zero deploy."
        if healthy
        else "Step 4 — click Auto-fix to restore the good image, re-enable GitOps sync, and verify health."
    )

    return {
        "ok": has_signal,
        "summary": summary,
        "simple_explanation": simple_explanation,
        "what_happened": what_happened,
        "root_cause": root_cause,
        "next_step": next_step,
        "findings": technical[:6],
        "holmes_ok": holmes_ok,
        "holmes_enabled": cfg.HOLMES_ENABLED,
        "holmes_investigation": holmes_summary,
        "holmes_raw": holmes_raw[-3000:] if holmes_raw else "",
        "argocd_tree": argocd_tree,
        "incident": {
            "image": ctx.get("image", ""),
            "pod_name": ctx.get("pod_name", ""),
            "pod_reason": ctx.get("pod_reason", ""),
            "pod_line": ctx.get("pod_line", ""),
            "replicas": ctx.get("replicas", -1),
            "ready_replicas": ctx.get("ready_replicas", 0),
        },
        "timeline": timeline,
        "raw": out[-2500:],
        "message": summary,
        "hint": (
            ""
            if code == 0
            else "Tip: for full AI explain, run in Cloud Shell: "
            "kubectl -n selfheal exec deploy/selfheal-ui -- k8sgpt auth add -b noopai"
        ) if not used_fallback else "",
    }


def auto_fix(on_step: StepCallback = None) -> dict:
    return _with_step_stream(on_step, _auto_fix_impl)


def _auto_fix_impl() -> dict:
    ok, cluster_msg = _cluster_reachable()
    if not ok:
        raise RuntimeError(cluster_msg)

    timeline: list[dict[str, str]] = []
    _timeline_step(timeline, "Starting GitOps recovery", "AI explained only — this step applies the fix")
    argo_note = ""
    heal_error: Exception | None = None
    try:
        staging = cfg.STAGING_APP_PATH
        if cfg.DEPLOY_TARGET != "oci" and staging.exists():
            _timeline_step(timeline, "Applying staging manifests", str(staging))
            code, apply_out = _run(_kubectl_cmd("apply", "-f", str(staging)), timeout=60)
            if code != 0:
                _kubectl(
                    "delete", "deployment", cfg.DEPLOYMENT_NAME, "-n", cfg.NAMESPACE,
                    "--ignore-not-found",
                )
                code, apply_out = _run(_kubectl_cmd("apply", "-f", str(staging)), timeout=60)
            if code != 0:
                raise RuntimeError(f"Apply staging app failed: {apply_out}")

        _timeline_step(timeline, "Scaling deployment to 1 replica", cfg.NAMESPACE)
        _kubectl_must(
            "scale", f"deployment/{cfg.DEPLOYMENT_NAME}",
            "-n", cfg.NAMESPACE, "--replicas=1",
            action="Scale staging app up",
        )
        _timeline_step(timeline, "Restoring known-good container image", cfg.GOOD_IMAGE)
        _clear_crash_override()
        _kubectl_must(
            "set", "image",
            f"deployment/{cfg.DEPLOYMENT_NAME}",
            f"{cfg.CONTAINER_NAME}={cfg.GOOD_IMAGE}",
            "-n", cfg.NAMESPACE,
            action="Restore good image",
        )
        _kubectl("delete", "pods", "-n", cfg.NAMESPACE, "-l", cfg.POD_LABEL, "--wait=false")
        _wait_rollout_with_steps(
            timeline,
            timeout=90,
            title="Waiting for rollout to complete",
        )
    except Exception as e:
        heal_error = e
    finally:
        _timeline_step(timeline, "Re-enabling ArgoCD auto-sync", "GitOps policy restored from manifest")
        restore_code, restore_out = _argocd_restore_gitops_policy()
        if restore_code != 0:
            argo_note += f" (ArgoCD restore skipped: {(restore_out or '')[:100]})"
        _argocd_refresh()
        _timeline_step(timeline, "Triggering ArgoCD sync", "Cluster state → Git → Healthy")
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
    _timeline_step(timeline, "Verifying health endpoint", cfg.APP_HEALTH_CHECK_URL, pause=True)
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
    _timeline_step(
        timeline,
        "Recovery complete" if recovered else "Recovery applied — verify status",
        "App and GitOps should be green again" if recovered else _pod_troubleshoot(),
        pause=False,
    )
    return {
        "timeline": timeline,
        "argocd": _kubectl_value(code, argo),
        "health": health,
        "app_reachable": recovered,
        "open_url": links.get("app_dashboard") or links.get("app_health", cfg.PUBLIC_APP_HEALTH_URL),
        "staging_url": links.get("app_dashboard"),
        "message": (
            ("App recovered — health check passed. Demo complete." if recovered else f"Heal applied — {_pod_troubleshoot()}")
            + argo_note
        ),
    }
