"""Kubernetes self-heal demo — Oracle OKE or local kind."""
from __future__ import annotations

import base64
import json
import logging
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

log = logging.getLogger(__name__)

StepCallback = Callable[[dict], None] | None
_active_step_cb: StepCallback = None
_last_gemini_failure: dict | None = None


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


def resolved_public_app_links() -> dict[str, dict[str, str]]:
    """Browser links for each demo app, plus shared Argo CD entrypoints."""
    app_links = {k: dict(v) for k, v in cfg.public_app_links().items()}
    shared = resolved_public_links()
    ui_host = _lb_host("selfheal", "selfheal-ui")
    argo_base = shared.get("argocd", "")

    fastapi = app_links.get("fastapi", {})
    if not _valid_browser_url(fastapi.get("health", "")) and ui_host:
        fastapi["health"] = f"http://{ui_host}/staging/health"
        fastapi["dashboard"] = f"http://{ui_host}/staging/"
    if not _valid_browser_url(fastapi.get("dashboard", "")) and shared.get("app_dashboard"):
        fastapi["dashboard"] = shared["app_dashboard"]
    if not _valid_browser_url(fastapi.get("health", "")) and shared.get("app_health"):
        fastapi["health"] = shared["app_health"]
    if not _valid_browser_url(fastapi.get("argocd_app", "")) and argo_base:
        fastapi["argocd_app"] = f"{argo_base.rstrip('/')}/applications/{cfg.ARGOCD_NAMESPACE}/{cfg.ARGOCD_APP}"
    fastapi["argocd"] = argo_base
    app_links["fastapi"] = fastapi

    nginx = app_links.get("nginx", {})
    if not _valid_browser_url(nginx.get("health", "")) and ui_host:
        nginx["health"] = f"http://{ui_host}/nginx/"
        nginx["dashboard"] = f"http://{ui_host}/nginx/"
    if not _valid_browser_url(nginx.get("argocd_app", "")) and argo_base:
        nginx["argocd_app"] = f"{argo_base.rstrip('/')}/applications/{cfg.ARGOCD_NAMESPACE}/{cfg.NGINX_ARGOCD_APP}"
    nginx["argocd"] = argo_base
    app_links["nginx"] = nginx

    for links in app_links.values():
        for key, val in list(links.items()):
            if not _valid_browser_url(val):
                links[key] = ""
    return app_links


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


def _argocd_trigger_sync_named(app_name: str) -> tuple[int, str]:
    patch = _patch_file(
        f"argocd-sync-op-{app_name}.json",
        '{"operation":{"initiatedBy":{"username":"selfheal-ui"},"sync":{"revision":"HEAD"}}}',
    )
    return _kubectl(
        "patch", "application", app_name, "-n", cfg.ARGOCD_NAMESPACE,
        "--type", "merge", f"--patch-file={patch}",
    )


def _argocd_sync_status_named(app_name: str) -> str:
    code, out = _kubectl(
        "get", "application", app_name, "-n", cfg.ARGOCD_NAMESPACE,
        "-o", "jsonpath={.status.sync.status}/{.status.health.status}",
    )
    return _kubectl_value(code, out, "Unknown/Unknown")


def _argocd_wait_synced_named(app_name: str, timeout: int = 90) -> str:
    deadline = time.time() + timeout
    last = _argocd_sync_status_named(app_name)
    while time.time() < deadline:
        if last.startswith("Synced/"):
            return last
        time.sleep(3)
        _argocd_refresh_named(app_name)
        last = _argocd_sync_status_named(app_name)
    return last


def _argocd_refresh_named(app_name: str) -> None:
    _kubectl(
        "annotate", "application", app_name, "-n", cfg.ARGOCD_NAMESPACE,
        "argocd.argoproj.io/refresh=hard", "--overwrite",
    )


def _argocd_set_automated_named(app_name: str, enabled: bool) -> tuple[int, str]:
    body = (
        '{"spec":{"syncPolicy":{"automated":{"prune":true,"selfHeal":true}}}}'
        if enabled
        else '{"spec":{"syncPolicy":{"automated":null}}}'
    )
    patch = _patch_file(f"argocd-sync-{app_name}.json", body)
    return _kubectl(
        "patch", "application", app_name, "-n", cfg.ARGOCD_NAMESPACE,
        "--type", "merge", f"--patch-file={patch}",
    )


def _argocd_app_exists_named(app_name: str) -> bool:
    code, _ = _kubectl("get", "application", app_name, "-n", cfg.ARGOCD_NAMESPACE)
    return code == 0


def _wait_argocd_app_status_named(app_name: str, timeout: int = 120, want_health: str = "Healthy") -> str:
    """Poll until Application reports health (returns sync/health string)."""
    deadline = time.time() + timeout
    last = "Unknown/Unknown"
    while time.time() < deadline:
        if not _argocd_app_exists_named(app_name):
            time.sleep(3)
            continue
        code, sh = _kubectl(
            "get", "application", app_name, "-n", cfg.ARGOCD_NAMESPACE,
            "-o", "jsonpath={.status.sync.status}/{.status.health.status}",
        )
        last = _kubectl_value(code, sh, last)
        if want_health in last:
            return last
        _argocd_refresh_named(app_name)
        time.sleep(4)
    return last


def _register_argocd_app_manifest(manifest: Path) -> tuple[int, str]:
    if not manifest.exists():
        return 1, f"Application manifest not found: {manifest}"
    return _kubectl("apply", "-f", str(manifest))


def _unregister_argocd_app_named(app_name: str) -> tuple[int, str]:
    return _kubectl(
        "delete", "application", app_name,
        "-n", cfg.ARGOCD_NAMESPACE,
        "--wait=false",
        "--ignore-not-found",
    )


def _argocd_trigger_sync() -> tuple[int, str]:
    return _argocd_trigger_sync_named(cfg.ARGOCD_APP)


def _argocd_sync_status() -> str:
    return _argocd_sync_status_named(cfg.ARGOCD_APP)


def _argocd_wait_synced(timeout: int = 90) -> str:
    return _argocd_wait_synced_named(cfg.ARGOCD_APP, timeout=timeout)


def _argocd_refresh() -> None:
    _argocd_refresh_named(cfg.ARGOCD_APP)


def _argocd_set_automated(enabled: bool) -> tuple[int, str]:
    return _argocd_set_automated_named(cfg.ARGOCD_APP, enabled)


def _argocd_app_exists() -> bool:
    return _argocd_app_exists_named(cfg.ARGOCD_APP)


def _wait_argocd_app_status(timeout: int = 120, want_health: str = "Healthy") -> str:
    return _wait_argocd_app_status_named(cfg.ARGOCD_APP, timeout=timeout, want_health=want_health)


def _register_argocd_app() -> tuple[int, str]:
    return _register_argocd_app_manifest(cfg.ARGOCD_APP_MANIFEST)


def _unregister_argocd_app() -> tuple[int, str]:
    """Remove fastapi-staging from Argo CD (prune deletes staging workloads)."""
    return _unregister_argocd_app_named(cfg.ARGOCD_APP)


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


def platform_status_for_app(
    app_id: str = "fastapi",
    *,
    health_timeout: int = 2,
    resolve_links: bool = True,
) -> dict:
    """Per-demo-app status for the guided wizard (FastAPI or Nginx)."""
    app = cfg.demo_app(app_id)
    app_links = resolved_public_app_links().get(app_id, {}) if resolve_links else cfg.public_app_links().get(app_id, {})
    ok, cluster_msg = _cluster_reachable()
    checks: dict = {
        "cluster": "ok" if ok else "fail",
        "cluster_message": cluster_msg,
        "demo_app": app_id,
        "demo_app_label": app["label"],
        "argocd_app_name": app.get("argocd_app") or "",
        **cfg.runtime_info(),
        "links": {
            **(resolved_public_links() if resolve_links else cfg.public_links()),
            "app_dashboard": app_links.get("dashboard", ""),
            "app_health": app_links.get("health", ""),
            "argocd_app": app_links.get("argocd_app", ""),
        },
    }
    health_url = app.get("health_url") or ""
    checks["app_health_check"] = "ok" if _reachable(health_url, timeout=health_timeout) else "fail"
    checks["deployments"] = "ok" if _argocd_reachable() else "fail"

    if not ok:
        checks["pod"] = "cluster offline"
        checks["gitops_app"] = "cluster offline"
        checks["argocd_app_exists"] = False
        checks["workloads_exist"] = False
        checks["app_deployed"] = False
        checks["app_clean"] = False
        checks["app_status_message"] = cluster_msg
        if app_id == "fastapi":
            checks["staging_deployed"] = False
            checks["staging_clean"] = False
        return checks

    argo_name = app.get("argocd_app") or ""
    if argo_name:
        code, argo = _kubectl(
            "get", "application", argo_name, "-n", cfg.ARGOCD_NAMESPACE,
            "-o", "jsonpath={.status.sync.status}/{.status.health.status}",
        )
        checks["gitops_app"] = _kubectl_value(code, argo, "Not registered")
        checks["argocd_app_exists"] = _argocd_app_exists_named(argo_name)
    else:
        checks["gitops_app"] = "n/a"
        checks["argocd_app_exists"] = False

    checks["workloads_exist"] = _app_workloads_exist(app)
    checks["pod"] = _app_pod_summary(app) if checks["workloads_exist"] else "no pods"
    pod_line = checks["pod"]
    pod_ok = "1/1" in pod_line and "Running" in pod_line
    gitops_parts = (checks["gitops_app"] or "").split("/", 1)
    gitops_health = gitops_parts[1] if len(gitops_parts) > 1 else ""

    if app.get("gitops"):
        checks["app_deployed"] = (
            checks["argocd_app_exists"]
            and checks["app_health_check"] == "ok"
            and pod_ok
            and gitops_health == "Healthy"
        )
    else:
        checks["app_deployed"] = checks["workloads_exist"] and pod_ok and checks["app_health_check"] == "ok"

    checks["app_clean"] = (
        not checks["argocd_app_exists"]
        and not checks["workloads_exist"]
        and checks["app_health_check"] != "ok"
    )
    checks["app_reachable"] = checks["app_health_check"] == "ok"

    if checks["app_deployed"]:
        checks["app_status_message"] = (
            f"{app['label']} is live — Argo CD {checks['gitops_app']}. Continue to Step 2."
            if app.get("gitops")
            else f"{app['label']} is live. Continue to Step 2."
        )
    elif checks["app_clean"]:
        checks["app_status_message"] = (
            f"Clean slate — {app['label']} is down and not in Argo CD. Step 1 deploys from Git."
        )
    elif not checks["argocd_app_exists"] and checks["workloads_exist"]:
        checks["app_status_message"] = (
            f"GitOps app removed but {app['label']} workloads still exist — run Reset again."
        )
    elif not checks["argocd_app_exists"]:
        checks["app_status_message"] = (
            f"{app['label']} is not registered in Argo CD — Step 1 will deploy the GitOps app."
        )
    elif not pod_ok or checks["app_health_check"] != "ok" or (app.get("gitops") and gitops_health != "Healthy"):
        checks["app_status_message"] = (
            f"Outage in progress — {app['label']} is down on purpose. Continue to Step 3 Explain, then Step 4 Auto-fix."
        )
    else:
        checks["app_status_message"] = ""

    if app_id == "fastapi":
        checks["staging_deployed"] = checks["app_deployed"]
        checks["staging_clean"] = checks["app_clean"]
        checks["staging_status_message"] = checks["app_status_message"]
        checks["app_health"] = checks["app_health_check"]
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
        "app_clean": clean,
        "app_deployed": False,
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


def _normalize_query(message: str) -> str:
    """Fix common typos so intent matching stays reliable."""
    q = message.lower().strip()
    for typo, fix in (
        ("sattus", "status"),
        ("staus", "status"),
        ("statu ", "status "),
        ("frech", "french"),
        ("frnech", "french"),
        ("leyman", "layman"),
        ("manaually", "manually"),
        ("manaual", "manual"),
        ("tell em", "tell me"),
        ("pods detail", "pod details"),
        ("pod detail", "pod details"),
    ):
        q = q.replace(typo, fix)
    return q


_INTENT_FILLERS = re.compile(
    r"\b(actually|exactly|really|literally|just|simply|please)\b",
    re.I,
)

_ROOT_CAUSE_PHRASES = (
    "what broke",
    "what break",
    "what happened",
    "what went wrong",
    "root cause",
    "what failed",
    "what's broken",
    "whats broken",
)


def _intent_query(message: str) -> str:
    """Normalized query with language clauses and filler words stripped."""
    q = _telemetry_intent_query(message)
    q = _INTENT_FILLERS.sub("", q)
    return re.sub(r"\s+", " ", q).strip()


_GREETING_RE = re.compile(
    r"^(hi+|hey+|hello+|howdy+|yo+|sup+)(\s+there)?\s*[!?.]*$|"
    r"^(how are you|how r u|how's it going|how is it going)\s*[!?.]*$",
    re.I,
)


def _is_greeting(message: str) -> bool:
    q = _telemetry_intent_query(message).strip().rstrip("!?.")
    return bool(_GREETING_RE.match(q))


def _is_root_cause_question(message: str) -> bool:
    q = _intent_query(message)
    if any(p in q for p in _ROOT_CAUSE_PHRASES):
        return True
    return any(p in q for p in ("what is wrong", "what's wrong", "whats wrong", "wrong with"))


def _requested_language(message: str) -> str:
    """Detect language the user wants the reply in (default English)."""
    q = message.lower()
    if any(p in q for p in ("french", "français", "en français", "in french")):
        return "fr"
    if any(p in q for p in ("hindi", "हिंदी", "in hindi", "hindi me")):
        return "hi"
    if any(p in q for p in ("spanish", "español", "in spanish")):
        return "es"
    return "en"


def _strip_language_clauses(message: str) -> str:
    """Remove language directives so telemetry matchers don't see mixed intents."""
    q = message.lower()
    for phrase in (
        "in hindi", "in french", "in french language", "in spanish", "in english",
        "en français", "en hindi", "हिंदी में", "hindi me", "french language",
    ):
        q = q.replace(phrase, "")
    return re.sub(r"\s+", " ", q).strip()


def _telemetry_intent_query(message: str) -> str:
    """Normalized query for allow-list matching (language clauses stripped)."""
    return _normalize_query(_strip_language_clauses(message))


def _resolved_language(message: str, history: list[dict] | None = None) -> str:
    """Language for this turn — explicit in message, or inherited from recent user turns."""
    lang = _requested_language(message)
    if lang != "en":
        return lang
    bare = message.lower().strip()
    if bare in ("hindi", "french", "spanish", "français"):
        return _requested_language(f"in {bare}")
    if history:
        for turn in reversed(history[-4:]):
            if turn.get("role") == "user":
                prev = _requested_language(str(turn.get("content", "")))
                if prev != "en" and len(bare) < 40:
                    return prev
    return "en"


_POD_BAD_STATES = (
    "errimagepull",
    "imagepullbackoff",
    "crashloopbackoff",
    "createcontainererror",
    "invalidimagename",
    "error",
)


def _pod_severity(pod: dict) -> int:
    reason = (pod.get("reason") or pod.get("status") or "").lower()
    for i, bad in enumerate(_POD_BAD_STATES):
        if bad in reason:
            return i
    if pod.get("ready") != "1/1":
        return 10
    if (pod.get("phase") or "").lower() not in ("running", "succeeded"):
        return 12
    return 99


def _fetch_pods_structured() -> list[dict]:
    """All pods for the workload — JSON for accurate ready/reason (not items[0] guess)."""
    code, out = _kubectl(
        "get", "pods", "-n", cfg.NAMESPACE, "-l", cfg.POD_LABEL, "-o", "json",
    )
    if code != 0 or not out.strip():
        return []
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return []

    pods: list[dict] = []
    for item in data.get("items", []):
        meta = item.get("metadata", {})
        status = item.get("status", {})
        name = meta.get("name", "")
        phase = status.get("phase", "Unknown")
        ready_count = 0
        total = 0
        reason = ""
        message = ""
        for cs in status.get("containerStatuses") or []:
            total += 1
            if cs.get("ready"):
                ready_count += 1
            st = cs.get("state", {})
            waiting = st.get("waiting") or {}
            terminated = st.get("terminated") or {}
            if waiting.get("reason"):
                reason = waiting["reason"]
                message = waiting.get("message", "") or message
            elif terminated.get("reason") and not reason:
                reason = terminated["reason"]
                message = terminated.get("message", "") or message
        ready_str = f"{ready_count}/{total}" if total else "?/?"
        display = reason if reason else phase
        pods.append({
            "name": name,
            "phase": phase,
            "ready": ready_str,
            "status": display,
            "reason": reason,
            "message": message,
            "line": f"{name}\t{ready_str}\t{display}",
        })
    pods.sort(key=_pod_severity)
    return pods


def _staging_is_healthy(ctx: dict) -> bool:
    replicas = ctx.get("replicas", -1)
    ready = ctx.get("ready_replicas", 0)
    if replicas <= 0 or ready < replicas:
        return False

    pods = ctx.get("pods") or []
    if pods:
        for pod in pods:
            if pod.get("ready") != "1/1":
                return False
            blob = f"{pod.get('reason', '')} {pod.get('status', '')}".lower()
            if any(bad in blob for bad in _POD_BAD_STATES):
                return False
            if (pod.get("phase") or "").lower() not in ("running", "succeeded"):
                return False
        return True

    pod_line = (ctx.get("pod_line") or "").lower()
    if "running" not in pod_line or "1/1" not in pod_line:
        return False
    reason = (ctx.get("pod_reason") or "").lower()
    return reason not in _POD_BAD_STATES


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

    pods = _fetch_pods_structured()
    ctx["pods"] = pods
    if pods:
        primary = pods[0]
        ctx["pod_name"] = primary["name"]
        ctx["pod_phase"] = primary["phase"]
        ctx["pod_reason"] = primary["reason"] or (
            primary["status"] if primary["status"] not in ("Running", "Succeeded") else ""
        )
        ctx["pod_message"] = primary.get("message", "")
        if len(pods) > 1:
            ctx["pod_line"] = "\n".join(p["line"] for p in pods)
    elif code == 0 and pod_line.strip():
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


def _incident_context_for_app(app: dict) -> dict:
    """Cluster facts for a specific demo app workload."""
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
        "app_id": app.get("id", ""),
        "app_label": app.get("label", ""),
        "deployment": app.get("deployment", ""),
        "bad_image": app.get("bad_image", ""),
    }
    dep = app["deployment"]
    pod_label = app["pod_label"]
    code, out = _kubectl(
        "get", "deployment", dep, "-n", cfg.NAMESPACE,
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
        "get", "pods", "-n", cfg.NAMESPACE, "-l", pod_label, "--no-headers",
    )
    ctx["pod_line"] = _kubectl_value(code, pod_line, "no pods")
    if code == 0 and pod_line.strip():
        ctx["pod_name"] = pod_line.strip().split()[0]
        parts = pod_line.strip().split()
        if len(parts) >= 4:
            ctx["pod_reason"] = parts[3]
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


def _extract_http_status(text: str) -> int | None:
    for pat in (
        r"HTTPError[:\s]+(\d{3})",
        r"status[:\s]+(\d{3})",
        r"\b(4\d{2}|5\d{2})\b",
    ):
        for m in re.finditer(pat, text, re.I):
            code = int(m.group(1))
            if 400 <= code < 600:
                return code
    return None


_GEMINI_HTTP_LABELS: dict[int, tuple[str, str]] = {
    401: ("invalid_api_key", "invalid or expired API key"),
    403: ("invalid_api_key", "invalid or expired API key"),
    429: ("quota_exceeded", "quota/rate limit exceeded"),
    400: ("bad_request", "bad request — check model name/payload shape"),
    404: ("model_not_found", "model not found — check HOLMES_MODEL"),
}


def _classify_gemini_failure(exit_code: int, raw: str, model: str) -> dict:
    text = _strip_ansi(raw or "")
    low = text.lower()
    http = _extract_http_status(text)

    if "timed out" in low or exit_code in (124, 137, -9):
        label, user = "timeout", "unreachable — check egress/network config or increase timeout"
    elif http and http in _GEMINI_HTTP_LABELS:
        label, user = _GEMINI_HTTP_LABELS[http]
    elif http == 404 or ("404" in text and "not found" in low):
        label, user = "model_not_found", "model not found — check HOLMES_MODEL"
    elif any(
        x in low
        for x in (
            "connection refused",
            "name or service not known",
            "network is unreachable",
            "failed to establish",
            "nodename nor servname",
            "temporary failure in name resolution",
        )
    ):
        label, user = "unreachable", "unreachable — check egress/network config"
    elif any(x in low for x in ("api key", "apikey", "unauthorized", "permission denied", "invalid key")):
        label, user = "invalid_api_key", "invalid or expired API key"
    elif "429" in text or "rate limit" in low or "quota" in low or "resource exhausted" in low:
        label, user = "quota_exceeded", "quota/rate limit exceeded"
    else:
        label, user = "unavailable", "unavailable"

    return {
        "label": label,
        "user_message": user,
        "http_status": http,
        "exit_code": exit_code,
        "model": model,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "detail_snippet": _holmes_detail_snippet(text, 300),
    }


def _log_gemini_failure(info: dict, raw: str) -> None:
    log.error(
        "Gemini/Holmes failure model=%s label=%s http=%s exit=%s detail=%s",
        info.get("model"),
        info.get("label"),
        info.get("http_status"),
        info.get("exit_code"),
        info.get("detail_snippet"),
    )
    if raw:
        log.error("Gemini/Holmes failure body (last 4k): %s", raw[-4000:])


def gemini_health() -> dict:
    """Lightweight Gemini API probe — no cluster calls."""
    model_full = cfg.resolved_holmes_model()
    model_id = model_full.replace("gemini/", "") if model_full.startswith("gemini/") else model_full
    base = {
        "ok": False,
        "model": model_full,
        "key_configured": _gemini_key_configured(),
        "holmes_enabled": cfg.HOLMES_ENABLED,
        "state": "unavailable",
        "label": "unavailable",
        "http_status": None,
    }
    if not _gemini_key_configured():
        return {
            **base,
            "state": "invalid_api_key",
            "label": "invalid or expired API key",
        }
    if not model_full.startswith("gemini/"):
        return {**base, "state": "skipped", "label": "non-Gemini model configured", "ok": True}

    key = os.environ.get("GEMINI_API_KEY", "").strip()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}?key={key}"
    try:
        with urllib.request.urlopen(url, timeout=12) as resp:
            if resp.status == 200:
                return {**base, "ok": True, "state": "ok", "label": "available"}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        info = _classify_gemini_failure(e.code, f"HTTP {e.code}: {body}", model_full)
        return {
            **base,
            "state": info["label"],
            "label": info["user_message"],
            "http_status": e.code,
        }
    except Exception as e:
        info = _classify_gemini_failure(0, str(e), model_full)
        return {**base, "state": info["label"], "label": info["user_message"]}
    return base


def holmes_cli_health() -> dict:
    """Probe Holmes CLI install + Gemini key — does not run a full agent ask."""
    gemini = gemini_health()
    cli_path = shutil.which(cfg.HOLMES_BIN) if cfg.HOLMES_ENABLED else None
    cli_installed = bool(cli_path)
    gemini_ok = bool(gemini.get("ok"))
    ok = bool(cfg.HOLMES_ENABLED and cli_installed and gemini_ok)
    if not cfg.HOLMES_ENABLED:
        label = "Holmes disabled — set HOLMES_ENABLED=true"
    elif not cli_installed:
        label = "Holmes CLI missing — holmesgpt not in container PATH"
    elif not gemini_ok:
        label = f"Gemini API issue — {gemini.get('label', 'unavailable')}"
    else:
        label = "cli_ready"
    note = ""
    if gemini_ok and cli_installed:
        note = (
            "Gemini API is healthy. If chat shows 'Holmes agent fallback', the CLI agent "
            "timed out or toolsets failed — direct Gemini answers still work."
        )
    return {
        "ok": ok,
        "label": label,
        "holmes_enabled": cfg.HOLMES_ENABLED,
        "holmes_mode": cfg.HOLMES_MODE,
        "model": cfg.resolved_holmes_model(),
        "cli_installed": cli_installed,
        "cli_path": cli_path or "",
        "timeout_seconds": cfg.HOLMES_TIMEOUT,
        "max_steps": cfg.HOLMES_MAX_STEPS,
        "gemini_api": gemini,
        "note": note,
    }


def _direct_gemini_chat(
    message: str,
    ctx: dict,
    tree: dict,
    lang: str,
    history: list[dict] | None = None,
) -> tuple[bool, str]:
    """Lightweight Gemini REST call when Holmes CLI fails but the API is healthy."""
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not _gemini_key_configured():
        return False, ""
    model_full = cfg.resolved_holmes_model()
    if not model_full.startswith("gemini/"):
        return False, ""
    model_id = model_full.replace("gemini/", "", 1)
    facts = _holmes_cluster_facts(ctx, tree)
    lang_note = {
        "fr": "Reply entirely in French.",
        "hi": "Reply entirely in Hindi (Devanagari).",
        "es": "Reply entirely in Spanish.",
    }.get(lang, "Reply in clear plain English.")
    history_block = _format_history_block(history)
    prompt = (
        f"{history_block}"
        f"LIVE CLUSTER FACTS (authoritative):\n{facts}\n\n"
        f"User question: {message}\n\n"
        f"{lang_note} Answer only what the user asked. Use markdown when helpful."
    )
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model_id}:generateContent?key={key}"
    )
    payload = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
    }).encode("utf-8")
    try:
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        candidates = data.get("candidates") or []
        if not candidates:
            return False, ""
        parts = (candidates[0].get("content") or {}).get("parts") or []
        text = "\n".join(str(p.get("text", "")) for p in parts).strip()
        return (bool(text), text[:4000])
    except Exception as exc:
        log.warning("Direct Gemini chat failed: %s", exc)
        return False, ""


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
    if "indexerror" in low and "choices" in low:
        return (
            "HolmesGPT agent crashed after tool calls — known Gemini/LiteLLM bug "
            "(empty choices in streaming response). Chat uses direct Gemini instead; "
            "rebuild image with litellm>=1.68 or set HOLMES_CHAT_DIRECT=true."
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


def _is_holmes_noise_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    low = s.lower()
    if any(
        n in low
        for n in (
            "toolset",
            "using selected model",
            "reference using",
            "environment variable",
            "was not set",
            "prerequisite",
            "holmesgpt complete",
            "user asks how to fix",
            "give numbered kubectl",
            "do not tell them to use guided demo",
            "answer only what",
            "live cluster facts",
            "recent chat",
            "user question:",
            "specific pod, image, and error from facts",
            "if follow-up",
            "if they ask what broke",
            "metrics api not available",
            "metrics api",
            "failed with exit code",
            "unknown command",
            "/bin/sh:",
            "not found",
            "exit code 1",
            "exit code 127",
            "running command",
            "warnings.warn",
            "traceback",
        )
    ):
        return True
    if re.match(r"^[✅❌✓✗]\s*", s) or s in ("✅", "❌"):
        return True
    if re.match(r"^Toolset\s+", s, re.I):
        return True
    if re.match(r"^gemini/", s, re.I):
        return True
    if re.match(r"^(error|warning|failed):", s, re.I):
        return True
    if re.match(r"^\S+:\s+not found", s, re.I):
        return True
    if re.match(r"^/.*:\s+\d+:\s+\S+: not found", s, re.I):
        return True
    return False


def _sanitize_holmes_reply(text: str) -> str:
    """Strip leaked system prompt and Holmes CLI toolset noise from output."""
    if not text:
        return text
    leak_markers = (
        "Answer ONLY what the user asked",
        "LIVE CLUSTER FACTS",
        "RECENT CHAT",
        "Do not invent pod names",
        "Reply entirely in",
        "User wants a MANUAL",
        "Use markdown sections",
        "authoritative — never contradict",
        "User asks how to fix",
        "Give numbered kubectl",
    )
    lines: list[str] = []
    for line in text.splitlines():
        if any(m in line for m in leak_markers):
            continue
        if _is_holmes_noise_line(line):
            continue
        lines.append(line)
    cleaned = "\n".join(lines).strip()
    cleaned = re.sub(r"^(AI|Assistant|Holmes):\s*", "", cleaned, flags=re.I).strip()
    return cleaned


def _extract_holmes_answer(text: str) -> str:
    """Pick the user-facing answer from raw Holmes CLI stdout."""
    text = _strip_ansi(text or "")
    if not text.strip():
        return ""

    for marker in (r"(?i)\bassistant:\s*", r"(?i)\bfinal answer:\s*"):
        parts = re.split(marker, text)
        if len(parts) > 1:
            text = parts[-1].strip()
            break

    # Walk up from the last line — keep trailing prose, stop at CLI/tool noise.
    lines = text.splitlines()
    tail: list[str] = []
    for ln in reversed(lines):
        s = ln.strip()
        if not s:
            if tail:
                break
            continue
        if _is_holmes_noise_line(ln):
            if tail:
                break
            continue
        tail.insert(0, ln)
    if tail:
        answer = "\n".join(tail).strip()
        answer = _sanitize_holmes_reply(answer)
        if len(answer) >= 8:
            return answer[:4000]

    cleaned = _sanitize_holmes_reply(text)
    blocks = [b.strip() for b in re.split(r"\n\s*\n", cleaned) if b.strip()]
    for block in reversed(blocks):
        prose_lines = [ln for ln in block.splitlines() if ln.strip() and not _is_holmes_noise_line(ln)]
        prose = "\n".join(prose_lines).strip()
        if len(prose) >= 8:
            return prose[:4000]

    return cleaned[:4000] if cleaned else text.strip()[:4000]


def _run_holmes_cli_prompt(prompt: str, max_steps: int | None = None) -> tuple[bool, str, str]:
    global _last_gemini_failure
    if shutil.which(cfg.HOLMES_BIN) is None:
        _last_gemini_failure = {
            "label": "cli_missing",
            "user_message": "HolmesGPT CLI not installed",
            "model": cfg.resolved_holmes_model(),
            "http_status": None,
            "exit_code": 127,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "detail_snippet": "holmes binary not found",
        }
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
        hint = _holmes_failure_hint(code, text)
        if model.startswith("gemini/"):
            _last_gemini_failure = _classify_gemini_failure(code, text or hint, model)
            _log_gemini_failure(_last_gemini_failure, text or hint)
        else:
            _last_gemini_failure = {
                "label": "unavailable",
                "user_message": "unavailable",
                "model": model,
                "http_status": _extract_http_status(text or hint),
                "exit_code": code,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "detail_snippet": _holmes_detail_snippet(text or hint, 300),
            }
        return False, "", hint
    _last_gemini_failure = None
    summary = _extract_holmes_answer(text)
    if not summary.strip():
        return False, "", _holmes_failure_hint(code, text)
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
            return "Holmes model not found — set HOLMES_MODEL=gemini/gemini-2.5-flash"
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


    return key.startswith(("AIza", "AQ."))


# --- Chat-first actions (natural language deploy / outage / heal) ---

def _app_pod_summary(app: dict) -> str:
    code, pods = _kubectl(
        "get", "pods", "-n", cfg.NAMESPACE, "-l", app["pod_label"], "--no-headers",
    )
    return _kubectl_value(code, pods, "not deployed")


def _app_workloads_exist(app: dict) -> bool:
    code, _ = _kubectl("get", "deployment", app["deployment"], "-n", cfg.NAMESPACE)
    return code == 0


def _app_is_healthy(app: dict) -> bool:
    if not _app_workloads_exist(app):
        return False
    code, pods = _kubectl(
        "get", "pods", "-n", cfg.NAMESPACE, "-l", app["pod_label"], "--no-headers",
    )
    line = _kubectl_value(code, pods, "")
    if not line or line in ("no pods", "not deployed"):
        return False
    if "Running" not in line or "1/1" not in line:
        return False
    url = app.get("health_url") or ""
    return _reachable(url) if url else True


def _argocd_status_for_app(app: dict) -> str:
    app_name = app.get("argocd_app", "")
    if not app.get("gitops") or not app_name:
        return ""
    if not _argocd_app_exists_named(app_name):
        return "Not registered"
    return _argocd_sync_status_named(app_name)


def _app_browser_links(app_id: str) -> dict[str, str]:
    return resolved_public_app_links().get(app_id, {})


def _app_links_markdown(app_id: str) -> str:
    links = _app_browser_links(app_id)
    items = []
    if links.get("dashboard"):
        items.append(f"[Open app]({links['dashboard']})")
    if links.get("argocd_app"):
        items.append(f"[Open Argo CD]({links['argocd_app']})")
    if links.get("health"):
        items.append(f"[Health URL]({links['health']})")
    return " · ".join(items)


def _deploy_nginx_app() -> dict:
    app = cfg.demo_app("nginx")
    manifest = app.get("argocd_manifest")
    if not manifest:
        raise RuntimeError("Nginx Argo CD manifest is not configured")
    code, out = _register_argocd_app_manifest(manifest)
    if code != 0:
        raise RuntimeError(f"Failed to register Nginx in Argo CD: {out}")
    _wait_argocd_app_status_named(app["argocd_app"], timeout=90, want_health="")
    _argocd_set_automated_named(app["argocd_app"], True)
    _argocd_refresh_named(app["argocd_app"])
    sync_code, sync_out = _argocd_trigger_sync_named(app["argocd_app"])
    if sync_code != 0:
        raise RuntimeError(f"Failed to sync Nginx Argo CD app: {sync_out}")
    _argocd_wait_synced_named(app["argocd_app"], timeout=90)
    deadline = time.time() + 90
    while time.time() < deadline:
        if _app_is_healthy(app):
            break
        time.sleep(3)
    healthy = _app_is_healthy(app)
    links_md = _app_links_markdown("nginx")
    links = _app_browser_links("nginx")
    return {
        "app": "nginx",
        "message": (
            f"**{app['label']}** is live in `{cfg.NAMESPACE}` — pod `{_app_pod_summary(app)}`."
            if healthy
            else f"**{app['label']}** deployed but not healthy yet — `{_app_pod_summary(app)}`."
        ) + (f"\n\n{links_md}" if links_md else ""),
        "healthy": healthy,
        "app_reachable": healthy,
        "pod_line": _app_pod_summary(app),
        "links": links,
        "open_url": links.get("dashboard"),
        "staging_url": links.get("dashboard"),
    }


def deploy_demo_app(app_id: str, on_step: StepCallback = None) -> dict:
    if app_id == "fastapi":
        return deploy_application(on_step=on_step)
    if app_id == "nginx":
        return _with_step_stream(on_step, _deploy_nginx_app)
    raise ValueError(f"Unknown app {app_id}")


def _reset_nginx_app() -> dict:
    app = cfg.demo_app("nginx")
    _unregister_argocd_app_named(app["argocd_app"])
    _kubectl(
        "delete", "deployment", app["deployment"],
        "-n", cfg.NAMESPACE, "--wait=false", "--ignore-not-found",
    )
    _kubectl(
        "delete", "service", app["deployment"],
        "-n", cfg.NAMESPACE, "--wait=false", "--ignore-not-found",
    )
    _kubectl(
        "delete", "ingress", app["deployment"],
        "-n", cfg.NAMESPACE, "--wait=false", "--ignore-not-found",
    )
    _kubectl(
        "delete", "configmap", f"{app['deployment']}-site",
        "-n", cfg.NAMESPACE, "--wait=false", "--ignore-not-found",
    )
    _kubectl(
        "delete", "pods", "-n", cfg.NAMESPACE, "-l", app["pod_label"],
        "--wait=false", "--ignore-not-found",
    )
    gone = not _app_workloads_exist(app)
    argo_gone = not _argocd_app_exists_named(app["argocd_app"])
    clean = gone and argo_gone
    return {
        "app": "nginx",
        "message": (
            f"**{app['label']}** removed from `{cfg.NAMESPACE}`."
            if clean
            else f"**{app['label']}** teardown started — verify pods are gone."
        ),
        "removed": gone,
        "app_clean": clean,
        "staging_clean": clean,
        "app_deployed": False,
        "argocd_app_exists": not argo_gone,
        "workloads_exist": not gone,
    }


def reset_demo_app(app_id: str, on_step: StepCallback = None) -> dict:
    if app_id == "fastapi":
        return reset_staging(on_step=on_step)
    if app_id == "nginx":
        return _with_step_stream(on_step, _reset_nginx_app)
    raise ValueError(f"Unknown app {app_id}")


def _simulate_app_outage_impl(app_id: str) -> dict:
    if app_id == "fastapi":
        return _simulate_outage_impl()
    app = cfg.demo_app(app_id)
    _kubectl_must(
        "scale", f"deployment/{app['deployment']}",
        "-n", cfg.NAMESPACE, "--replicas=1",
        action="Ensure nginx deployment exists",
    )
    _kubectl_must(
        "set", "image",
        f"deployment/{app['deployment']}",
        f"{app['container']}={app['bad_image']}",
        "-n", cfg.NAMESPACE,
        action="Inject bad nginx image",
    )
    deadline = time.time() + 30
    while time.time() < deadline:
        if not _app_is_healthy(app):
            break
        time.sleep(2)
    return {
        "app": app_id,
        "message": (
            f"**Outage simulated on {app['label']}** — pod `{_app_pod_summary(app)}`. "
            "Say **auto-fix** or **fix nginx** to restore."
        ) + (f"\n\n{_app_links_markdown(app_id)}" if _app_links_markdown(app_id) else ""),
        "pod_line": _app_pod_summary(app),
        "healthy": _app_is_healthy(app),
        "links": _app_browser_links(app_id),
    }


def simulate_app_outage(app_id: str, on_step: StepCallback = None) -> dict:
    return _with_step_stream(on_step, lambda: _simulate_app_outage_impl(app_id))


def _auto_fix_app_impl(app_id: str) -> dict:
    if app_id == "fastapi":
        return _auto_fix_impl()
    app = cfg.demo_app(app_id)
    _kubectl_must(
        "scale", f"deployment/{app['deployment']}",
        "-n", cfg.NAMESPACE, "--replicas=1",
        action="Scale nginx up",
    )
    _kubectl_must(
        "set", "image",
        f"deployment/{app['deployment']}",
        f"{app['container']}={app['good_image']}",
        "-n", cfg.NAMESPACE,
        action="Restore good nginx image",
    )
    _kubectl(
        "patch", f"deployment/{app['deployment']}", "-n", cfg.NAMESPACE,
        "--type=json",
        "-p", '[{"op":"replace","path":"/spec/template/spec/containers/0/imagePullPolicy","value":"Always"}]',
    )
    _kubectl(
        "delete", "pods", "-n", cfg.NAMESPACE, "-l", app["pod_label"], "--wait=false", "--force", "--grace-period=0",
    )
    deadline = time.time() + 90
    while time.time() < deadline:
        if _app_is_healthy(app):
            break
        time.sleep(3)
    healthy = _app_is_healthy(app)
    return {
        "app": app_id,
        "message": (
            f"**{app['label']} recovered** — `{_app_pod_summary(app)}`, health check "
            f"{'passing' if healthy else 'still failing'}."
        ) + (f"\n\n{_app_links_markdown(app_id)}" if _app_links_markdown(app_id) else ""),
        "healthy": healthy,
        "app_reachable": healthy,
        "pod_line": _app_pod_summary(app),
        "links": _app_browser_links(app_id),
        "open_url": _app_browser_links(app_id).get("dashboard"),
        "staging_url": _app_browser_links(app_id).get("dashboard"),
    }


def auto_fix_app(app_id: str, on_step: StepCallback = None) -> dict:
    return _with_step_stream(on_step, lambda: _auto_fix_app_impl(app_id))


def _resolve_app_target(message: str) -> str | None:
    q = _normalize_query(message)
    if re.search(r"\b(both|all apps|all applications|everything|each app)\b", q):
        return "all"
    if re.search(r"\b(nginx|web front|frontend|web app)\b", q):
        return "nginx"
    if re.search(r"\b(fastapi|fast api|python api|api app)\b", q):
        return "fastapi"
    return None


def _is_capabilities_question(message: str) -> bool:
    q = _normalize_query(message)
    return bool(re.search(
        r"(what can you do|what do you do|your capabilities|list capabilities|help me|how can you help)",
        q,
    ))


def _capabilities_reply() -> str:
    return (
        "I'm your **Kubernetes chat agent** connected to a live staging cluster. "
        "Here's what I can do for you:\n\n"
        "1. **Deploy applications** — `deploy fastapi`, `deploy nginx`, or `deploy both apps` "
        "(imagine you have 5–10 apps; chat deploys them rapidly).\n"
        "2. **Show status** — ask `show status` or `show cluster snapshot` when you want "
        "pod health, GitOps sync, and per-app state (I won't clutter the UI until you ask).\n"
        "3. **Detect problems** — `what broke?`, `explain in simple language`, or `explain with AI`.\n"
        "4. **Fix problems** — `auto-fix` restores the good image and verifies recovery.\n"
        "5. **Demo incidents** — `simulate outage` or `break fastapi` for a safe client demo.\n"
        "6. **Reset** — `reset fastapi` or `remove nginx` to tear down workloads.\n\n"
        "**Demo apps:** FastAPI (GitOps / Argo CD) and Nginx (second workload in the same namespace).\n\n"
        "Want click-by-click steps instead? Use the **guided demo** link at the bottom of this page."
    )


def _apps_status_data() -> list[dict]:
    """Structured status for rich chat cards — only shown when user asks."""
    rows = []
    for app_id, app in cfg.demo_apps().items():
        exists = _app_workloads_exist(app)
        healthy = _app_is_healthy(app) if exists else False
        pod = _app_pod_summary(app) if exists else "not deployed"
        if not exists:
            state, state_key = "Not deployed", "idle"
        elif healthy:
            state, state_key = "Healthy", "ok"
        else:
            state, state_key = "Unhealthy", "bad"
        gitops = _argocd_status_for_app(app)
        links = _app_browser_links(app_id)
        rows.append({
            "id": app_id,
            "label": app["label"],
            "blurb": app["blurb"],
            "state": state,
            "state_key": state_key,
            "pod_line": pod,
            "deployed": exists,
            "healthy": healthy,
            "gitops": gitops,
            "links": links,
        })
    return rows


def _format_apps_status_reply() -> str:
    rows = _apps_status_data()
    lines = [
        f"**Cluster snapshot** for namespace `{cfg.NAMESPACE}` — live from Kubernetes:\n"
    ]
    for r in rows:
        extra = f" · GitOps `{r['gitops']}`" if r.get("gitops") else ""
        lines.append(f"- **{r['label']}** — {r['state']} · `{r['pod_line']}`{extra}")
    lines.append(
        "\nNext: **deploy** an app, **simulate outage** for a demo, or **auto-fix** to recover."
    )
    return "\n".join(lines)


def _needs_status_disambiguation(message: str) -> bool:
    q = _normalize_query(message)
    if _resolve_app_target(message):
        return False
    return bool(re.search(r"\b(pod status|show status|status|show pods|pod details|apps status|cluster status)\b", q))


def _status_disambiguation_reply() -> tuple[str, list[dict[str, str]]]:
    return (
        "I can make that interactive. Choose the app and depth you want:",
        [
            {"label": "FastAPI pod status", "prompt": "Show pod status for fastapi"},
            {"label": "Nginx pod status", "prompt": "Show pod status for nginx"},
            {"label": "Both apps status", "prompt": "Show cluster status for both apps"},
            {"label": "Pods + GitOps", "prompt": "Show pods and GitOps status for both apps"},
            {"label": "Open FastAPI app", "prompt": "Open links for fastapi"},
            {"label": "Open Nginx app", "prompt": "Open links for nginx"},
        ],
    )


def _classify_chat_action(message: str) -> tuple[str, str | None]:
    """Return (action, app_target). action=chat means no cluster mutation."""
    q = _normalize_query(message)
    if _is_capabilities_question(message):
        return "capabilities", None
    if re.search(r"\b(pod status|show pods|pod details|show pod|app status)\b", q) and _resolve_app_target(message):
        return "app_status", _resolve_app_target(message)
    if re.search(r"\b(open|show|give)\b", q) and re.search(r"\b(link|url|dashboard|app)\b", q):
        return "links", _resolve_app_target(message) or "all"
    if re.search(r"\b(show status|cluster status|apps status|all status|cluster snapshot|show snapshot)\b", q):
        return "status", "all"
    if re.search(r"\b(deploy|bring up|launch|install|register|create)\b", q):
        target = _resolve_app_target(message)
        if re.search(r"\b(both|all apps|all applications|everything)\b", q):
            return "deploy", "all"
        return "deploy", target or "fastapi"
    if re.search(r"\b(reset|tear down|remove|delete|destroy|uninstall)\b", q):
        target = _resolve_app_target(message)
        if re.search(r"\b(both|all|everything)\b", q):
            return "reset", "all"
        return "reset", target or "fastapi"
    if re.search(r"\b(simulate outage|simulate an outage|break the app|break app|inject|cause outage)\b", q):
        return "outage", _resolve_app_target(message) or "fastapi"
    if re.search(r"\bbreak\b", q) and re.search(r"\b(fastapi|nginx|app)\b", q):
        return "outage", _resolve_app_target(message) or "fastapi"
    if re.search(r"\b(auto-?fix|heal|restore|recover)\b", q):
        return "heal", _resolve_app_target(message) or "fastapi"
    if re.search(r"\bfix (it|this|the app|nginx|fastapi)\b", q):
        return "heal", _resolve_app_target(message) or "fastapi"
    if re.search(r"\b(explain with ai|full diagnosis|run diagnosis|ai diagnosis)\b", q):
        return "explain", _resolve_app_target(message) or "fastapi"
    return "chat", None


def _execute_chat_action(
    action: str,
    target: str | None,
    on_step: StepCallback,
) -> dict:
    """Run a mutating demo action from chat; returns action metadata + message."""
    target = target or "fastapi"

    def step(title: str, detail: str = "", phase: str = "cluster") -> None:
        if on_step:
            on_step({"title": title, "detail": detail, "phase": phase})

    if action == "capabilities":
        return {"action": action, "message": _capabilities_reply(), "ui": "capabilities"}

    if action == "links":
        app_links = resolved_public_app_links()
        if target == "all":
            parts = []
            for app_id, app in cfg.demo_apps().items():
                md = _app_links_markdown(app_id)
                if md:
                    parts.append(f"- **{app['label']}** — {md}")
            return {
                "action": action,
                "target": "all",
                "message": "**Live app links**\n\n" + ("\n".join(parts) if parts else "Links are not available yet."),
                "links": app_links,
            }
        app = cfg.demo_app(target)
        md = _app_links_markdown(target)
        return {
            "action": action,
            "target": target,
            "message": f"**{app['label']} links**\n\n{md or 'Links are not available yet.'}",
            "links": {target: app_links.get(target, {})},
        }

    if action == "app_status":
        if target == "all":
            rows = _apps_status_data()
            return {
                "action": action,
                "target": "all",
                "message": _format_apps_status_reply(),
                "apps_status": rows,
                "ui": "status_cards",
                "links": resolved_public_app_links(),
            }
        app = cfg.demo_app(target)
        row = next((r for r in _apps_status_data() if r["id"] == target), None)
        msg = (
            f"**{app['label']}** — {row['state']}\n\n"
            f"- Pod: `{row['pod_line']}`\n"
            f"- GitOps: `{row['gitops'] or 'not available'}`\n\n"
            f"{_app_links_markdown(target)}"
        ) if row else f"No status found for {app['label']}."
        return {
            "action": action,
            "target": target,
            "message": msg,
            "apps_status": [row] if row else [],
            "ui": "status_cards",
            "links": {target: _app_browser_links(target)},
        }

    if action == "status":
        rows = _apps_status_data()
        return {
            "action": action,
            "message": _format_apps_status_reply(),
            "apps_status": rows,
            "ui": "status_cards",
            "links": resolved_public_app_links(),
        }

    if action == "deploy":
        step("Deploy requested", target, "git")
        if target == "all":
            r1 = deploy_demo_app("fastapi", on_step=on_step)
            r2 = deploy_demo_app("nginx", on_step=on_step)
            msg = (
                "**Both demo apps deployed.**\n\n"
                f"1. FastAPI — {r1.get('message', 'done')}\n"
                f"2. Nginx — {r2.get('message', 'done')}"
            )
            return {"action": action, "target": "all", "message": msg, "results": [r1, r2], "links": resolved_public_app_links()}
        r = deploy_demo_app(target, on_step=on_step)
        app = cfg.demo_app(target)
        msg = r.get("message") or f"**{app['label']}** deploy finished."
        if target == "fastapi" and r.get("app_reachable"):
            msg += "\n\nStaging health checks are passing."
        links_md = _app_links_markdown(target)
        if links_md and links_md not in msg:
            msg += f"\n\n{links_md}"
        return {"action": action, "target": target, "message": msg, "result": r, "links": {target: _app_browser_links(target)}}

    if action == "reset":
        step("Reset requested", target, "argocd")
        if target == "all":
            r1 = reset_demo_app("fastapi", on_step=on_step)
            r2 = reset_demo_app("nginx", on_step=on_step)
            msg = f"**Reset complete.**\n\n- FastAPI: {r1.get('message')}\n- Nginx: {r2.get('message')}"
            return {"action": action, "target": "all", "message": msg, "links": resolved_public_app_links()}
        r = reset_demo_app(target, on_step=on_step)
        return {"action": action, "target": target, "message": r.get("message", "Reset complete."), "result": r, "links": {target: _app_browser_links(target)}}

    if action == "outage":
        step("Simulating outage", target, "break")
        r = simulate_app_outage(target, on_step=on_step)
        return {"action": action, "target": target, "message": r.get("message", "Outage active."), "result": r, "links": {target: _app_browser_links(target)}}

    if action == "heal":
        step("Auto-fix requested", target, "health")
        r = auto_fix_app(target, on_step=on_step)
        return {"action": action, "target": target, "message": r.get("message", "Recovery complete."), "result": r, "links": {target: _app_browser_links(target)}}

    if action == "explain":
        step("Running AI diagnosis", target, "ai")
        r = explain_with_ai(on_step=on_step)
        summary = r.get("summary") or r.get("message") or "Diagnosis complete."
        simple = r.get("simple_explanation") or ""
        body = f"**{summary}**"
        if simple:
            body += f"\n\n{simple}"
        body += "\n\nSay **auto-fix** to restore the app, or ask follow-up questions."
        return {"action": action, "target": target, "message": body, "result": r}

    raise ValueError(f"Unknown chat action {action}")


def holmes_snapshot() -> dict:
    """Live cluster facts for the Holmes chat sidebar (no LLM)."""
    ctx = _incident_context()
    tree = _argocd_app_tree(ctx)
    healthy = _staging_is_healthy(ctx)
    apps_list = []
    for app_id, app in cfg.demo_apps().items():
        exists = _app_workloads_exist(app)
        apps_list.append({
            "id": app_id,
            "label": app["label"],
            "blurb": app["blurb"],
            "deployed": exists,
            "healthy": _app_is_healthy(app) if exists else False,
            "pod_line": _app_pod_summary(app) if exists else "not deployed",
        })
    return {
        "ok": True,
        "healthy": healthy,
        "holmes_enabled": cfg.HOLMES_ENABLED,
        "chat_actions_enabled": cfg.CHAT_ACTIONS_ENABLED,
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
        "gemini_key_ok": _gemini_key_configured(),
        "apps": apps_list,
    }


def _holmes_cluster_facts(ctx: dict, tree: dict) -> str:
    pod_lines = []
    for p in ctx.get("pods") or []:
        pod_lines.append(
            f"  - {p['name']}: ready={p['ready']}, phase={p['phase']}, status={p['status']}"
            + (f", reason={p['reason']}" if p.get("reason") else "")
        )
    pods_block = "\n".join(pod_lines) if pod_lines else f"  - {ctx.get('pod_line') or 'none'}"
    return "\n".join([
        f"namespace: {cfg.NAMESPACE}",
        f"deployment: {cfg.DEPLOYMENT_NAME}",
        f"image: {ctx.get('image') or 'unknown'}",
        f"replicas_ready: {ctx.get('ready_replicas', 0)}/{ctx.get('replicas', -1)}",
        f"staging_healthy: {_staging_is_healthy(ctx)}",
        f"argocd_sync: {tree.get('sync_status', 'Unknown')}",
        f"argocd_health: {tree.get('health_status', 'Unknown')}",
        "pods:",
        pods_block,
    ])


def _telemetry_diagnosis_reply(ctx: dict, tree: dict, lang: str = "en") -> str:
    """Plain-English (or localized) diagnosis from live kubectl — no LLM."""
    summary, bullets, root, simple = _plain_language_explain(ctx)
    sync = tree.get("sync_status", "Unknown")
    argo_health = tree.get("health_status", "Unknown")
    healthy = _staging_is_healthy(ctx)

    if lang == "fr":
        verdict = "EN BONNE SANTÉ" if healthy else "PAS EN BONNE SANTÉ"
        lines = [
            f"**Verdict : {verdict}**",
            "",
            f"**Résumé :** {summary}",
            "",
            "**En termes simples :**",
            _translate_simple_paragraph(simple, lang),
            "",
            f"- **Cause :** {root}",
            f"- **Argo CD :** {sync} / {argo_health}",
            f"- **Image du déploiement :** `{ctx.get('image') or 'inconnue'}`",
        ]
        for b in bullets[:4]:
            lines.append(f"- {b}")
        lines.append("\n*Source : télémétrie cluster en direct (instantané).*")
        return "\n".join(ln for ln in lines if ln is not None)

    lines = [
        f"**Verdict: {'HEALTHY' if healthy else 'UNHEALTHY'}**",
        "",
        f"**{summary}**",
        "",
        simple,
        "",
        f"- **Root cause label:** {root}",
        f"- **Argo CD:** {sync} / {argo_health}",
        f"- **Deployment image:** `{ctx.get('image') or 'unknown'}`",
    ]
    for b in bullets[:4]:
        lines.append(f"- {b}")
    lines.append("\n*Source: live cluster telemetry (instant).*")
    return "\n".join(lines)


def _translate_simple_paragraph(text: str, lang: str) -> str:
    """Lightweight localization for client-facing paragraphs (no LLM)."""
    if lang != "fr":
        return text
    if "cannot pull the container image" in text or "does not exist in the registry" in text:
        return (
            "En termes simples : l'application pointe vers une image de conteneur qui n'existe pas dans le registre — "
            "comme une mauvaise adresse de livraison. Kubernetes réessaie en boucle mais le pod ne démarre jamais, "
            "donc l'application est hors service pour les utilisateurs. Le dépôt Git contient encore la bonne image ; "
            "seul le cluster live est incorrect."
        )
    if "healthy" in text.lower() and "running normally" in text.lower():
        return (
            "En termes simples : l'application fonctionne normalement. Kubernetes sert la bonne image, "
            "le pod est Ready et GitOps est synchronisé. Aucune action n'est nécessaire."
        )
    if "keeps crashing" in text.lower() or "crashloop" in text.lower():
        return (
            "En termes simples : le conteneur démarre puis plante immédiatement. Kubernetes redémarre en boucle "
            "sans jamais obtenir un pod stable — l'URL staging échoue jusqu'au retour arrière."
        )
    if "zero running pods" in text.lower() or "scaling" in text.lower():
        return (
            "En termes simples : toutes les copies de l'application ont été arrêtées volontairement — "
            "comme fermer tous les magasins. Aucun pod ne sert le trafic jusqu'à Auto-fix."
        )
    return (
        "En termes simples : quelque chose dans la charge staging n'est pas sain. "
        "Consultez l'état des pods ci-dessous, puis lancez Auto-fix sur la démo guidée si besoin."
    )


def _format_pod_details_reply(ctx: dict, tree: dict, lang: str = "en") -> str:
    """Structured pod table — always from live kubectl JSON."""
    healthy = _staging_is_healthy(ctx)
    sync = tree.get("sync_status", "Unknown")
    argo_health = tree.get("health_status", "Unknown")
    image = ctx.get("image") or "unknown"
    ready = ctx.get("ready_replicas", 0)
    replicas = ctx.get("replicas", -1)
    pods = ctx.get("pods") or []

    if lang == "fr":
        title = "**Détails des pods (live)**"
        verdict = "**Verdict : EN BONNE SANTÉ**" if healthy else "**Verdict : PAS EN BONNE SANTÉ**"
        ns_l, dep_l, img_l, rep_l = "Namespace", "Déploiement", "Image", "Réplicas prêts"
        pod_h, src = "Pods", "*Source : kubectl JSON — pas d'interprétation LLM.*"
    else:
        title = "**Pod details (live)**"
        verdict = "**Verdict: HEALTHY**" if healthy else "**Verdict: UNHEALTHY**"
        ns_l, dep_l, img_l, rep_l = "Namespace", "Deployment", "Image", "Replicas ready"
        pod_h, src = "Pods", "*Source: kubectl JSON — no LLM guesswork.*"

    lines = [
        title,
        "",
        verdict,
        "",
        f"- **{ns_l}:** `{cfg.NAMESPACE}`",
        f"- **{dep_l}:** `{cfg.DEPLOYMENT_NAME}`",
        f"- **{img_l}:** `{image}`",
        f"- **{rep_l}:** {ready}/{replicas}",
        f"- **Argo CD:** {sync} / {argo_health}",
        "",
        f"**{pod_h}:**",
    ]
    if not pods:
        lines.append(f"- {ctx.get('pod_line') or ('aucun pod' if lang == 'fr' else 'no pods')}")
    else:
        for i, p in enumerate(pods, 1):
            extra = ""
            if p.get("message"):
                extra = f" — {p['message'][:120]}"
            lines.append(f"{i}. `{p['name']}` — **{p['ready']}** {p['status']}{extra}")

    if not healthy and pods:
        worst = pods[0]
        if lang == "fr":
            lines.extend([
                "",
                f"**Pod bloquant :** `{worst['name']}` ({worst.get('reason') or worst['status']})",
                "Les pods plus anciens en Running peuvent rester visibles pendant un déploiement — "
                "le verdict suit **readyReplicas** et le pod le plus critique.",
            ])
        else:
            lines.extend([
                "",
                f"**Blocking pod:** `{worst['name']}` ({worst.get('reason') or worst['status']})",
                "Older Running pods may still appear during a rollout — "
                "verdict uses **readyReplicas** and the worst pod state.",
            ])
    lines.append(f"\n{src}")
    return "\n".join(lines)


def _format_health_verdict(ctx: dict, tree: dict, lang: str = "en") -> str:
    """One-line health answer — same logic every time."""
    healthy = _staging_is_healthy(ctx)
    sync = tree.get("sync_status", "Unknown")
    argo_health = tree.get("health_status", "Unknown")
    ready = ctx.get("ready_replicas", 0)
    replicas = ctx.get("replicas", -1)
    image = ctx.get("image") or "unknown"
    pods = ctx.get("pods") or []
    pod_summary = pods[0]["line"] if pods else (ctx.get("pod_line") or "no pods")

    if lang == "fr":
        if healthy:
            return (
                "**Oui — votre application staging est en bonne santé.**\n\n"
                f"- **Pod principal :** {pod_summary}\n"
                f"- **Image :** `{image}`\n"
                f"- **Réplicas prêts :** {ready}/{replicas}\n"
                f"- **Argo CD :** {sync} / {argo_health}\n\n"
                "*Basé sur kubectl live (readyReplicas + état de tous les pods).*"
            )
        reason = (pods[0].get("reason") if pods else None) or ctx.get("pod_reason") or "pas prêt"
        return (
            "**Non — la charge staging n'est pas en bonne santé.**\n\n"
            f"- **Pod principal :** {pod_summary}\n"
            f"- **Problème :** {reason}\n"
            f"- **Image :** `{image}`\n"
            f"- **Réplicas prêts :** {ready}/{replicas}\n"
            f"- **Argo CD :** {sync} / {argo_health}\n\n"
            "Demandez *expliquer en français* ou lancez **Auto-fix** sur la démo guidée."
        )

    if healthy:
        return (
            "**Yes — your staging app is healthy.**\n\n"
            f"- **Primary pod:** {pod_summary}\n"
            f"- **Image:** `{image}`\n"
            f"- **Replicas ready:** {ready}/{replicas}\n"
            f"- **Argo CD:** {sync} / {argo_health}\n\n"
            "*Based on live kubectl (readyReplicas + all pod states).*"
        )
    reason = (pods[0].get("reason") if pods else None) or ctx.get("pod_reason") or "not ready"
    return (
        "**No — the staging workload is not healthy.**\n\n"
        f"- **Primary pod:** {pod_summary}\n"
        f"- **Issue:** {reason}\n"
        f"- **Image:** `{image}`\n"
        f"- **Replicas ready:** {ready}/{replicas}\n"
        f"- **Argo CD:** {sync} / {argo_health}\n\n"
        "Ask *explain the issue* or run **Auto-fix** on the guided demo."
    )


def _wants_layman_explain(message: str) -> bool:
    q = _normalize_query(message)
    return any(
        p in q
        for p in (
            "layman", "laymen", "simple language", "plain english", "plain language",
            "easy words", "non technical", "non-technical", "explain the issue",
            "explain in simple", "simple terms", "eli5", "like i'm 5",
        )
    )


def _needs_conversational_answer(message: str) -> bool:
    """Questions that need LLM reasoning — not a static telemetry template."""
    if _wants_layman_explain(message):
        return True
    if _is_root_cause_question(message):
        return True
    q = _intent_query(message)
    return any(
        phrase in q
        for phrase in (
            "how to", "how do", "how can", "how should",
            "fix this", "correct this", "correct ", "fix it",
            "why ", "help me", "recommend", "steps to", "should i",
            "tell me a joke", "explain why",
            "investigate",
            "explain", "what problem", "what is the problem", "what's the problem",
            "inside my pod", "go inside", "detail review", "review my pod",
        )
    )


def _is_pod_details_telemetry(q: str) -> bool:
    """Strict pod-details allow-list — rejects mixed conversational phrasing."""
    if any(x in q for x in ("explain", "problem", "inside", "review", "how", "why", "broke", "layman", "wrong")):
        return False
    if re.search(r"\b(pod details?|list pods?|show pods?|my pod details)\b", q):
        return True
    return q in ("i need my pod details", "i need pod details", "need my pod details")


def _is_status_telemetry(q: str) -> bool:
    """Exact status queries only — 'pod status in hindi' must not match (lang stripped elsewhere)."""
    if any(x in q for x in ("explain", "problem", "how", "why", "hindi", "french", "spanish")):
        return False
    return bool(re.search(r"^(pod status|app status|show status|status)$", q)) or bool(
        re.search(r"^(is my app healthy|is the app healthy|app healthy)\??$", q)
    )


def _is_telemetry_only_intent(message: str) -> bool:
    """Strict allow-list: instant kubectl answers only (English, factual)."""
    if _requested_language(message) != "en":
        return False
    if _needs_conversational_answer(message):
        return False
    q = _telemetry_intent_query(message)
    if _is_greeting(message):
        return True
    if _is_pod_details_telemetry(q):
        return True
    if _is_status_telemetry(q):
        return True
    if "image" in q and any(w in q for w in ("what", "which", "tag", "using", "current")):
        return True
    if any(w in q for w in ("argo", "gitops")) and "sync" in q:
        return True
    if any(w in q for w in ("recovered", "outage", "still broken", "still down")):
        return True
    return False


def _wants_manual_fix(message: str) -> bool:
    q = _normalize_query(message)
    return any(
        p in q
        for p in (
            "manually", "manual", "by hand", "kubectl", "command line",
            "terminal", "shell", "myself", "without auto-fix", "without autofix",
            "not auto-fix", "not the demo", "without demo", "cli fix",
        )
    )


def _is_bad_image_outage(ctx: dict) -> bool:
    image = (ctx.get("image") or "").lower()
    reason = (ctx.get("pod_reason") or "").lower()
    return (
        "does-not-exist" in image
        or "errimagepull" in reason
        or "imagepullbackoff" in reason
    )


def _manual_fix_steps_reply(ctx: dict, tree: dict, lang: str = "en") -> str:
    """Exact kubectl / Argo steps — what Auto-fix runs under the hood."""
    ns = cfg.NAMESPACE
    dep = cfg.DEPLOYMENT_NAME
    ctr = cfg.CONTAINER_NAME
    good = cfg.GOOD_IMAGE
    bad = ctx.get("image") or "unknown"
    label = cfg.POD_LABEL
    argo_app = cfg.ARGOCD_APP
    argo_ns = cfg.ARGOCD_NAMESPACE

    k8s_block = (
        f"kubectl set image deployment/{dep} {ctr}={good} -n {ns}\n"
        f"kubectl scale deployment/{dep} -n {ns} --replicas=1\n"
        f"kubectl rollout status deployment/{dep} -n {ns}\n"
        f"kubectl get pods -n {ns} -l {label}"
    )

    if not _is_bad_image_outage(ctx):
        if lang == "hi":
            return "**मैन्युअल फिक्स** इस स्थिति पर निर्भर करता है — पहले पूछें *what broke?*"
        if lang == "fr":
            return "**Correction manuelle :** décrivez le symptôme ou demandez *what broke?*"
        return (
            "**Manual fix** depends on the failure mode. Ask *what broke?* first, "
            "then I can give exact `kubectl` commands."
        )

    if lang == "hi":
        return (
            f"**मैन्युअल समाधान (kubectl)**\n\n"
            f"**समस्या:** इमेज `{bad}` pull नहीं हो रही → `ImagePullBackOff`.\n\n"
            f"**चरण (Cloud Shell या जहाँ kubectl है):**\n"
            f"```bash\n{k8s_block}\n```\n\n"
            f"**Argo CD:** ऐप `{argo_app}` Git से sync करें, या live manifest फिर से बदल देगा:\n"
            f"```bash\nkubectl patch application {argo_app} -n {argo_ns} "
            f"--type merge -p '{{\"metadata\":{{\"annotations\":{{\"argocd.argoproj.io/refresh\":\"hard\"}}}}}}'\n"
            f"```\n\n"
            "*Guided demo → Auto-fix यही kubectl चलाता है — मैन्युअल के लिए ऊपर के commands कॉपी करें।*"
        )

    if lang == "fr":
        return (
            f"**Correction manuelle (kubectl)**\n\n"
            f"**Problème :** l'image `{bad}` est introuvable → `ImagePullBackOff`.\n\n"
            f"**Commandes (Cloud Shell) :**\n"
            f"```bash\n{k8s_block}\n```\n\n"
            f"**Argo CD :** si GitOps réécrit le manifest, resynchronisez `{argo_app}` ou utilisez Auto-fix.\n\n"
            "*La démo guidée → Auto-fix exécute ces mêmes commandes pour vous.*"
        )

    return (
        f"**Manual fix (kubectl)**\n\n"
        f"**What broke:** Image `{bad}` does not exist in OCIR → pod stuck in `ImagePullBackOff`.\n\n"
        f"**Run these in OCI Cloud Shell** (same commands Auto-fix runs):\n\n"
        f"```bash\n{k8s_block}\n```\n\n"
        f"**Why this works:** `kubectl set image` points the deployment at the known-good tag "
        f"`{good.split('/')[-1]}`. Scale + rollout status waits until the pod is `1/1 Running`.\n\n"
        f"**If Argo CD reverts you:** Git still has the good image — refresh/sync app `{argo_app}` "
        f"in namespace `{argo_ns}`, or use **Guided demo → Auto-fix** (does kubectl + Argo for you).\n\n"
        f"**Verify image on deployment:**\n"
        f"```bash\nkubectl get deployment {dep} -n {ns} "
        f"-o jsonpath='{{.spec.template.spec.containers[0].image}}'\n```"
    )


def _demo_fix_steps_reply(ctx: dict, tree: dict, lang: str = "en") -> str:
    """Guided-demo Auto-fix path (UI button) — not manual kubectl."""
    bad_image = _is_bad_image_outage(ctx)
    sync = tree.get("sync_status", "Unknown")

    if lang == "hi":
        if bad_image:
            return (
                "**समाधान (डेमो)**\n\n"
                "**समस्या:** कंटेनर इमेज रजिस्ट्री में नहीं मिल रही (`ImagePullBackOff`)।\n\n"
                "**कदम:**\n"
                "1. **Guided demo** खोलें → चरण 4 **Auto-fix** चलाएँ\n"
                "2. Auto-fix सही इमेज `demo-pass` पर वापस करता है और Argo CD सिंक करता है\n"
                "3. Pod `1/1 Running` होने तक प्रतीक्षा करें (~1–2 मिनट)\n\n"
                f"*वर्तमान इमेज:* `{ctx.get('image', '')}` · *Argo CD:* {sync}"
            )
        return (
            "**समाधान:** Guided demo → **Auto-fix** चलाएँ, फिर pod status दोबारा पूछें।"
        )

    if lang == "fr":
        if bad_image:
            return (
                "**Comment corriger (démo)**\n\n"
                "**Problème :** l'image conteneur est introuvable dans le registre (`ImagePullBackOff`).\n\n"
                "**Étapes :**\n"
                "1. Ouvrez la **démo guidée** → étape 4 **Auto-fix**\n"
                "2. Auto-fix restaure l'image `demo-pass` et synchronise Argo CD\n"
                "3. Attendez que le pod passe à `1/1 Running` (~1–2 min)\n\n"
                f"*Image actuelle :* `{ctx.get('image', '')}` · *Argo CD :* {sync}"
            )
        return "**Correction :** lancez **Auto-fix** sur la démo guidée."

    if bad_image:
        return (
            "**How to fix this (demo)**\n\n"
            "**What broke:** The deployment points at a container image that does not exist in OCIR "
            "(`ImagePullBackOff` / `ErrImagePull`). This is the intentional demo outage.\n\n"
            "**Steps:**\n"
            "1. Open **Guided demo** → Step 4 **Auto-fix**\n"
            "2. Auto-fix patches the deployment to the known-good image (`demo-pass`) and syncs Argo CD\n"
            "3. Wait until the pod shows `1/1 Running` (~1–2 minutes)\n"
            "4. Re-ask *pod status* to confirm recovery\n\n"
            f"*Current image:* `{ctx.get('image', '')}` · *Argo CD:* {sync}\n\n"
            "*Git still has the correct image — only the live cluster manifest is wrong.*"
        )
    return (
        "**How to fix:** Run **Auto-fix** on the guided demo, or describe the symptom "
        "and I can investigate further."
    )


def _degraded_banner(lang: str, err: dict | None = None, gemini_api_ok: bool = False) -> str:
    reason = (err or {}).get("user_message", "unavailable")
    if gemini_api_ok:
        reason = (err or {}).get("user_message") or "Holmes agent timed out (Gemini API OK)"
        if lang == "fr":
            return (
                f"\n\n---\n*⚠️ **Mode dégradé** — agent Holmes indisponible ({reason}). "
                "Réponse factuelle ci-dessous.*"
            )
        if lang == "hi":
            return (
                f"\n\n---\n*⚠️ **Degraded mode** — Holmes agent failed ({reason}). "
                "Factual fallback below.*"
            )
        return (
            f"\n\n---\n*⚠️ **Degraded mode** — Holmes agent failed ({reason}). "
            "Factual fallback below (Gemini API is OK).*"
        )
    if lang == "hi":
        return (
            f"\n\n---\n*⚠️ **Degraded mode** — Gemini unavailable ({reason}). "
            "Factual fallback below (not LLM-generated).*"
        )
    if lang == "fr":
        return (
            f"\n\n---\n*⚠️ **Mode dégradé** — Gemini indisponible ({reason}). "
            "Réponse factuelle ci-dessous.*"
        )
    return (
        f"\n\n---\n*⚠️ **Degraded mode** — Gemini unavailable ({reason}). "
        "Factual fallback below (not LLM-generated).*"
    )


def _fallback_capabilities(message: str, lang: str, ctx: dict | None = None) -> tuple[list[str], list[str]]:
    """What Gemini would do vs what static fallback can still provide."""
    cannot: list[str] = []
    can: list[str] = ["live cluster telemetry (pods, image, Argo CD sync)"]
    q = _intent_query(message)

    if lang != "en":
        lang_names = {"fr": "French", "hi": "Hindi", "es": "Spanish"}
        cannot.append(f"a fully translated reply in {lang_names.get(lang, lang)}")

    if _wants_manual_fix(message):
        if ctx and _is_bad_image_outage(ctx):
            can.append("standard kubectl remediation commands for this outage pattern")
        else:
            cannot.append("custom remediation commands without knowing the failure mode")
    elif _is_fix_question(message):
        cannot.append("custom step-by-step remediation tailored to your exact wording")
        can.append("Guided demo Auto-fix path and generic recovery notes")

    if _wants_layman_explain(message):
        cannot.append("a conversational layman explanation")
        can.append("a factual summary from kubectl")

    if _is_root_cause_question(message):
        cannot.append("a deep narrative root-cause analysis")
        can.append("factual RCA bullets from live telemetry")

    if "joke" in q or "story" in q or "poem" in q:
        cannot.append("creative or off-topic answers")

    return cannot, can


def _degraded_preamble(
    message: str, lang: str, err: dict | None, ctx: dict | None = None,
    gemini_api_ok: bool = False,
) -> str:
    cannot, can_do = _fallback_capabilities(message, lang, ctx)
    reason = (err or {}).get("user_message", "unavailable")
    if gemini_api_ok:
        reason = (err or {}).get("user_message") or (
            "Holmes agent timed out — Gemini API is reachable"
        )
        if lang == "fr":
            return f"**L'agent Holmes a échoué ({reason}).** Voici les faits live :\n\n"
        if lang == "hi":
            return f"**Holmes agent विफल ({reason}).** यहाँ live telemetry है:\n\n"
        return f"**Holmes agent could not finish ({reason}).** Here's live telemetry:\n\n"
    if not cannot:
        if lang == "fr":
            return f"**Gemini est indisponible ({reason}).** Voici les faits live :\n\n"
        if lang == "hi":
            return f"**Gemini उपलब्ध नहीं ({reason}).** यहाँ live telemetry है:\n\n"
        return f"**Gemini is currently unavailable ({reason}).** Here's live telemetry:\n\n"

    cannot_s = ", ".join(cannot)
    can_s = "; ".join(can_do)
    if lang == "fr":
        return (
            f"**Gemini est indisponible ({reason}).** "
            f"Je ne peux pas fournir : {cannot_s}. "
            f"Voici ce que je peux confirmer : {can_s}. "
            "Réessayez quand Gemini sera de retour.\n\n"
        )
    if lang == "hi":
        return (
            f"**Gemini उपलब्ध नहीं ({reason}).** "
            f"मैं अभी यह नहीं दे सकता: {cannot_s}। "
            f"यहाँ वह है जो telemetry से पुष्टि हो सकती है: {can_s}। "
            "Gemini वापस आने पर दोबारा पूछें।\n\n"
        )
    return (
        f"**Gemini is currently unavailable ({reason}).** "
        f"I can't generate {cannot_s} right now. "
        f"Here's what I can confirm from live telemetry: {can_s}. "
        "Once Gemini is back, ask again for the full answer.\n\n"
    )


def _demo_rca_fallback_reply(ctx: dict, tree: dict, lang: str = "en") -> str:
    """Multilingual RCA when Gemini fails — never the pod-details table."""
    summary, bullets, root, simple = _plain_language_explain(ctx)
    sync = tree.get("sync_status", "Unknown")
    argo_health = tree.get("health_status", "Unknown")
    worst = (ctx.get("pods") or [{}])[0]
    pod_name = worst.get("name") or ctx.get("pod_name") or "unknown"
    image = ctx.get("image") or "unknown"

    if lang == "hi":
        lines = [
            f"**स्थिति:** {summary}",
            "",
            _hindi_simple_explain(ctx, simple),
            "",
            f"- **मुख्य pod:** `{pod_name}`",
            f"- **कारण:** {root}",
            f"- **इमेज:** `{image}`",
            f"- **Argo CD:** {sync} / {argo_health}",
        ]
        for b in bullets[:3]:
            lines.append(f"- {b}")
        return "\n".join(lines)

    if lang == "fr":
        lines = [
            f"**Statut :** {summary}",
            "",
            _translate_simple_paragraph(simple, "fr"),
            "",
            f"- **Pod principal :** `{pod_name}`",
            f"- **Cause :** {root}",
            f"- **Image :** `{image}`",
            f"- **Argo CD :** {sync} / {argo_health}",
        ]
        for b in bullets[:3]:
            lines.append(f"- {b}")
        return "\n".join(lines)

    lines = [
        f"**Status:** {summary}",
        "",
        simple,
        "",
        f"- **Primary pod:** `{pod_name}`",
        f"- **Root cause:** {root}",
        f"- **Image:** `{image}`",
        f"- **Argo CD:** {sync} / {argo_health}",
    ]
    for b in bullets[:3]:
        lines.append(f"- {b}")
    return "\n".join(lines)


def _hindi_simple_explain(ctx: dict, simple: str) -> str:
    image = (ctx.get("image") or "").lower()
    reason = (ctx.get("pod_reason") or "").lower()
    if "does-not-exist" in image or "imagepull" in reason:
        return (
            "सरल शब्दों में: Kubernetes आपके ऐप की container image रजिस्ट्री से नहीं खींच पा रहा — "
            "जैसे गलत पते पर डिलीवरी। Pod शुरू नहीं होता, इसलिए staging ऐप डाउन दिखता है।"
        )
    if _staging_is_healthy(ctx):
        return "सरल शब्दों में: ऐप सामान्य रूप से चल रहा है। Pod Ready है और GitOps sync में है।"
    return (
        "सरल शब्दों में: staging workload अभी स्वस्थ नहीं है। नीचे तकनीकी विवरण देखें; "
        "Guided demo पर Auto-fix चलाएँ।"
    )


def _is_fix_question(message: str) -> bool:
    q = _normalize_query(message)
    return any(w in q for w in ("how", "fix", "correct", "remedy", "resolve", "repair"))


def _wants_history_recap(message: str, history: list[dict] | None) -> bool:
    if not history:
        return False
    q = _intent_query(message)
    return any(
        p in q
        for p in (
            "previous err", "prior err", "last err", "earlier err", "any err",
            "previous error", "last error", "any error", "got err", "what err",
            "recap", "what did you say", "what you said", "earlier you",
            "before you", "in our chat", "from chat", "i asked you",
        )
    )


def _history_recap_reply(
    history: list[dict], ctx: dict, tree: dict, lang: str = "en",
) -> str:
    user_bits: list[str] = []
    assistant_bits: list[str] = []
    for turn in history[-10:]:
        content = str(turn.get("content", "")).strip()
        if not content:
            continue
        if turn.get("role") == "user":
            user_bits.append(content[:220])
        else:
            assistant_bits.append(content[:500])

    healthy = _staging_is_healthy(ctx)
    lines = ["**From this chat session:**", ""]
    if user_bits:
        lines.append("**You asked:**")
        for u in user_bits[-4:]:
            lines.append(f"- {u}")
        lines.append("")
    if assistant_bits:
        lines.append("**I said earlier:**")
        for a in assistant_bits[-3:]:
            first = a.split("\n")[0].strip()[:200]
            if first:
                lines.append(f"- {first}")
        lines.append("")

    lines.extend([
        "**Current live state:**",
        f"- Pod: {ctx.get('pod_line') or 'unknown'}",
        f"- Healthy: **{'yes' if healthy else 'no'}**",
        f"- Argo CD: {tree.get('sync_status', 'Unknown')} / {tree.get('health_status', 'Unknown')}",
    ])
    if not healthy:
        lines.append(f"- Active issue: **{ctx.get('pod_reason') or 'workload not ready'}**")
    return "\n".join(lines)


def _pick_degraded_fallback(
    message: str, ctx: dict, tree: dict, lang: str, err: dict | None = None,
    history: list[dict] | None = None, gemini_api_ok: bool = False,
) -> str:
    preamble = _degraded_preamble(message, lang, err, ctx, gemini_api_ok=gemini_api_ok)
    if _wants_history_recap(message, history):
        return preamble + _history_recap_reply(history or [], ctx, tree, lang)
    if _is_fix_question(message):
        if _wants_manual_fix(message):
            return preamble + _manual_fix_steps_reply(ctx, tree, lang)
        return preamble + _demo_fix_steps_reply(ctx, tree, lang)
    if _wants_layman_explain(message) or "explain" in _intent_query(message):
        return preamble + _demo_rca_fallback_reply(ctx, tree, lang)
    if _is_root_cause_question(message):
        return preamble + _demo_rca_fallback_reply(ctx, tree, lang)
    return preamble + _demo_rca_fallback_reply(ctx, tree, lang)


def _format_history_block(history: list[dict] | None) -> str:
    if not history:
        return ""
    lines = []
    for turn in history[-8:]:
        role = str(turn.get("role", "user")).capitalize()
        content = str(turn.get("content", "")).strip()[:600]
        if content:
            lines.append(f"{role}: {content}")
    if not lines:
        return ""
    return "RECENT CHAT (use for follow-ups — e.g. 'how to fix' refers to prior diagnosis):\n" + "\n".join(lines) + "\n\n"


def _holmes_gemini_reply(
    message: str,
    ctx: dict,
    tree: dict,
    lang: str,
    on_step: StepCallback,
    chat_steps: int,
    history: list[dict] | None = None,
) -> tuple[bool, str, str]:
    """Call Holmes/Gemini with live facts + optional chat history."""
    facts = _holmes_cluster_facts(ctx, tree)
    lang_note = {
        "fr": "Reply entirely in French.",
        "hi": "Reply entirely in Hindi (Devanagari script).",
        "es": "Reply entirely in Spanish.",
    }.get(lang, "Reply in clear plain English.")
    history_block = _format_history_block(history)
    fix_note = ""
    if _wants_manual_fix(message):
        fix_note = (
            f"User wants a MANUAL fix via kubectl/terminal — give copy-paste bash commands: "
            f"`kubectl set image deployment/{cfg.DEPLOYMENT_NAME} {cfg.CONTAINER_NAME}={cfg.GOOD_IMAGE} "
            f"-n {cfg.NAMESPACE}`, scale, rollout status, get pods. "
            "Do NOT tell them to use Guided demo Step 4 or Auto-fix unless as optional footnote.\n"
        )
    elif _is_fix_question(message):
        fix_note = (
            "User asks how to fix — give numbered kubectl commands first, then mention "
            "Guided demo → Auto-fix as an alternative.\n"
        )
    layman_note = ""
    if _wants_layman_explain(message):
        layman_note = (
            "Explain in simple non-technical language for a business user. "
            "If explaining a prior answer, summarize what broke (bad image / ImagePullBackOff) "
            "and what the fix does — not only 'run kubectl get pods'.\n"
        )
    prompt = (
        f"{history_block}"
        f"User question: {message}\n\n"
        f"LIVE CLUSTER FACTS (authoritative — never contradict these):\n{facts}\n\n"
        f"{fix_note}{layman_note}"
        "Answer ONLY what the user asked. "
        "If they ask what broke / explain: cite the specific pod, image, and error from facts. "
        "If follow-up 'how to fix': reference RECENT CHAT diagnosis. "
        "Do not invent pod names or images. "
        f"{lang_note} Use markdown sections when helpful."
    )

    def step(title: str, detail: str = "", phase: str = "cluster") -> None:
        if on_step:
            on_step({"title": title, "detail": detail, "phase": phase})

    step("Running HolmesGPT", "Gemini + live cluster facts", "ai")
    return _run_holmes_cli_prompt(prompt, max_steps=chat_steps)


def _try_holmes_fast_answer(message: str, ctx: dict, tree: dict) -> str | None:
    """Instant accurate answers from live kubectl — no LLM latency."""
    lang = _requested_language(message)
    q = _normalize_query(message)
    healthy = _staging_is_healthy(ctx)
    image = ctx.get("image") or "unknown"
    image_short = image.split("/")[-1]
    pod_line = ctx.get("pod_line") or "no pods"
    sync = tree.get("sync_status", "Unknown")
    argo_health = tree.get("health_status", "Unknown")
    ready = ctx.get("ready_replicas", 0)
    replicas = ctx.get("replicas", -1)

    if _is_greeting(message):
        if lang == "fr":
            status = "en bonne santé" if healthy else "pas en bonne santé"
            return (
                f"Bonjour ! Je suis HolmesGPT sur **{cfg.NAMESPACE}**. fastapi est **{status}**.\n\n"
                f"- **Pod :** {pod_line}\n"
                f"- **Image :** `{image_short}`\n"
                f"- **Argo CD :** {sync} / {argo_health}"
            )
        status = "healthy and running" if healthy else "not fully healthy"
        return (
            f"Hello! I'm HolmesGPT on **{cfg.NAMESPACE}**. Right now fastapi looks **{status}**.\n\n"
            f"- **Pod:** {pod_line}\n"
            f"- **Image:** `{image_short}`\n"
            f"- **Argo CD:** {sync} / {argo_health}\n\n"
            "Ask for pod details, health status, or *explain in French*."
        )

    if re.match(r"^(how are you|how r u|how's it going|how is it going)\b", q):
        status = "healthy" if healthy else "watching an active issue"
        return (
            f"I'm online and connected to **{cfg.NAMESPACE}** — cluster looks **{status}** right now.\n\n"
            f"- **Pod:** {pod_line}\n"
            "Ask *pod status*, *what broke?*, or *fix manually* when you want details."
        )

    if _is_pod_details_telemetry(q):
        return _format_pod_details_reply(ctx, tree, lang)

    if _is_status_telemetry(q) or re.search(
        r"^(is my app healthy|is the app healthy|app healthy)\??$", q
    ):
        return _format_health_verdict(ctx, tree, lang)

    if "image" in q and any(w in q for w in ("what", "which", "tag", "using", "current")):
        return f"The deployment is using:\n\n`{image}`\n\n**Pod snapshot:** {pod_line}"

    if any(w in q for w in ("argo", "gitops")) and "sync" in q:
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
            "Run **Auto-fix** on the guided demo or ask me to explain the issue."
        )

    if any(
        phrase in q
        for phrase in (
            "what is wrong", "what's wrong", "whats wrong", "wrong with",
            "summarize", "summary", "client", "diagnosis",
        )
    ):
        return _telemetry_diagnosis_reply(ctx, tree, lang)

    return None


def _gemini_key_configured() -> bool:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key or len(key) > 120:
        return False
    low = key.lower()
    if any(bad in low for bad in ("bash", "export", "kubectl")):
        return False
    return key.startswith(("AIza", "AQ."))


def holmes_chat(
    message: str,
    on_step: StepCallback = None,
    history: list[dict] | None = None,
) -> dict:
    """Free-form HolmesGPT Q&A — separate from the guided demo Step 3."""
    global _last_gemini_failure
    _last_gemini_failure = None
    text = (message or "").strip()
    if not text:
        raise ValueError("Message is required")
    ok_cluster, cluster_msg = _cluster_reachable()
    if not ok_cluster:
        raise RuntimeError(cluster_msg)

    def step(title: str, detail: str = "", phase: str = "cluster") -> None:
        if on_step:
            on_step({"title": title, "detail": detail, "phase": phase})

    def result(
        reply: str, source: str, raw: str = "", degraded: bool = False,
        gemini_error: dict | None = None, **extra,
    ) -> dict:
        snap = holmes_snapshot()
        return {
            "ok": True,
            "reply": reply,
            "error": "",
            "source": source,
            "degraded": degraded,
            "gemini_error": gemini_error,
            "model": cfg.resolved_holmes_model(),
            "context": snap,
            "raw": raw[-2000:] if raw else "",
            **extra,
        }

    if cfg.CHAT_ACTIONS_ENABLED:
        if _needs_status_disambiguation(text):
            reply, choices = _status_disambiguation_reply()
            return result(reply, "action", ui="choices", choices=choices)
        act_type, target = _classify_chat_action(text)
        if act_type != "chat":
            try:
                act = _execute_chat_action(act_type, target, on_step)
                step("Done", act_type, "done")
                return result(
                    act["message"],
                    "action",
                    action=act.get("action"),
                    action_target=act.get("target"),
                    apps_status=act.get("apps_status"),
                    ui=act.get("ui"),
                    choices=act.get("choices"),
                    links=act.get("links"),
                )
            except Exception as exc:
                log.exception("Chat action failed")
                step("Action failed", str(exc)[:120], "cluster")
                return result(f"**Action failed:** {exc}", "error")

    if not cfg.HOLMES_ENABLED:
        raise RuntimeError(
            "HolmesGPT is disabled. Set HOLMES_ENABLED=true and configure a Gemini key in secret k8sgpt-ai."
        )

    step("Reading live cluster state", f"namespace {cfg.NAMESPACE}", "cluster")
    ctx = _incident_context()
    tree = _argocd_app_tree(ctx)
    healthy = _staging_is_healthy(ctx)
    lang = _resolved_language(text, history)

    step("Checking telemetry", "Argo CD + pod status", "cluster")

    if _is_fix_question(text) and _wants_manual_fix(text):
        step("Answer ready", "Manual kubectl steps", "done")
        return result(_manual_fix_steps_reply(ctx, tree, lang), "telemetry")

    if _is_telemetry_only_intent(text):
        fast = _try_holmes_fast_answer(text, ctx, tree)
        if fast:
            step("Answer ready", "Live cluster telemetry (instant)", "done")
            return result(fast, "telemetry")

    if _wants_history_recap(text, history):
        step("Answer ready", "Chat history recap", "done")
        return result(_history_recap_reply(history or [], ctx, tree, lang), "telemetry")

    api_ok = bool(gemini_health().get("ok"))
    if cfg.HOLMES_CHAT_DIRECT and api_ok:
        step("Answering with Gemini", "Live cluster facts", "ai")
        dg_ok, dg_reply = _direct_gemini_chat(text, ctx, tree, lang, history=history)
        if dg_ok and dg_reply.strip():
            step("Answer ready", "Gemini", "done")
            return result(dg_reply, "gemini")

    chat_steps = int(os.environ.get("HOLMES_CHAT_MAX_STEPS", "8"))
    holmes_ok, reply, raw = _holmes_gemini_reply(
        text, ctx, tree, lang, on_step, chat_steps, history=history,
    )
    if holmes_ok and reply.strip():
        step("HolmesGPT complete", "Answer ready", "done")
        return result(reply, "holmes", raw=raw)

    if api_ok:
        step("Trying Gemini direct", "Holmes agent fallback", "ai")
        dg_ok, dg_reply = _direct_gemini_chat(text, ctx, tree, lang, history=history)
        if dg_ok and dg_reply.strip():
            step("Answer ready", "Gemini direct", "done")
            return result(dg_reply, "gemini")

    step("Using factual fallback", "Holmes agent unavailable", "cluster")
    err_info = _last_gemini_failure
    if api_ok:
        err_info = {
            **(err_info or {}),
            "label": "holmes_agent_failed",
            "user_message": "Holmes agent timed out or returned no answer (Gemini API is reachable)",
        }
    fallback_body = _pick_degraded_fallback(
        text, ctx, tree, lang, err_info, history=history, gemini_api_ok=api_ok,
    )
    if not fallback_body.strip():
        fallback_body = _demo_rca_fallback_reply(ctx, tree, lang)
    fallback = fallback_body + _degraded_banner(lang, err_info, gemini_api_ok=api_ok)
    return result(fallback, "telemetry", raw=raw, degraded=True, gemini_error=err_info)


def explain_demo_app(app_id: str, on_step: StepCallback = None) -> dict:
    if app_id == "fastapi":
        return explain_with_ai(on_step=on_step)
    return _with_step_stream(on_step, lambda: _explain_demo_app_impl(app_id))


def _explain_demo_app_impl(app_id: str) -> dict:
    app = cfg.demo_app(app_id)
    ok, cluster_msg = _cluster_reachable()
    if not ok:
        raise RuntimeError(cluster_msg)
    timeline: list[dict[str, str]] = []
    _timeline_step(timeline, f"Starting AI-assisted diagnosis for {app['label']}", "Read-only cluster analysis")
    ctx = _incident_context_for_app(app)
    _timeline_step(timeline, "Inspecting pod state", ctx["pod_line"] or "no pods")
    summary, what_happened, root_cause, simple_explanation = _plain_language_explain(ctx)
    argo_name = app.get("argocd_app") or ""
    tree = {}
    if argo_name and _argocd_app_exists_named(argo_name):
        code, sh = _kubectl(
            "get", "application", argo_name, "-n", cfg.ARGOCD_NAMESPACE,
            "-o", "jsonpath={.status.sync.status}/{.status.health.status}",
        )
        sync_health = _kubectl_value(code, sh, "Unknown/Unknown")
        parts = sync_health.split("/", 1)
        tree = {
            "sync_status": parts[0] if parts else "Unknown",
            "health_status": parts[1] if len(parts) > 1 else "Unknown",
            "tree_summary": f"Argo CD application `{argo_name}` for {app['label']}.",
            "resources": [],
        }
    _timeline_step(timeline, "Diagnosis complete", root_cause or summary, phase="ai", pause=False)
    return {
        "summary": summary,
        "what_happened": what_happened,
        "root_cause": root_cause,
        "simple_explanation": simple_explanation,
        "argocd_tree": tree,
        "message": summary,
        "timeline": timeline,
        "findings": ctx.get("events", []),
        "holmes_ok": False,
        "holmes_enabled": cfg.HOLMES_ENABLED,
        "next_step": f"Say auto-fix {app_id} or click Step 4 Auto-fix to restore the good image.",
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
        if not holmes_ok and gemini_health().get("ok"):
            dg_ok, dg_reply = _direct_gemini_chat(
                "What broke in this Kubernetes staging outage? Root cause, pod evidence, and fix in plain English.",
                ctx,
                argocd_tree,
                "en",
            )
            if dg_ok and dg_reply.strip():
                holmes_ok = True
                holmes_summary = dg_reply
                holmes_raw = "gemini-direct-fallback"
                _timeline_step(
                    timeline,
                    "HolmesGPT summary (Gemini direct)",
                    holmes_summary[:120] + ("…" if len(holmes_summary) > 120 else ""),
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
