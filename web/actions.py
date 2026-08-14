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
from failure_modes import (
    FAILURE_MODES,
    MODE_EXPECTED_SIGNALS,
    SERVICE_LEVEL_MODE_IDS,
    classify_failure_mode,
    classify_failure_modes,
    clear_all_failure_injections,
    describe_expected_failure,
    describe_expected_failure_plain,
    expected_signals_for_modes,
    inject_mode_chips,
    format_active_failure_headline,
    failure_mode_label,
    failure_mode_layman_explain,
    failure_modes_by_category,
    chaos_mesh_info,
    inject_failure_mode,
    inject_failure_modes,
    list_demo_scenarios,
    list_failure_modes,
    scenario_by_id,
)

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


def _kubectl_apply_yaml(yaml_text: str) -> tuple[int, str]:
    """kubectl apply -f - (used when manifest files are not baked into the container image)."""
    content = (yaml_text or "").strip()
    if not content:
        return 1, "empty manifest yaml"
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
        p = subprocess.run(
            _kubectl_cmd("apply", "-f", "-"),
            input=content,
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
        out = ((p.stdout or "") + (p.stderr or "")).strip()
        return p.returncode, out
    except Exception as e:
        return 1, str(e)


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
        ui_base = _public_ui_base()
        if ui_base:
            links["app_health"] = f"{ui_base}/staging/health"
            links["app_dashboard"] = f"{ui_base}/staging/"

    if not _valid_browser_url(links.get("argocd", "")):
        argo_base = _public_argo_base()
        if argo_base:
            links["argocd"] = argo_base
            links["argocd_app"] = f"{argo_base}/applications/{cfg.ARGOCD_NAMESPACE}/{cfg.ARGOCD_APP}"
    elif not _valid_browser_url(links.get("argocd_app", "")):
        base = links["argocd"].rstrip("/")
        links["argocd_app"] = f"{base}/applications/{cfg.ARGOCD_NAMESPACE}/{cfg.ARGOCD_APP}"

    for key in links:
        if not _valid_browser_url(links[key]):
            links[key] = ""
    return links


def _public_ui_base() -> str:
    """HTTPS base for browser links (ingress hostname preferred over raw LB IP)."""
    base = (cfg.PUBLIC_UI_BASE_URL or "").rstrip("/")
    if _valid_browser_url(base):
        return base
    ui_host = _lb_host("selfheal", "selfheal-ui")
    if not ui_host:
        return ""
    if ui_host.replace(".", "").isdigit():
        return f"http://{ui_host}"
    return f"https://{ui_host}"


def _public_argo_base() -> str:
    base = (cfg.PUBLIC_ARGOCD_HOST or cfg.PUBLIC_ARGOCD_URL or "").rstrip("/")
    if _valid_browser_url(base):
        return base
    argo_host = _lb_host(cfg.ARGOCD_NAMESPACE, "argocd-server")
    if argo_host:
        return f"https://{argo_host}"
    return ""


def resolved_public_app_links() -> dict[str, dict[str, str]]:
    """Browser links for each demo app, plus shared Argo CD entrypoints."""
    app_links = {k: dict(v) for k, v in cfg.public_app_links().items()}
    ui_base = _public_ui_base()
    argo_base = _public_argo_base()

    fastapi = app_links.get("fastapi", {})
    if ui_base:
        if not _valid_browser_url(fastapi.get("dashboard", "")):
            fastapi["dashboard"] = f"{ui_base}/staging/"
        if not _valid_browser_url(fastapi.get("health", "")):
            fastapi["health"] = f"{ui_base}/staging/health"
    if not _valid_browser_url(fastapi.get("argocd_app", "")) and argo_base:
        fastapi["argocd_app"] = f"{argo_base}/applications/{cfg.ARGOCD_NAMESPACE}/{cfg.ARGOCD_APP}"
    fastapi["argocd"] = argo_base
    app_links["fastapi"] = fastapi

    nginx = app_links.get("nginx", {})
    if ui_base:
        if not _valid_browser_url(nginx.get("dashboard", "")):
            nginx["dashboard"] = f"{ui_base}/nginx/"
        if not _valid_browser_url(nginx.get("health", "")):
            nginx["health"] = f"{ui_base}/nginx/"
    if not _valid_browser_url(nginx.get("argocd_app", "")) and argo_base:
        nginx["argocd_app"] = f"{argo_base}/applications/{cfg.ARGOCD_NAMESPACE}/{cfg.NGINX_ARGOCD_APP}"
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


def _collect_container_issues(container_statuses: list) -> list[dict[str, str]]:
    """All distinct container waiting/terminated reasons (combo inject shows each)."""
    issues: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for cs in container_statuses or []:
        cname = cs.get("name", "container")
        for state_key in ("waiting", "terminated"):
            st = (cs.get("state") or {}).get(state_key) or {}
            reason = (st.get("reason") or "").strip()
            if not reason:
                continue
            key = (cname, reason)
            if key in seen:
                continue
            seen.add(key)
            issues.append({
                "container": cname,
                "reason": reason,
                "message": (st.get("message") or "")[:200],
            })
    return issues


def _parse_pod_json_items(items: list) -> list[dict]:
    pods: list[dict] = []
    for item in items:
        meta = item.get("metadata", {})
        status = item.get("status", {})
        name = meta.get("name", "")
        phase = status.get("phase", "Unknown")
        container_statuses = status.get("containerStatuses") or []
        ready_count = sum(1 for cs in container_statuses if cs.get("ready"))
        total = len(container_statuses)
        ready_str = f"{ready_count}/{total}" if total else "?/?"
        container_errors = _collect_container_issues(container_statuses)
        if container_errors:
            display = " + ".join(
                f"{e['reason']} ({e['container']})" for e in container_errors
            )
            reason = container_errors[0]["reason"]
            message = container_errors[0].get("message", "")
        else:
            reason = ""
            message = ""
            display = phase
        pods.append({
            "name": name,
            "phase": phase,
            "ready": ready_str,
            "status": display,
            "reason": reason,
            "message": message,
            "container_errors": container_errors,
            "line": f"{name} · {ready_str} · {display}",
        })
    pods.sort(key=_pod_severity)
    return pods


def _pod_detail_for_label(pod_label: str) -> dict:
    pods = _fetch_pods_for_label(pod_label)
    active = [p for p in pods if p["phase"] != "Terminating"]
    pick = (active or pods)[:1]
    if not pick:
        return {"line": "no pods", "container_errors": []}
    return {
        "line": pick[0]["line"],
        "container_errors": pick[0].get("container_errors") or [],
        "pod_name": pick[0]["name"],
    }


def _pod_line_with_injection(app: dict, base_line: str) -> str:
    """Append active chaos mode names when service-level failures hide in pod phase."""
    modes = _app_injected_modes(app)
    if not modes:
        return base_line
    from failure_modes import failure_mode_label
    labels = [failure_mode_label(m) for m in modes[:4]]
    return f"{base_line} · Injected: {' + '.join(labels)}"


def _pod_ready_not_full(line: str) -> bool:
    """True when ready count < total (e.g. 0/1, 1/2) — matches Argo CD Degraded."""
    import re
    m = re.search(r"(\d+)/(\d+)", line or "")
    if not m:
        return False
    ready, total = int(m.group(1)), int(m.group(2))
    return total > 0 and ready < total


def _pod_display_line(pod_label: str) -> str:
    """Readable pod line — all container errors when combo inject (crash + OOM sidecar)."""
    return _pod_detail_for_label(pod_label)["line"]


def _fetch_pods_for_label(pod_label: str) -> list[dict]:
    code, out = _kubectl(
        "get", "pods", "-n", cfg.NAMESPACE, "-l", pod_label, "-o", "json",
    )
    if code != 0 or not out.strip():
        return []
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return []
    return _parse_pod_json_items(data.get("items", []))


def _pod_rows_for_label(pod_label: str) -> list[dict[str, str]]:
    """Parse kubectl pod lines into structured rows (skips wide-output noise)."""
    code, out = _kubectl(
        "get", "pods", "-n", cfg.NAMESPACE, "-l", pod_label, "--no-headers",
    )
    rows: list[dict[str, str]] = []
    if code != 0 or not out.strip():
        return rows
    for line in out.strip().splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        rows.append({"name": parts[0], "ready": parts[1], "phase": parts[2]})
    return rows


def _app_heal_phase(app: dict) -> str:
    """ok | recovering | unhealthy"""
    if _app_has_active_injection(app):
        return "unhealthy"
    if _app_is_healthy(app):
        return "ok"
    rows = _pod_rows_for_label(app["pod_label"])
    active = [r for r in rows if r["phase"] != "Terminating"]
    if not active:
        return "unhealthy"
    phase = active[0]["phase"]
    if phase in ("ContainerCreating", "PodInitializing", "Pending"):
        return "recovering"
    if active[0]["ready"] == "1/1" and phase == "Running":
        return "ok"
    return "unhealthy"


def _wait_post_heal_health(app_ids: list[str], *, timeout: int = 90) -> None:
    """After heal, poll until apps are healthy or timeout (avoids stale UNHEALTHY cards)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if all(_app_is_healthy(cfg.demo_app(aid)) for aid in app_ids):
            return
        time.sleep(3)


def _format_heal_app_summary(app_id: str, result: dict | None = None) -> dict:
    """Structured per-app heal outcome for chat UI."""
    app = cfg.demo_app(app_id)
    result = result or {}
    injected = _app_has_active_injection(app)
    phase = _app_heal_phase(app)
    gitops = _argocd_status_for_app(app)
    healthy = (
        not injected
        and (bool(result.get("healthy") or result.get("app_reachable")) or phase == "ok")
    )
    if healthy and gitops and not _argocd_is_synced_healthy(gitops):
        status, status_key = "Recovering", "warn"
        detail = f"Pod is healthy; GitOps catching up ({gitops})."
    elif healthy:
        status, status_key = "Recovered", "ok"
        detail = "Health check passing."
    elif injected:
        status, status_key = "Outage active", "bad"
        detail = "Failure injection still active — run auto-fix again."
    elif phase == "recovering":
        status, status_key = "Recovering", "warn"
        detail = "Pod is still starting — usually green within 30 seconds."
    else:
        status, status_key = "Needs attention", "bad"
        raw_err = str(result.get("heal_error") or result.get("error") or "").strip()
        if raw_err:
            detail = raw_err[:140]
        else:
            detail = "Pod not ready yet — refresh status or run auto-fix again."
    return {
        "id": app_id,
        "label": app["label"],
        "status": status,
        "status_key": status_key,
        "pod_line": _pod_display_line(app["pod_label"]),
        "gitops": gitops,
        "detail": detail,
        "healthy": healthy and status_key == "ok",
        "links": _app_browser_links(app_id),
    }


def _already_deployed_reply(app_id: str) -> tuple[str, list[dict[str, str]]]:
    """Message + choice chips when user deploys an app that already exists."""
    app = cfg.demo_app(app_id)
    if _app_is_healthy(app):
        msg = (
            f"**{app['label']} is already deployed** and healthy in `{cfg.NAMESPACE}`.\n\n"
            "No changes were made. You can **reset** (tear down / undeploy) or simulate a failure."
        )
    else:
        msg = (
            f"**{app['label']} is already deployed** in `{cfg.NAMESPACE}` but not fully healthy yet.\n\n"
            "You can wait for pods, run **auto-fix**, or **reset** to tear down."
        )
    choices = [
        {"label": f"Reset {app['label']}", "prompt": f"reset {app_id}"},
        {"label": f"Auto-fix {app['label']}", "prompt": f"auto-fix {app_id}"},
        {"label": "Pod status", "prompt": f"show pod status for {app_id}"},
    ]
    return msg, choices


def _both_already_deployed_reply() -> tuple[str, list[dict[str, str]]]:
    rows = _apps_status_data()
    healthy = sum(1 for r in rows if r["healthy"])
    total = len(rows)
    msg = (
        f"**Both demo apps are already deployed** in `{cfg.NAMESPACE}` "
        f"({healthy}/{total} healthy).\n\n"
        "No changes were made. You can **reset** either app or both, or simulate a failure."
    )
    choices = [
        {"label": "Reset FastAPI", "prompt": "reset fastapi"},
        {"label": "Reset Nginx", "prompt": "reset nginx"},
        {"label": "Reset both apps", "prompt": "reset both apps"},
        {"label": "Show cluster status", "prompt": "show cluster status"},
    ]
    return msg, choices


def _format_heal_all_message(summaries: list[dict]) -> str:
    """Short markdown summary — details render in heal_summary cards."""
    ok = sum(1 for s in summaries if s.get("status_key") == "ok")
    warn = sum(1 for s in summaries if s.get("status_key") == "warn")
    total = len(summaries)
    if ok == total:
        headline = f"**Auto-fix complete** — {_demo_health_phrase(ok, total).strip('**')}."
    elif warn:
        headline = (
            f"**Auto-fix complete** — {ok}/{total} healthy, "
            f"{warn} still starting (Argo CD may already show green)."
        )
    else:
        headline = f"**Auto-fix applied** — {ok}/{total} apps fully recovered."
    bullets = []
    icons = {"ok": "✓", "warn": "◷", "bad": "✗"}
    for s in summaries:
        icon = icons.get(s.get("status_key"), "·")
        bullets.append(
            f"- {icon} **{s['label']}** — {s['status']} · `{s['pod_line']}`"
            + (f" · GitOps `{s['gitops']}`" if s.get("gitops") else "")
        )
    return headline + "\n\n" + "\n".join(bullets)


def _app_needs_heal(app_id: str) -> bool:
    app = cfg.demo_app(app_id)
    if not _app_workloads_exist(app):
        return False
    return not _app_is_healthy(app) or _app_has_active_injection(app)


def _already_healthy_reply(target: str) -> dict | None:
    """Return a chat payload when heal/auto-fix is unnecessary."""
    if target == "all":
        rows = [r for r in _apps_status_data() if r.get("deployed")]
        if not rows:
            return None
        if any(_app_needs_heal(r["id"]) for r in rows):
            return None
        lines = [
            f"{_demo_health_phrase(len(rows), len(rows), already=True)}.\n",
            "No auto-fix needed — pods are Running and there is no active chaos injection.\n",
        ]
        for r in rows:
            lines.append(f"- **{r['label']}** — {r['state']} · `{r['pod_line']}`")
        lines.append("\nSay **pod status** anytime, or **simulate outage** to run a demo failure.")
        return {
            "message": "\n".join(lines),
            "apps_status": rows,
            "heal_summary": [
                _format_heal_app_summary(r["id"], {"healthy": True, "app_reachable": True})
                for r in rows
            ],
        }
    if not _app_workloads_exist(cfg.demo_app(target)):
        return None
    if _app_needs_heal(target):
        return None
    app = cfg.demo_app(target)
    row = next((r for r in _apps_status_data() if r["id"] == target), None)
    msg = (
        f"**{app['label']} is already healthy.**\n\n"
        f"- Pod: `{row['pod_line'] if row else _pod_display_line(app['pod_label'])}`\n"
        f"- GitOps: `{row['gitops'] if row else 'n/a'}`\n\n"
        "No auto-fix needed. Say **simulate outage** to inject a demo failure."
    )
    return {
        "message": msg,
        "apps_status": [row] if row else [],
        "heal_summary": [_format_heal_app_summary(target, {"healthy": True, "app_reachable": True})],
    }


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


def _argocd_is_synced_healthy(status: str) -> bool:
    """True only when Argo CD reports Synced and Healthy (not OutOfSync/Healthy)."""
    parts = (status or "").split("/", 1)
    return len(parts) == 2 and parts[0] == "Synced" and parts[1] == "Healthy"


def _argocd_wait_synced_named(app_name: str, timeout: int = 90) -> str:
    deadline = time.time() + timeout
    last = _argocd_sync_status_named(app_name)
    while time.time() < deadline:
        if _argocd_is_synced_healthy(last):
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


def _register_argocd_app_manifest(manifest: Path, *, yaml_text: str = "") -> tuple[int, str]:
    if manifest.is_file():
        return _kubectl("apply", "-f", str(manifest))
    content = (yaml_text or "").strip()
    if content:
        return _kubectl_apply_yaml(content)
    return 1, f"Application manifest not found: {manifest}"


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
    return _register_argocd_app_manifest(cfg.ARGOCD_APP_MANIFEST, yaml_text=cfg.FASTAPI_ARGOCD_APP_YAML)


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
    return _argocd_restore_for_app(cfg.demo_app("fastapi"))


def _argocd_restore_for_app(app: dict) -> tuple[int, str]:
    """Re-apply Argo CD Application spec for a demo workload."""
    manifest = app.get("argocd_manifest")
    if manifest and Path(manifest).is_file():
        return _kubectl("apply", "-f", str(manifest))
    if app.get("id") == "fastapi" and cfg.FASTAPI_ARGOCD_APP_YAML:
        return _kubectl_apply_yaml(cfg.FASTAPI_ARGOCD_APP_YAML)
    argo_app = app.get("argocd_app") or ""
    if argo_app:
        return _argocd_set_automated_named(argo_app, True)
    return 0, ""


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


def _pod_ready_for_label(pod_label: str) -> bool:
    code, out = _kubectl(
        "get", "pods", "-n", cfg.NAMESPACE, "-l", pod_label, "--no-headers",
    )
    if code != 0 or not out.strip():
        return False
    for line in out.strip().splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[1] == "1/1" and parts[2] == "Running":
            return True
    return False


def _pod_summary_for_label(pod_label: str) -> str:
    code, pods = _kubectl(
        "get", "pods", "-n", cfg.NAMESPACE, "-l", pod_label, "--no-headers",
    )
    return _kubectl_value(code, pods, "no pods")


def _wait_pod_ready_with_steps(
    timeline: list[dict[str, str]],
    *,
    pod_label: str | None = None,
    timeout: int = 90,
    title: str = "Waiting for healthy pod",
) -> bool:
    """Poll pod readiness — works when rollout status is stuck on a bad revision."""
    label = pod_label or cfg.POD_LABEL
    deadline = time.time() + timeout
    last_detail = ""
    tick = 0
    while time.time() < deadline:
        if _pod_ready_for_label(label):
            _timeline_step(
                timeline,
                "Pod is ready",
                _pod_summary_for_label(label),
                phase="k8s",
                pause=False,
            )
            return True
        detail = _pod_summary_for_label(label)
        if detail != last_detail or tick % 2 == 0:
            _timeline_step(timeline, title, detail, phase="k8s", pause=False)
            last_detail = detail
        tick += 1
        time.sleep(3)
    return False


def _wait_rollout_with_steps(
    timeline: list[dict[str, str]],
    *,
    timeout: int = 120,
    title: str = "Waiting for rollout to complete",
) -> None:
    """Poll pod readiness (rollout status lies when a bad revision is still progressing)."""
    if _wait_pod_ready_with_steps(timeline, timeout=timeout, title=title):
        return
    raise RuntimeError(f"Pod not ready after {timeout}s. {_pod_troubleshoot()}")


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


def _cluster_api_ok() -> tuple[bool, str]:
    """Namespace-scoped API access — enough for chat telemetry and read-only answers."""
    ns = cfg.NAMESPACE
    code, out = _kubectl(
        "get", "deployment", "-n", ns,
        "--request-timeout=10s", "--no-headers",
    )
    if code == 0:
        return True, ""
    code2, out2 = _kubectl("get", "ns", ns, "--request-timeout=10s")
    if code2 == 0:
        return True, ""
    return _cluster_reachable()


def _cluster_reachable() -> tuple[bool, str]:
    code, out = _kubectl("get", "nodes", "--request-timeout=10s", "--no-headers")
    if code == 0 and out.strip():
        return True, ""
    ns = cfg.NAMESPACE
    code_ns, out_ns = _kubectl("get", "deployment", "-n", ns, "--request-timeout=10s", "--no-headers")
    if code_ns == 0:
        return True, ""
    if _cluster_offline(out) or _cluster_offline(out_ns):
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


def _probe_app_health(
    app_id: str,
    app: dict,
    *,
    pod_ok: bool = False,
    workloads_exist: bool = False,
    timeout: int = 2,
) -> bool:
    """Best-effort health: in-cluster URL, UI proxy, then healthy pod fallback."""
    health_url = app.get("health_url") or ""
    if _reachable(health_url, timeout=timeout):
        return True
    ui_proxy = "http://selfheal-ui.selfheal.svc.cluster.local"
    if app_id == "nginx" and _reachable(f"{ui_proxy}/nginx/", timeout=timeout):
        return True
    if app_id == "fastapi" and _reachable(f"{ui_proxy}/staging/health", timeout=timeout):
        return True
    return bool(pod_ok and workloads_exist)


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
    checks["deployments"] = "ok" if _argocd_reachable() else "fail"

    if not ok:
        checks["pod"] = "cluster offline"
        checks["gitops_app"] = "cluster offline"
        checks["argocd_app_exists"] = False
        checks["workloads_exist"] = False
        checks["app_deployed"] = False
        checks["app_clean"] = False
        checks["app_status_message"] = cluster_msg
        checks["app_health"] = "fail"
        checks["app_health_check"] = "fail"
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
    checks["app_health_check"] = (
        "ok" if _probe_app_health(
            app_id, app, pod_ok=pod_ok, workloads_exist=checks["workloads_exist"], timeout=health_timeout,
        ) else "fail"
    )
    checks["app_health"] = checks["app_health_check"]
    gitops_parts = (checks["gitops_app"] or "").split("/", 1)
    gitops_health = gitops_parts[1] if len(gitops_parts) > 1 else ""

    if app.get("gitops"):
        checks["app_deployed"] = (
            checks["workloads_exist"]
            and checks["argocd_app_exists"]
            and pod_ok
            and checks["app_health_check"] == "ok"
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
    elif not pod_ok or checks["app_health_check"] != "ok":
        checks["app_status_message"] = (
            f"Outage in progress — {app['label']} is down on purpose. Continue to Step 3 Explain, then Step 4 Auto-fix."
        )
    elif app.get("gitops") and gitops_health != "Healthy":
        checks["app_status_message"] = (
            f"{app['label']} is running but Argo CD reports {checks['gitops_app']} — you can continue the demo."
        )
    else:
        checks["app_status_message"] = ""

    if app_id == "fastapi":
        checks["staging_deployed"] = checks["app_deployed"]
        checks["staging_clean"] = checks["app_clean"]
        checks["staging_status_message"] = checks["app_status_message"]
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
    _pause_argocd_autosync_named(cfg.ARGOCD_APP)


def _pause_gitops_for_injection(
    app: dict,
    timeline: list[dict[str, str]] | None = None,
) -> None:
    """Pause Argo CD auto-sync so kubectl injections are not immediately reverted."""
    argo_app = app.get("argocd_app") or ""
    if not app.get("gitops") or not argo_app:
        return
    if timeline is not None:
        _timeline_step(
            timeline,
            "Pausing Argo CD auto-sync",
            f"{argo_app} — GitOps won't heal before auto-fix",
            phase="break",
        )
    _pause_argocd_autosync_named(argo_app)


def _pause_gitops_for_demo_apps(app_ids: tuple[str, ...] = ("fastapi", "nginx")) -> None:
    for aid in app_ids:
        _pause_gitops_for_injection(cfg.demo_app(aid))


def _pause_argocd_autosync_named(argo_app: str) -> None:
    if not argo_app:
        return
    patch = _patch_file(
        "argocd-no-heal.json",
        '{"spec":{"syncPolicy":{"automated":null}}}',
    )
    code, patch_out = _kubectl(
        "patch", "application", argo_app, "-n", cfg.ARGOCD_NAMESPACE,
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
    return _simulate_guided_outage_impl("fastapi")


def _simulate_guided_outage_impl(app_id: str) -> dict:
    """Classic guided-demo outage (bad image / crash / scale-0) for FastAPI or Nginx."""
    app = cfg.demo_app(app_id)
    dep = app["deployment"]
    container = app["container"]
    pod_label = app["pod_label"]
    bad_image = app.get("bad_image") or cfg.BAD_IMAGE
    argo_app = app.get("argocd_app") or cfg.ARGOCD_APP
    app_label = app["label"]

    ok, cluster_msg = _cluster_reachable()
    if not ok:
        raise RuntimeError(cluster_msg)

    timeline: list[dict[str, str]] = []
    _timeline_step(
        timeline,
        f"Preparing incident simulation for {app_label}",
        "Safe for staging — production is not touched",
    )
    if app.get("gitops") and argo_app:
        _timeline_step(
            timeline,
            "Pausing Argo CD auto-sync",
            f"{argo_app} — GitOps won't heal before Explain + Auto-fix",
        )
        _pause_gitops_for_injection(app)

    mode = cfg.OUTAGE_MODE
    mode_note = ""

    if mode == "instant":
        _timeline_step(timeline, "Scaling deployment to zero replicas", f"{dep} — fast outage")
        _kubectl_must(
            "scale", f"deployment/{dep}",
            "-n", cfg.NAMESPACE, "--replicas=0",
            action=f"Scale {dep} to zero",
        )
        mode_note = f"{app_label} scaled to 0 replicas — app down in seconds."
    elif mode == "crash":
        _timeline_step(timeline, "Injecting crash-loop command into container", f"{dep} exits on start")
        if app_id == "fastapi":
            _clear_crash_override()
        patch = _patch_file(
            f"guided-crash-{dep}.json",
            '[{"op":"add","path":"/spec/template/spec/containers/0/command",'
            '"value":["/bin/sh","-c","exit 1"]},'
            '{"op":"replace","path":"/spec/template/spec/containers/0/args","value":[]}]',
        )
        code, patch_out = _kubectl(
            "patch", "deployment", dep, "-n", cfg.NAMESPACE,
            "--type", "json", f"--patch-file={patch}",
        )
        if code != 0:
            raise RuntimeError(_kubectl_value(code, patch_out, "Failed to inject crash command"))
        _kubectl("delete", "pods", "-n", cfg.NAMESPACE, "-l", pod_label, "--wait=false")
        _timeline_step(timeline, "Waiting for CrashLoopBackOff", "Argo CD should show Degraded")
        mode_note = f"Crash loop on {app_label} — pod shows CrashLoopBackOff in ~15s."
    else:
        mode = "image"
        _timeline_step(
            timeline,
            f"Patching {app_label} with invalid container image",
            bad_image,
        )
        code, set_out = _kubectl(
            "set", "image",
            f"deployment/{dep}",
            f"{container}={bad_image}",
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
            "Argo CD Progressing → Degraded in ~1–2 minutes",
        )
        mode_note = f"Bad image on {app_label} — ErrImagePull / ImagePullBackOff expected."

    deadline = time.time() + (6 if mode == "instant" else 18)
    while time.time() < deadline:
        if mode == "instant":
            code, rep = _kubectl(
                "get", "deployment", dep, "-n", cfg.NAMESPACE,
                "-o", "jsonpath={.spec.replicas}",
            )
            if code == 0 and rep.strip() == "0":
                break
        elif not _app_is_healthy(app):
            break
        time.sleep(2)

    code, pods = _kubectl(
        "get", "pods", "-n", cfg.NAMESPACE, "-l", pod_label, "--no-headers",
    )
    if argo_app:
        code2, argo = _kubectl(
            "get", "application", argo_app, "-n", cfg.ARGOCD_NAMESPACE,
            "-o", "jsonpath={.status.sync.status}/{.status.health.status}",
        )
        argo_val = _kubectl_value(code2, argo, "unknown")
    else:
        argo_val = "n/a"

    app_links = _app_browser_links(app_id)
    links = resolved_public_app_links()
    health_url = app_links.get("health") or app.get("health_url") or ""
    app_down = not _probe_app_health(app_id, app, timeout=3) if health_url else not _app_is_healthy(app)
    _timeline_step(
        timeline,
        f"Outage active — {app_label} is down",
        _kubectl_value(code, pods, "no pods"),
        pause=False,
    )
    tips = [
        f"{app_label} health should show Down.",
        f"Open Argo CD for {argo_app or app_label} to show Degraded.",
    ]
    if app_links.get("dashboard"):
        tips.insert(1, f"App UI: {app_links['dashboard']}")

    return {
        "mode": mode,
        "app": app_id,
        "timeline": timeline,
        "pods": _kubectl_value(code, pods, "no pods"),
        "argocd": argo_val,
        "app_down": app_down,
        "tips": tips,
        "open_url": app_links.get("argocd_app") or links.get("argocd_app", cfg.PUBLIC_ARGOCD_APP_URL),
        "staging_url": app_links.get("dashboard") or links.get("app_dashboard", cfg.PUBLIC_APP_DASHBOARD_URL),
        "health_url": app_links.get("health") or links.get("app_health", cfg.PUBLIC_APP_HEALTH_URL),
        "message": (
            f"**Outage simulated on {app_label}** — app is down on purpose for the guided demo. "
            + mode_note
        ),
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
        ("outrgae", "outrage"),
        ("outarge", "outrage"),
        ("outrage", "outage"),
        ("simulte", "simulate"),
        ("simualte", "simulate"),
        ("stimualte", "stimulate"),
        ("stimulate", "simulate"),
        ("autofiz", "autofix"),
        ("auto fiz", "autofix"),
        ("auto-fiz", "autofix"),
        ("fic both", "fix both"),
        ("fic my", "fix my"),
        (" fic ", " fix "),
        ("fic ", "fix "),
        ("ngnix", "nginx"),
        ("ngix", "nginx"),
        ("ninx", "nginx"),
        ("fastpi", "fastapi"),
        ("imagpull", "imagepull"),
        ("crashloop", "crash loop"),
        ("crashloopbackoff", "crash loop"),
        ("crashloopbackoof", "crash loop"),
        ("sattus", "status"),
        ("staus", "status"),
        ("statu ", "status "),
        ("pod stat ", "pod status "),
        ("frech", "french"),
        ("frnech", "french"),
        ("leyman", "layman"),
        ("manaually", "manually"),
        ("manaual", "manual"),
        ("tell em", "tell me"),
        ("pods detail", "pod details"),
        ("pod detail", "pod details"),
        ("self heal", "self-heal"),
        ("selfheal", "self-heal"),
    ):
        q = q.replace(typo, fix)
    return q


def _classify_error_mode(message: str) -> str:
    """Map natural language to a demo-safe failure injection mode."""
    return classify_failure_mode(_normalize_query(message))


def _error_mode_label(mode: str) -> str:
    return failure_mode_label(mode)


_INTENT_FILLERS = re.compile(
    r"\b(actually|exactly|really|literally|just|simply|please)\b",
    re.I,
)

_ROOT_CAUSE_PHRASES = (
    "what broke",
    "what break",
    "what happened",
    "what went wrong",
    "what caused",
    "caused this outage",
    "root cause",
    "what failed",
    "what's broken",
    "whats broken",
    "not working",
    "why is",
    "why isn't",
    "why are",
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
    structured = _fetch_pods_for_label(pod_label)
    if structured:
        ctx["pod_line"] = "\n".join(p["line"] for p in structured[:3])
        primary = structured[0]
        ctx["pod_name"] = primary["name"]
        ctx["pod_phase"] = primary["phase"]
        ctx["pod_reason"] = primary["reason"] or primary["phase"]
        ctx["pod_message"] = primary.get("message", "")
    else:
        ctx["pod_line"] = _kubectl_value(code, pod_line, "no pods")
        if code == 0 and pod_line.strip():
            ctx["pod_name"] = pod_line.strip().split()[0]
    ctx["events"] = _kubectl_events_findings(cfg.NAMESPACE, limit=6, pod_name=ctx["pod_name"])
    code_ann, ann = _kubectl(
        "get", "deployment", dep, "-n", cfg.NAMESPACE,
        "-o", "jsonpath={.metadata.annotations.enlight-lab/injected-modes}",
    )
    if code_ann == 0 and ann.strip():
        ctx["injected_modes"] = [m.strip() for m in ann.split(",") if m.strip()]
        ctx["injected_summary"] = describe_expected_failure(ctx["injected_modes"])
    return ctx


def _plain_language_explain(ctx: dict) -> tuple[str, list[str], str, str]:
    """Return (headline, bullet facts, root_cause label, simple_paragraph for clients)."""
    replicas = ctx["replicas"]
    ready = ctx["ready_replicas"]
    image = ctx["image"] or "unknown"
    reason = (ctx["pod_reason"] or "").strip()
    reason_l = reason.lower()
    bad_image = ctx.get("bad_image") or cfg.BAD_IMAGE
    bad_tag = bad_image.split(":")[-1].lower() if bad_image else ""
    image_l = image.lower()
    app_label = ctx.get("app_label") or "staging app"
    dep_name = ctx.get("deployment") or cfg.DEPLOYMENT_NAME
    injected = ctx.get("injected_modes") or []

    if injected:
        primary_mode = injected[0]
        headline, root, simple = failure_mode_layman_explain(primary_mode, app_label)
        labels = ", ".join(failure_mode_label(m) for m in injected)
        summary = ctx.get("injected_summary") or describe_expected_failure(injected)
        ev_lines = [e for e in (ctx.get("events") or [])[:3] if e]
        pod_note = ctx.get("pod_line") or "unknown"
        if "crashloop" in reason_l and primary_mode == "startup":
            simple += (
                "\n\n*Note: CrashLoopBackOff can appear after startup probe retries — "
                "the injected failure is still **startup probe failure**, not a generic crash.*"
            )
        bullets = [
            f"Injected failure: **{labels}**",
            summary,
            f"Current pod: `{pod_note}`",
            *ev_lines,
        ]
        if reason and root.lower() not in reason_l:
            bullets.append(f"Observed pod reason: {reason}")
        return headline, bullets, root, simple

    if replicas == 0:
        return (
            f"{app_label} has zero running pods — nothing is serving traffic.",
            [
                f"We simulated an outage by scaling {dep_name} to 0 replicas.",
                "ArgoCD and the demo UI are still running; only this workload is down.",
                f"Health checks for {app_label} fail until you run Auto-fix.",
            ],
            "Scaled to 0 replicas (instant outage mode)",
            f"In simple terms: we deliberately shut off every running copy of {app_label} — like closing all store "
            "locations at once. Kubernetes shows zero pods, so the app URL stops responding. "
            "This is a safe demo outage; ArgoCD and the control UI are still up.",
        )

    if "errimagepull" in reason_l or "imagepullbackoff" in reason_l or "does-not-exist" in image_l or (
        bad_tag and bad_tag in image_l
    ):
        msg = ctx["pod_message"] or "Registry rejected or could not find the image tag."
        return (
            f"{app_label} cannot start — Kubernetes cannot pull the container image.",
            [
                f"Deployment is set to image: {image}",
                f"Pod state: {reason or 'ImagePullBackOff'} — {msg[:160]}",
                "This is the intentional demo outage (bad image). GitOps still tracks the good image in Git.",
                "Next step: click Auto-fix to restore the known-good image and sync ArgoCD.",
            ],
            reason or "ErrImagePull / ImagePullBackOff",
            f"In simple terms: we pointed {app_label} at a container image that does not exist in the registry — "
            "like giving a delivery driver a wrong address. Kubernetes keeps retrying but the pod never starts, "
            "so clients see the app as down. Your Git repo still has the correct image; only the live cluster is wrong.",
        )

    if "oomkilled" in reason_l:
        return (
            f"{app_label} container was killed for exceeding its memory limit (OOMKilled).",
            [
                f"Pod {ctx['pod_name'] or dep_name} — {reason}.",
                ctx["pod_message"][:160] if ctx["pod_message"] else "Container exceeded memory limit (exit 137).",
                "Kubernetes restarts the container; memory pressure continues until limits are fixed.",
            ],
            reason or "OOMKilled",
            f"In simple terms: {app_label} used more RAM than its limit allows. "
            "Kubernetes killed the container to protect the node — you'll see OOMKilled in events.",
        )

    if "failedcreatepodsandbox" in reason_l or "memory limit" in (ctx.get("pod_message") or "").lower():
        return (
            f"{app_label} cannot start — invalid pod memory configuration.",
            [
                f"Pod event: {reason or 'FailedCreatePodSandBox'}.",
                (ctx["pod_message"] or "Memory limit below runtime minimum.")[:200],
                "Fix resources.limits.memory in the deployment (use at least 32Mi).",
            ],
            reason or "FailedCreatePodSandBox",
            "In simple terms: the pod was rejected before the app could start because "
            "the memory limit in the manifest is too low for the container runtime.",
        )

    if "crashloopbackoff" in reason_l or reason_l == "error":
        return (
            f"{app_label} pod keeps crashing and cannot stay running.",
            [
                f"Pod {ctx['pod_name'] or dep_name} is in {reason or 'CrashLoopBackOff'}.",
                ctx["pod_message"][:160] if ctx["pod_message"] else "Container exits immediately after start (demo crash injection).",
                "The Service has no healthy endpoints, so the app URL returns errors.",
                "Next step: Auto-fix clears the crash override and rolls back to a good deploy.",
            ],
            reason or "CrashLoopBackOff",
            f"In simple terms: the {app_label} container starts, immediately crashes, and Kubernetes keeps restarting it "
            "in a loop. No stable pod means no traffic can be served — the public URL will fail until we roll back.",
        )

    if replicas > 0 and ready == 0:
        return (
            f"{app_label} deployment exists but no pods are ready — the app is down.",
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


def _argocd_app_tree_for_app(app: dict, ctx: dict) -> dict:
    """Live Argo CD application tree for a demo app (explain panel)."""
    argo_name = app.get("argocd_app") or cfg.ARGOCD_APP
    dep_name = app.get("deployment") or cfg.DEPLOYMENT_NAME
    svc_name = dep_name if app.get("id") == "nginx" else "fastapi"
    sync_status, health_status = "Unknown", "Unknown"
    repo_url, repo_path = "", ""
    code, sh = _kubectl(
        "get", "application", argo_name, "-n", cfg.ARGOCD_NAMESPACE,
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
    pod_name = ctx.get("pod_name") or f"{dep_name}-…"
    pod_reason = (ctx.get("pod_reason") or ctx.get("pod_phase") or "Unknown").strip()
    image = ctx.get("image") or app.get("good_image") or cfg.GOOD_IMAGE

    deploy_health = "Healthy"
    if replicas == 0 or (replicas > 0 and ready == 0):
        deploy_health = "Degraded"
    elif pod_reason.lower() not in ("running", "completed", ""):
        deploy_health = "Degraded"

    pod_health = "Healthy" if pod_reason.lower() == "running" else "Degraded"
    if replicas == 0:
        pod_health = "Missing"

    svc_health = "Healthy"
    code_svc, _ = _kubectl("get", "service", svc_name, "-n", cfg.NAMESPACE)
    if code_svc != 0:
        svc_health = "Unknown"

    tree_summary = (
        f"Argo CD app «{argo_name}» is {sync_status} / {health_status}. "
        f"The failing resource is usually the Pod ({pod_reason}) under Deployment {dep_name}."
    )

    return {
        "app_name": argo_name,
        "sync_status": sync_status,
        "health_status": health_status,
        "source_repo": repo_short,
        "source_path": repo_path or (
            "demos/nginx-staging/overlays/oci" if app.get("id") == "nginx" else "overlays/oci"
        ),
        "destination_namespace": dest_ns,
        "tree_summary": tree_summary,
        "resources": [
            {
                "kind": "Application",
                "name": argo_name,
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
                "name": dep_name,
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
                "name": svc_name,
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
        "You are Cluster Command Deck — a workload-agnostic Kubernetes assistant on enlight-staging.\n"
        "You run inside a Python backend (selfheal-ui) that ALREADY executes real kubectl against the live "
        "cluster when users ask to simulate outages, deploy, auto-fix, reset, or show status. "
        "Those actions run server-side in the pod — no MCP or user terminal required.\n"
        "Treat FastAPI, Nginx, and any service the user names with equal priority — never assume FastAPI.\n"
        "Map typos (outrage→outage, ngnix→nginx, auto fiz→auto-fix) before answering.\n"
        "For errors: explain root cause, show realistic kubectl/bash in code blocks, suggest remediation.\n"
        "If asked whether you can run commands: yes — via this backend, not from the browser directly.\n"
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


# --- Chat-first actions (natural language deploy / outage / heal) ---

def _app_pod_summary(app: dict) -> str:
    if not _app_workloads_exist(app):
        return "not deployed"
    return _pod_display_line(app["pod_label"])


def _app_injected_modes(app: dict) -> list[str]:
    dep = app["deployment"]
    code, ann = _kubectl(
        "get", "deployment", dep, "-n", cfg.NAMESPACE,
        "-o", "jsonpath={.metadata.annotations.enlight-lab/injected-modes}",
    )
    if code != 0 or not ann.strip():
        return []
    return [m.strip() for m in ann.split(",") if m.strip()]


def _app_has_active_injection(app: dict) -> bool:
    return bool(_app_injected_modes(app))


def _app_workloads_exist(app: dict) -> bool:
    code, _ = _kubectl("get", "deployment", app["deployment"], "-n", cfg.NAMESPACE)
    return code == 0


def _app_is_healthy(app: dict) -> bool:
    if _app_has_active_injection(app):
        return False
    if not _app_workloads_exist(app):
        return False
    detail = _pod_detail_for_label(app["pod_label"])
    line = detail.get("line", "")
    if not line or line == "no pods":
        return False
    if "1/1" not in line:
        return False
    if detail.get("container_errors"):
        return False
    if "Running" not in line and "Succeeded" not in line:
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


def _strip_markdown_links(text: str) -> str:
    """Remove inline markdown links — UI renders link buttons on status cards instead."""
    cleaned = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", "", text or "")
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _apps_status_for_target(target: str) -> list[dict]:
    rows = _apps_status_data()
    if target == "all":
        return rows
    return [r for r in rows if r["id"] == target]


def _inject_summary_item(app_id: str, modes: list[str], pod_line: str, *, healthy: bool = False) -> dict:
    app = cfg.demo_app(app_id)
    injected = _app_has_active_injection(app)
    expected = expected_signals_for_modes(modes)
    detail = _pod_detail_for_label(app["pod_label"])
    # Injection stamp is source of truth — outage is active even when pod stays Running (network chaos).
    outage_active = injected or bool(modes)
    service_level = any(m in SERVICE_LEVEL_MODE_IDS for m in modes)
    return {
        "app": app_id,
        "label": app["label"],
        "modes": inject_mode_chips(modes),
        "expected_signals": expected,
        "expected_summary": describe_expected_failure_plain(modes),
        "service_level": service_level,
        "links": _app_browser_links(app_id),
        "pod_line": detail["line"] or pod_line,
        "container_errors": detail.get("container_errors") or [],
        "healthy": False if outage_active else healthy,
        "injected": outage_active,
    }


def _action_result_payload(
    action: str,
    target: str,
    message: str,
    *,
    include_cards: bool = False,
    links: dict | None = None,
    heal_summary: list[dict] | None = None,
    inject_summary: list[dict] | None = None,
    timeline: list[dict] | None = None,
) -> dict:
    """Chat action response — status cards only when user asked for status/links."""
    payload: dict = {
        "action": action,
        "target": target,
        "action_target": target,
        "message": _strip_markdown_links(message),
        "links": links or resolved_public_app_links(),
    }
    if heal_summary:
        payload["heal_summary"] = heal_summary
        payload["ui"] = "heal_summary"
    if inject_summary:
        payload["inject_summary"] = inject_summary
        payload["ui"] = "inject_summary"
    if timeline:
        payload["timeline"] = timeline
    if include_cards:
        payload["apps_status"] = _apps_status_for_target(target)
        if not heal_summary and not inject_summary:
            payload["ui"] = "status_cards"
    return payload


def _apply_staging_nginx_manifests() -> tuple[int, str]:
    """Apply nginx workload YAML from the image/overlay path (same as apply-nginx-staging.sh)."""
    staging = cfg.STAGING_NGINX_PATH
    if staging.is_dir():
        return _kubectl("apply", "-f", str(staging))
    return 0, "staging nginx path not mounted — relying on Argo CD sync only"


def _deploy_nginx_app() -> dict:
    app = cfg.demo_app("nginx")
    manifest = app.get("argocd_manifest")
    if not manifest:
        raise RuntimeError("Nginx Argo CD manifest is not configured")

    apply_code, apply_out = _apply_staging_nginx_manifests()
    if apply_code != 0:
        raise RuntimeError(f"Failed to apply Nginx workloads: {apply_out}")

    code, out = _register_argocd_app_manifest(
        manifest, yaml_text=cfg.NGINX_ARGOCD_APP_YAML,
    )
    if code != 0:
        raise RuntimeError(f"Failed to register Nginx in Argo CD: {out}")
    _wait_argocd_app_status_named(app["argocd_app"], timeout=90, want_health="")
    _argocd_set_automated_named(app["argocd_app"], True)
    _argocd_refresh_named(app["argocd_app"])
    sync_code, sync_out = _argocd_trigger_sync_named(app["argocd_app"])
    if sync_code != 0:
        log.warning("Nginx Argo CD sync trigger failed (workloads may still be up): %s", sync_out)
    else:
        _argocd_wait_synced_named(app["argocd_app"], timeout=90)

    _kubectl(
        "set", "image",
        f"deployment/{app['deployment']}",
        f"{app['container']}={app['good_image']}",
        "-n", cfg.NAMESPACE,
    )
    _kubectl(
        "patch", "deployment", app["deployment"], "-n", cfg.NAMESPACE,
        "--type=json",
        "-p", '[{"op":"replace","path":"/spec/template/spec/containers/0/imagePullPolicy","value":"Always"}]',
    )
    deadline = time.time() + 90
    while time.time() < deadline:
        if _app_is_healthy(app):
            break
        time.sleep(3)
    healthy = _app_is_healthy(app)
    links = _app_browser_links("nginx")
    return {
        "app": "nginx",
        "message": (
            f"**{app['label']}** is live in `{cfg.NAMESPACE}` — pod `{_app_pod_summary(app)}`."
            if healthy
            else f"**{app['label']}** deployed but not healthy yet — `{_app_pod_summary(app)}`."
        ),
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


def _simulate_app_error_impl(
    app_id: str,
    message: str = "",
    *,
    mode: str | list[str] | None = None,
) -> dict:
    """Inject demo-safe failure(s) on any registered workload."""
    app = cfg.demo_app(app_id)
    if isinstance(mode, list):
        modes = [m.lower() for m in mode if m]
    elif mode:
        modes = [mode.lower()]
    else:
        modes = classify_failure_modes(message)
    modes = [m if m in {x.id for x in FAILURE_MODES} else classify_failure_mode(m) for m in modes]
    modes = list(dict.fromkeys(modes))[:4]
    dep = app["deployment"]
    mode_label = ", ".join(failure_mode_label(m) for m in modes)

    timeline: list[dict[str, str]] = []
    ok, cluster_msg = _cluster_reachable()
    if not ok:
        raise RuntimeError(cluster_msg)

    _timeline_step(timeline, "Clearing prior demo injections", dep, phase="break")
    clear_all_failure_injections(app)

    _timeline_step(
        timeline,
        f"Preparing {mode_label} simulation for {app['label']}",
        f"{len(modes)} failure mode(s) — staging only",
        phase="break",
    )
    _pause_gitops_for_injection(app, timeline)

    if not _app_workloads_exist(app):
        raise RuntimeError(
            f"**{app['label']}** is not deployed — run **deploy {app_id}** first, then simulate again."
        )

    if "instant" not in modes:
        _kubectl_must(
            "scale", f"deployment/{dep}",
            "-n", cfg.NAMESPACE, "--replicas=1",
            action=f"Ensure {dep} is running before injection",
        )

    kubectl_log: list[str] = []
    for m in modes:
        label = failure_mode_label(m)
        if m == "instant":
            _timeline_step(timeline, "Scaling deployment to zero replicas", dep, phase="break")
        else:
            _timeline_step(timeline, f"Injecting {label}", dep, phase="break")
    kubectl_log.extend(inject_failure_modes(modes, app))
    argo_app = app.get("argocd_app") or ""
    if app.get("gitops") and argo_app:
        _argocd_refresh_named(argo_app)

    _timeline_step(
        timeline,
        "Waiting for failure signals",
        f"Expect {mode_label} — Argo CD should show Degraded",
        phase="break",
    )
    deadline = time.time() + (8 if modes == ["instant"] else 60)
    while time.time() < deadline:
        if modes == ["instant"]:
            code, rep = _kubectl(
                "get", "deployment", dep, "-n", cfg.NAMESPACE,
                "-o", "jsonpath={.spec.replicas}",
            )
            if code == 0 and rep.strip() == "0":
                break
        elif _app_has_active_injection(app):
            detail = _pod_detail_for_label(app["pod_label"])
            line = detail.get("line", "")
            if detail.get("container_errors") or _pod_ready_not_full(line):
                break
            if not _app_is_healthy(app):
                break
            argo = _argocd_status_for_app(app)
            if argo and not _argocd_is_synced_healthy(argo) and "Degraded" in argo:
                break
            if argo and ("Progressing" in argo or "Unhealthy" in argo):
                break
        elif not _app_is_healthy(app):
            break
        time.sleep(3)

    pod_line = _app_pod_summary(app)
    _timeline_step(timeline, "Failure active", pod_line, phase="break", pause=False)

    klog = "\n".join(kubectl_log)
    expected_desc = describe_expected_failure(modes)
    inject_item = _inject_summary_item(app_id, modes, pod_line)
    return {
        "app": app_id,
        "mode": modes[0] if len(modes) == 1 else modes,
        "modes": modes,
        "message": (
            f"**{mode_label} active on {app['label']}** — failure injected.\n\n"
            f"{expected_desc}\n\n"
            f"**Argo CD:** expect **Degraded** or **Progressing** (not Healthy) within ~30s.\n\n"
            f"**Observed pod state:** `{pod_line}`\n\n"
            f"Say **auto-fix {app_id}** to remediate, or ask **what broke?** for diagnosis."
        ),
        "expected_signals": expected_signals_for_modes(modes),
        "pod_line": pod_line,
        "healthy": False,
        "inject_summary": [inject_item],
        "links": _app_browser_links(app_id),
        "timeline": timeline,
        "kubectl_log": klog,
    }


def _simulate_app_outage_impl(app_id: str, message: str = "") -> dict:
    return _simulate_app_error_impl(app_id, message)


def simulate_app_outage(app_id: str, on_step: StepCallback = None, message: str = "") -> dict:
    if not (message or "").strip():
        return _with_step_stream(on_step, lambda: _simulate_guided_outage_impl(app_id))
    return _with_step_stream(on_step, lambda: _simulate_app_error_impl(app_id, message))


def inject_outage_plain_english(
    message: str,
    *,
    app_id: str = "fastapi",
    scenario_id: str | None = None,
) -> dict:
    """Plain-English outage API — for MCP, scripts, and automations."""
    app_key = (app_id or "fastapi").strip().lower()
    if app_key in ("both", "all"):
        _pause_gitops_for_demo_apps()
        results = []
        for aid in ("fastapi", "nginx"):
            results.append(inject_outage_plain_english(message, app_id=aid, scenario_id=scenario_id))
        return {
            "ok": True,
            "apps": results,
            "message": "**Outage applied** to FastAPI and Nginx.",
        }

    if scenario_id:
        sc = scenario_by_id(scenario_id)
        if sc:
            message = sc.prompt
            if app_key not in sc.apps and len(sc.apps) == 1:
                app_key = sc.apps[0]
            r = _simulate_app_error_impl(app_key, message=message, mode=list(sc.modes))
            r["scenario"] = sc.id
            r["ok"] = True
            return r

    r = _simulate_app_error_impl(app_key, message=message)
    r["ok"] = True
    return r


def _heal_apply_path_for_app(app: dict) -> Path | None:
    """Bundled kustomize overlay / manifest dir used to reset a demo workload."""
    app_id = app.get("id") or ""
    if app_id == "fastapi":
        overlay = cfg.HEAL_OVERLAY_PATH
        if overlay.is_dir() and (overlay / "kustomization.yaml").is_file():
            return overlay
        if cfg.STAGING_APP_PATH.is_dir():
            return cfg.STAGING_APP_PATH
        return None
    if app_id == "nginx":
        nginx_path = app.get("manifest_path") or cfg.STAGING_NGINX_PATH
        return nginx_path if nginx_path.is_dir() else None
    return None


def _apply_heal_manifests(app: dict, timeline: list[dict[str, str]], *, fast: bool = False) -> None:
    """Replace the broken Deployment with the known-good bundled manifests (Recreate strategy)."""
    path = _heal_apply_path_for_app(app)
    if not path:
        return
    dep = app["deployment"]
    _timeline_step(
        timeline,
        "Removing broken deployment",
        f"{dep} — clean slate before re-apply",
        pause=not fast,
    )
    _kubectl(
        "delete", "deployment", dep, "-n", cfg.NAMESPACE,
        "--wait=false", "--ignore-not-found",
    )
    label = f"kustomize {path}" if (path / "kustomization.yaml").is_file() else str(path)
    _timeline_step(timeline, "Applying known-good manifests", label, pause=not fast)
    if (path / "kustomization.yaml").is_file():
        code, out = _run(_kubectl_cmd("apply", "-k", str(path)), timeout=120)
    else:
        code, out = _run(_kubectl_cmd("apply", "-f", str(path)), timeout=120)
    if code != 0:
        raise RuntimeError(_kubectl_value(code, out, "Heal manifest apply failed"))


def _heal_deployment_for_app(app: dict, timeline: list[dict[str, str]], *, fast: bool = False) -> bool:
    """Restore a demo deployment to its known-good spec and wait for a healthy pod."""
    dep = app["deployment"]
    argo_app = app.get("argocd_app") or ""
    good_image = app.get("good_image") or cfg.GOOD_IMAGE

    if app.get("gitops") and argo_app:
        _timeline_step(
            timeline,
            "Ensuring Argo CD auto-sync is paused",
            f"{argo_app} — GitOps won't fight the fix",
            pause=not fast,
        )
        _pause_gitops_for_injection(app)

    _apply_heal_manifests(app, timeline, fast=fast)

    _timeline_step(timeline, "Clearing failure injections", dep)
    clear_all_failure_injections(app)
    if app.get("id") == "fastapi":
        _clear_crash_override()

    _timeline_step(timeline, "Scaling deployment to 1 replica", cfg.NAMESPACE)
    _kubectl_must(
        "scale", f"deployment/{dep}",
        "-n", cfg.NAMESPACE, "--replicas=1",
        action="Scale up",
    )

    _timeline_step(timeline, "Restoring known-good container image", good_image)
    _kubectl_must(
        "set", "image",
        f"deployment/{dep}",
        f"{app['container']}={good_image}",
        "-n", cfg.NAMESPACE,
        action="Restore good image",
    )
    _kubectl(
        "patch", f"deployment/{dep}", "-n", cfg.NAMESPACE,
        "--type=json",
        "-p", '[{"op":"replace","path":"/spec/template/spec/containers/0/imagePullPolicy","value":"Always"}]',
    )

    _timeline_step(timeline, "Recycling pods", "Force delete so the good image applies immediately")
    _kubectl(
        "delete", "pods", "-n", cfg.NAMESPACE, "-l", app["pod_label"],
        "--wait=false", "--force", "--grace-period=0",
    )

    pod_timeout = 90 if fast else 120
    ready = _wait_pod_ready_with_steps(
        timeline,
        pod_label=app["pod_label"],
        timeout=pod_timeout,
        title="Waiting for healthy pod",
    )
    if not ready:
        _timeline_step(timeline, "Re-applying heal manifests", "Pod still unhealthy — full reset retry")
        _apply_heal_manifests(app, timeline, fast=fast)
        _kubectl_must(
            "set", "image",
            f"deployment/{dep}",
            f"{app['container']}={good_image}",
            "-n", cfg.NAMESPACE,
            action="Restore good image",
        )
        _kubectl(
            "delete", "pods", "-n", cfg.NAMESPACE, "-l", app["pod_label"],
            "--wait=false", "--force", "--grace-period=0",
        )
        ready = _wait_pod_ready_with_steps(
            timeline,
            pod_label=app["pod_label"],
            timeout=60,
            title="Retrying pod readiness",
        )
    return ready


def _argocd_finalize_heal(app: dict, timeline: list[dict[str, str]], *, fast: bool = False) -> str:
    """Re-enable GitOps sync after a manual heal."""
    if not app.get("gitops") or not app.get("argocd_app"):
        return ""
    argo_note = ""
    _timeline_step(timeline, "Re-enabling Argo CD auto-sync", "GitOps policy restored from manifest")
    restore_code, restore_out = _argocd_restore_for_app(app)
    if restore_code != 0:
        argo_note += f" (ArgoCD restore skipped: {(restore_out or '')[:100]})"
    _argocd_refresh()
    _timeline_step(timeline, "Triggering Argo CD sync", "Cluster state → Git → Healthy")
    sync_code, sync_out = _argocd_trigger_sync_named(app["argocd_app"])
    if sync_code != 0:
        argo_note += f" (ArgoCD sync skipped: {(sync_out or '')[:80]})"
    else:
        argo = _argocd_wait_synced_named(app["argocd_app"], timeout=90 if fast else 120)
        if not _argocd_is_synced_healthy(argo):
            _argocd_trigger_sync_named(app["argocd_app"])
            argo = _argocd_wait_synced_named(app["argocd_app"], timeout=60)
        if not _argocd_is_synced_healthy(argo):
            argo_note += f" (GitOps: {argo} — refresh Argo CD in ~30s)"
    return argo_note


def _auto_fix_app_impl(app_id: str, *, fast: bool = False) -> dict:
    if app_id == "fastapi":
        return _auto_fix_impl(fast=fast)
    app = cfg.demo_app(app_id)
    timeline: list[dict[str, str]] = []
    ok, cluster_msg = _cluster_reachable()
    if not ok:
        raise RuntimeError(cluster_msg)

    _timeline_step(timeline, "Starting recovery", f"Restoring {app['label']}")
    ready = _heal_deployment_for_app(app, timeline, fast=fast)
    argo_note = _argocd_finalize_heal(app, timeline, fast=fast)
    healthy = ready and _app_is_healthy(app)
    summary = _format_heal_app_summary(app_id, {"healthy": healthy, "app_reachable": healthy})
    _timeline_step(
        timeline,
        "Recovery complete" if healthy else "Recovery applied — verify status",
        summary["pod_line"],
        pause=False,
    )
    return {
        "app": app_id,
        "timeline": timeline,
        "message": _format_heal_all_message([summary]),
        "heal_summary": [summary],
        "healthy": healthy,
        "app_reachable": healthy,
        "pod_line": summary["pod_line"],
        "links": _app_browser_links(app_id),
        "open_url": _app_browser_links(app_id).get("dashboard"),
        "staging_url": _app_browser_links(app_id).get("dashboard"),
    }


def auto_fix_app(app_id: str, on_step: StepCallback = None, *, fast: bool = False) -> dict:
    return _with_step_stream(on_step, lambda: _auto_fix_app_impl(app_id, fast=fast))


def _infer_target_from_history(history: list | None) -> str | None:
    """Resolve pronouns (it, them, both) from recent chat context."""
    if not history:
        return None
    for h in reversed(history[-8:]):
        c = str(h.get("content") or "").lower()
        has_fastapi = bool(re.search(r"\b(fastapi|fast api|fastapi-\w+)\b", c))
        has_nginx = bool(re.search(r"\b(nginx|nginx web|nginx-demo)\b", c))
        if has_fastapi and has_nginx:
            continue
        if has_nginx:
            return "nginx"
        if has_fastapi:
            return "fastapi"
    blob = " ".join(str(h.get("content") or "") for h in history[-8:]).lower()
    has_fastapi = bool(re.search(r"\bfastapi\b", blob))
    has_nginx = bool(re.search(r"\bnginx\b", blob))
    if has_fastapi and has_nginx:
        return "all"
    if has_nginx:
        return "nginx"
    if has_fastapi:
        return "fastapi"
    if re.search(r"\b(both apps|two apps|all apps|demo apps)\b", blob):
        return "all"
    return None


_DEPLOY_SKIP_TOKENS = frozenset({
    "a", "an", "the", "my", "our", "new", "some", "app", "apps", "application",
    "applications", "workload", "workloads", "service", "both", "all", "them",
    "demo", "staging", "cluster",
})

_KNOWN_WORKLOAD_TOKENS = frozenset({
    "fastapi", "fast", "api", "fastpi", "python", "nginx", "ngnix", "ngix", "ninx",
    "web", "frontend", "both", "all", "apps", "applications",
})


def _unsupported_workload_token(message: str) -> str | None:
    """Return an unsupported workload name when the user names a specific app we don't manage."""
    q = _normalize_query(message)
    if not re.search(r"\b(deploy|bring up|launch|install|register|create)\b", q):
        return None
    m = re.search(
        r"\b(?:deploy|bring up|launch|install|register|create)\w*\s+"
        r"(?:the\s+)?(?:a\s+)?(?:my\s+)?([a-z][a-z0-9_-]*)",
        q,
    )
    if not m:
        return None
    token = m.group(1)
    if token in _DEPLOY_SKIP_TOKENS:
        return None
    if token in _KNOWN_WORKLOAD_TOKENS or token.startswith(("fastapi", "nginx")):
        return None
    return token


def _unsupported_workload_reply(token: str) -> str:
    labels = " and ".join(a["label"] for a in cfg.demo_apps().values())
    return (
        f"**{token.title()}** is not available for deployment in this environment.\n\n"
        f"Supported workloads are **{labels}** (Argo CD in `{cfg.NAMESPACE}`).\n\n"
        "Try **deploy fastapi**, **deploy nginx**, or **deploy both apps**."
    )


def _is_dangerous_operation(message: str) -> bool:
    q = _normalize_query(message)
    if re.search(r"\bdelete\s+the\s+cluster\b", q):
        return True
    if re.search(r"\b(delete|destroy|wipe|nuke)\b.{0,30}\b(cluster|control plane|etcd)\b", q):
        return True
    if re.search(r"\b(delete|destroy|remove)\b.{0,20}\b(every|all)\s+namespaces?\b", q):
        return True
    return False


def _dangerous_operation_reply() -> str:
    return (
        "I **cannot delete the cluster or all namespaces** — that would be destructive "
        "and is blocked in this demo.\n\n"
        f"I only manage **FastAPI** and **Nginx Web** in namespace `{cfg.NAMESPACE}` on Oracle OKE.\n\n"
        "To tear down a demo app safely, say **reset fastapi** or **reset nginx**."
    )


def _has_conflicting_instructions(message: str) -> bool:
    q = _normalize_query(message)
    if re.search(r"\bdeploy\b", q) and re.search(r"\b(don'?t|do not|never)\s+deploy\b", q):
        return True
    pairs = (("fastapi", "fastapi"), ("nginx", "nginx"))
    for verb, app in pairs:
        if (
            re.search(rf"\bdeploy\b.{{0,40}}\b{app}\b", q)
            and re.search(rf"\b(don'?t|do not|never)\s+deploy\b.{{0,40}}\b{app}\b", q)
        ):
            return True
    return False


def _conflicting_instructions_reply() -> str:
    return (
        "Your message has **conflicting instructions** (for example, deploy and don't deploy "
        "the same app).\n\n"
        "Please clarify — pick one:\n"
        "- **deploy fastapi** — register FastAPI in Argo CD\n"
        "- **reset fastapi** — tear down FastAPI only\n"
        "- **deploy nginx** — deploy only Nginx"
    )


def _impossible_deploy_destination(message: str) -> str | None:
    q = _normalize_query(message)
    if not re.search(r"\b(deploy|bring up|launch|install)\b", q):
        return None
    m = re.search(r"\b(to|on|into)\s+(aws|amazon|azure|gcp|google cloud|production|prod)\b", q)
    if m:
        return m.group(2)
    if re.search(r"\baws\b", q) and re.search(r"\bdeploy\b", q):
        return "aws"
    return None


def _impossible_deploy_reply(dest: str) -> str:
    label = dest.upper().replace("GOOGLE CLOUD", "GCP")
    return (
        f"I **cannot deploy to {label}** from this chatbot.\n\n"
        f"This environment only deploys **FastAPI** and **Nginx Web** to **Oracle OKE** "
        f"namespace `{cfg.NAMESPACE}` via Argo CD.\n\n"
        "Try **deploy fastapi** or **deploy nginx** for the staging demo."
    )


def _try_compound_deploy_break(message: str) -> tuple[str, str] | None:
    """Parse 'deploy X and break Y' when X and Y differ."""
    q = _normalize_query(message)
    if not re.search(r"\bdeploy\b", q) or not re.search(r"\b(break|inject|simulate|cause)\b", q):
        return None
    deploy_t = "fastapi" if re.search(r"\bdeploy\b.{0,40}\bfastapi\b", q) else (
        "nginx" if re.search(r"\bdeploy\b.{0,40}\bnginx\b", q) else None
    )
    break_t = "nginx" if re.search(r"\b(break|inject|simulate|cause)\b.{0,40}\bnginx\b", q) else (
        "fastapi" if re.search(r"\b(break|inject|simulate|cause)\b.{0,40}\bfastapi\b", q) else None
    )
    if deploy_t and break_t and deploy_t != break_t:
        return deploy_t, break_t
    return None


def _is_diagnosis_question(message: str) -> bool:
    if _wants_layman_explain(message) or _is_root_cause_question(message):
        return True
    q = _intent_query(message)
    return bool(re.search(
        r"\b(why (is|isn'?t|are|aren'?t)|not working|what caused|caused (this )?outage|"
        r"service (is )?down|outage on)\b",
        q,
    ))


def _try_scoped_diagnosis_reply(message: str, history: list | None) -> str | None:
    """Plain-English RCA scoped to one demo app — avoids 40-mode catalog and cross-app noise."""
    if not _is_diagnosis_question(message):
        return None
    target = _resolve_app_target(message, history) or _infer_target_from_history(history)
    if not target or target == "all":
        return None
    app = cfg.demo_app(target)
    ctx = _incident_context_for_app(app)
    tree = _argocd_app_tree_for_app(app, ctx)
    lang = _resolved_language(message, history)
    return _demo_rca_fallback_reply(ctx, tree, lang)


def _wants_repeat_same_outage(message: str) -> bool:
    q = _normalize_query(message)
    return bool(re.search(
        r"\b(same\s+(outage|failure|error|scenario|issue)|inject\s+(the\s+)?same|"
        r"repeat\s+(the\s+)?(same\s+)?(outage|failure)|outage\s+again)\b",
        q,
    )) or bool(re.search(r"\b(again|one more)\b", q) and re.search(r"\b(inject|outage|failure)\b", q))


def _last_failure_injection_from_history(
    history: list | None,
) -> tuple[str | None, list[str] | None]:
    """Recover workload + failure modes from the most recent inject command in chat."""
    if not history:
        return None, None
    last_target: str | None = None
    last_modes: list[str] | None = None
    for h in reversed(history[-12:]):
        if h.get("role") != "user":
            continue
        c = str(h.get("content") or "")
        q = _normalize_query(c)
        if not re.search(r"\b(simulat\w*|stimulat\w*|inject|trigger|cause|break)\b", q):
            continue
        if re.search(r"\b(same\s+outage|inject\s+the\s+same)\b", q):
            continue
        modes = classify_failure_modes(c)
        target = _resolve_app_target(c, history)
        if modes:
            last_modes = modes
        if target:
            last_target = target
        if last_modes:
            break
    return last_target, last_modes


def _is_deployable_apps_question(message: str) -> bool:
    q = _normalize_query(message)
    return bool(re.search(
        r"which (applications?|apps?|workloads?).*(can you )?(deploy|bring up|launch)",
        q,
    )) or bool(re.search(r"what (apps?|applications?|workloads?) can you deploy", q))


def _scoped_apps_status(rows: list[dict] | None, target: str | None) -> list[dict] | None:
    """When an action targets one app, never return the other app's status card."""
    if not rows or not target or target == "all":
        return rows
    scoped = [r for r in rows if r.get("id") == target]
    return scoped or rows


def _deployable_apps_reply() -> str:
    lines = [f"I can deploy these workloads in namespace `{cfg.NAMESPACE}`:\n"]
    for app in cfg.demo_apps().values():
        lines.append(f"- **{app['label']}** — {app['blurb']}")
    lines.append(
        "\nSay **deploy fastapi**, **deploy nginx**, or **deploy both apps**."
    )
    return "\n".join(lines)


def _try_curated_info_reply(message: str) -> str | None:
    """Deterministic answers for common cluster/app questions (consistent bullet formatting)."""
    q = _normalize_query(message)
    if _is_deployable_apps_question(message):
        return _deployable_apps_reply()
    if re.search(r"which (application|app).*(api doc|swagger|openapi|/docs)", q):
        return (
            "**FastAPI** exposes API docs — open the FastAPI app and use `/docs` (Swagger UI) "
            "or `/redoc`.\n\n"
            "**Nginx Web** is the static frontend demo (no API docs)."
        )
    if re.search(r"which (application|app).*(frontend|front end|web ui|web front)", q):
        return (
            "**Nginx Web** is the frontend / static web demo workload.\n\n"
            "**FastAPI** is the Python API backend (GitOps via Argo CD)."
        )
    if re.search(r"what policies.*(protect|deployment)|policies protect deployment", q):
        return (
            f"Deployments in **`{cfg.NAMESPACE}`** are protected by:\n\n"
            "- **Argo CD GitOps** — desired state comes from Git; drift is reconciled or blocked.\n"
            "- **Kubernetes RBAC** — only authorized operators can mutate workloads.\n"
            "- **Staging guardrails** — demo chaos and auto-fix are scoped to registered demo apps only.\n\n"
            "I can only deploy **FastAPI** and **Nginx Web** in this environment."
        )
    if re.search(r"which workload.*(oracle oke|oke|oci)|running on oracle oke", q):
        labels = ", ".join(a["label"] for a in cfg.demo_apps().values())
        return (
            f"Both demo workloads (**{labels}**) run on **Oracle OKE** in namespace "
            f"`{cfg.NAMESPACE}`.\n\n"
            "- **FastAPI** — `app=fastapi` deployment\n"
            "- **Nginx Web** — `app=nginx-demo` deployment"
        )
    return None


def _try_failure_catalog_reply(message: str) -> tuple[str, dict] | None:
    from failure_modes import (
        failure_modes_catalog_data,
        failure_modes_catalog_reply,
        is_failure_catalog_request,
    )
    if not is_failure_catalog_request(message):
        return None
    return failure_modes_catalog_reply(), failure_modes_catalog_data()


def _demo_health_phrase(healthy: int, total: int, *, already: bool = False) -> str:
    """Consistent healthy-count wording (QA: '1 of 1 demo applications are healthy')."""
    if already:
        if total == 1:
            return "**The demo application is already healthy**"
        return f"**All demo apps are already healthy** ({healthy}/{total})"
    if total == 1:
        return f"**{healthy} of 1 demo applications are healthy**"
    if healthy == total:
        return f"**All {total} demo apps are healthy**"
    return f"**{healthy} of {total} demo applications are healthy**"


def _llm_resolve_target(message: str, history: list | None = None) -> str | None:
    """Use Gemini to infer workload when regex and history heuristics are ambiguous."""
    if not cfg.CHAT_LLM_TARGET or not _gemini_key_configured():
        return None
    model_full = cfg.resolved_holmes_model()
    if not model_full.startswith("gemini/"):
        return None
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    model_id = model_full.replace("gemini/", "", 1)
    history_block = _format_history_block(history)
    prompt = (
        f"{history_block}"
        "You route Kubernetes chat commands for a staging cluster with exactly two demo workloads: "
        "fastapi (FastAPI API) and nginx (Nginx Web).\n"
        f"User message: {message}\n\n"
        "Which workload does the user mean? Consider typos (outrage=outage, stimulate=simulate). "
        "Pronouns it/this/that refer to the workload from recent messages.\n"
        "Reply with exactly one word: fastapi, nginx, both, or unknown."
    )
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model_id}:generateContent?key={key}"
    )
    payload = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 16},
    }).encode("utf-8")
    try:
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        parts = ((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
        text = " ".join(str(p.get("text", "")) for p in parts).strip().lower()
        token = re.sub(r"[^a-z]", "", text.split()[0] if text.split() else text)
        if token in ("fastapi", "nginx"):
            return token
        if token == "both":
            return "all"
    except Exception as exc:
        log.debug("LLM target resolution failed: %s", exc)
    return None


def _resolve_action_target(
    act_type: str,
    message: str,
    history: list | None,
    regex_target: str | None,
) -> str | None:
    """Pick workload for a mutating action — regex, then history, then Gemini."""
    if regex_target:
        if act_type == "outage" and regex_target == "all" and not _user_wants_both_apps(message):
            return None
        return regex_target
    if act_type == "deploy" and _unsupported_workload_token(message):
        return None
    if act_type not in ("deploy", "reset", "outage", "heal", "explain"):
        return None
    if act_type == "outage":
        if _user_wants_both_apps(message):
            return "all"
        explicit = _resolve_app_target(message, history)
        if explicit and explicit != "all":
            return explicit
        from_history = _infer_target_from_history(history)
        if from_history and from_history != "all":
            return from_history
        return None
    from_history = _infer_target_from_history(history)
    if from_history:
        return from_history
    return _llm_resolve_target(message, history)


def _resolve_app_target(message: str, history: list | None = None) -> str | None:
    q = _normalize_query(message)
    if re.search(
        r"\b(both|all apps|all applications|everything|each app|both of them|all of them|"
        r"all my apps|my apps|the two apps|two apps|them both)\b",
        q,
    ):
        return "all"
    if re.search(r"\b(nginx|ngnix|ngix|ninx|web front|frontend|web app)\b", q):
        return "nginx"
    if re.search(r"\b(fastapi|fastpi|fast api|fastapitapi|python api|api app)\b", q):
        return "fastapi"
    if re.search(r"\b(them|they|those|it|this|that)\b", q):
        return _infer_target_from_history(history)
    if re.search(r"\b(another|one more|again|same)\b", q):
        return _infer_target_from_history(history)
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
    """Structured status for rich chat cards — respects active chaos injection stamp."""
    rows = []
    for app_id, app in cfg.demo_apps().items():
        exists = _app_workloads_exist(app)
        injected_modes = _app_injected_modes(app) if exists else []
        injected = bool(injected_modes)
        detail = _pod_detail_for_label(app["pod_label"]) if exists else {"line": "not deployed", "container_errors": []}
        pod = _pod_line_with_injection(app, detail["line"]) if exists else detail["line"]
        gitops = _argocd_status_for_app(app) if exists else ""
        links = _app_browser_links(app_id)

        if not exists:
            state, state_key, healthy = "Not deployed", "idle", False
        elif injected:
            state, state_key, healthy = "Outage active", "bad", False
        elif _app_is_healthy(app):
            state, state_key, healthy = "Healthy", "ok", True
        elif _app_heal_phase(app) == "recovering":
            state, state_key, healthy = "Recovering", "warn", False
        else:
            state, state_key, healthy = "Unhealthy", "bad", False

        rows.append({
            "id": app_id,
            "label": app["label"],
            "blurb": app["blurb"],
            "state": state,
            "state_key": state_key,
            "pod_line": pod,
            "container_errors": detail.get("container_errors") or [],
            "deployed": exists,
            "healthy": healthy,
            "injected": injected,
            "injected_modes": injected_modes,
            "injected_mode_labels": [failure_mode_label(m) for m in injected_modes],
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


def _cluster_greeting_reply(message: str = "") -> str:
    """Greeting that lists every demo app (FastAPI + Nginx) from live cluster state."""
    lang = _requested_language(message)
    rows = _apps_status_data()
    n = len(rows)
    healthy = sum(1 for r in rows if r["healthy"])
    if lang == "fr":
        lines = [
            f"Bonjour ! Je suis votre **agent Kubernetes** sur **{cfg.NAMESPACE}** — "
            f"**{n} applications démo** (FastAPI + Nginx).\n",
            f"**En bonne santé :** {healthy}/{n}\n",
        ]
        for r in rows:
            gitops = f" · GitOps `{r['gitops']}`" if r.get("gitops") else ""
            lines.append(f"- **{r['label']}** — {r['state']} · `{r['pod_line']}`{gitops}")
        lines.append("\nDemandez **pod status**, **combien d'apps**, **deploy nginx**, ou **auto-fix**.")
        return "\n".join(lines)
    lines = [
        f"Hello! I'm your **Kubernetes agent** on **{cfg.NAMESPACE}** — "
        f"**{n} demo apps** (**FastAPI** + **Nginx**).\n",
        f"**Healthy:** {healthy}/{n}\n",
    ]
    for r in rows:
        gitops = f" · GitOps `{r['gitops']}`" if r.get("gitops") else ""
        lines.append(f"- **{r['label']}** — {r['state']} · `{r['pod_line']}`{gitops}")
    lines.append("\nAsk **pod status**, **how many apps**, **deploy nginx**, or **auto-fix**.")
    return "\n".join(lines)


def _is_inject_or_outage_intent(message: str) -> bool:
    """True when the user wants to simulate/inject a failure (not check status)."""
    q = _normalize_query(message)
    if re.search(r"\b(simulat\w*|stimulat\w*|inject|trigger|cause|break)\b", q):
        return True
    if re.search(
        r"\b(crash\s*loop|crashloop|oom|probe|outage|failure|chaos|image\s*pull|"
        r"network\s*policy|pending|readiness|liveness|startup)\b",
        q,
    ) and re.search(r"\b(simulat\w*|stimulat\w*|inject|cause|break)\b", q):
        return True
    return False


def _user_wants_both_apps(message: str) -> bool:
    q = _normalize_query(message)
    return bool(re.search(
        r"\b(both|all apps|all applications|everything|each app|both of them|"
        r"all of them|all my apps|my apps|the two apps|two apps|them both)\b",
        q,
    ))


def _needs_status_disambiguation(message: str) -> bool:
    q = _normalize_query(message)
    if _resolve_app_target(message):
        return False
    if _is_inject_or_outage_intent(message):
        return False
    return bool(re.search(
        r"\b(pod status|show status|show pods|pod details|apps status|cluster status|"
        r"is (my |the )?app healthy)\b",
        q,
    ))


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


def _outage_target_disambiguation(message: str) -> tuple[str, list[dict[str, str]]]:
    """App picker for failure injection — preserves the failure type in each choice."""
    q = _normalize_query(message)
    failure_part = re.sub(
        r"^\s*(please\s+)?(simulat\w*|stimulat\w*|inject|trigger|cause|break)\w*\s+",
        "",
        q,
    ).strip()
    if not failure_part or failure_part in ("outage", "failure", "error"):
        failure_part = "an outage"

    def _prompt(app_id: str) -> str:
        app_phrase = "both apps" if app_id == "all" else app_id
        return f"Simulate {failure_part} on {app_phrase}"

    label = failure_part if failure_part != "an outage" else "this failure"
    return (
        f"Which workload should I inject **{label}** on?",
        [
            {"label": "FastAPI API", "prompt": _prompt("fastapi")},
            {"label": "Nginx Web", "prompt": _prompt("nginx")},
            {"label": "Both apps", "prompt": _prompt("all")},
        ],
    )


def _action_target_disambiguation(action: str, message: str = "") -> tuple[str, list[dict[str, str]]]:
    if action == "outage" and message:
        return _outage_target_disambiguation(message)
    verbs = {
        "deploy": "deploy",
        "reset": "reset",
        "outage": "simulate a failure for",
        "heal": "auto-fix",
        "explain": "diagnose",
    }
    verb = verbs.get(action, action)
    return (
        f"Which workload should I **{verb}**?",
        [
            {"label": "FastAPI API", "prompt": f"{verb} fastapi"},
            {"label": "Nginx Web", "prompt": f"{verb} nginx"},
            {"label": "Both apps", "prompt": f"{verb} both apps"},
        ],
    )


def _classify_chat_action(message: str, history: list | None = None) -> tuple[str, str | None]:
    """Return (action, app_target). action=chat means no cluster mutation."""
    q = _normalize_query(message)
    target = _resolve_app_target(message, history)
    if _wants_inject_commands_explanation(message):
        return "inject_commands", target or _infer_target_from_history(history) or "all"
    if _wants_manual_fix_commands(message):
        return "manual_fix", target or _infer_target_from_history(history) or "all"
    if _is_capabilities_question(message):
        return "capabilities", None
    if re.search(r"\b(how many apps?|number of apps?|count (my )?apps?|apps do i have|apps are there)\b", q):
        return "app_count", "all"
    if re.search(r"\b(pod status|show pods|pod details|show pod|app status)\b", q) and target:
        return "app_status", target
    if re.search(r"\b(check|verify|properly|working|running ok|running properly)\b", q) and target:
        if not re.search(r"\b(simulat|inject|deploy|fix|heal|break)\b", q):
            return "app_status", target
    if re.search(r"\b(open|show|give)\b", q) and re.search(r"\b(links?|url|dashboard|app)\b", q):
        return "links", target or "all"
    if re.search(r"\b(show status|cluster status|apps status|all status|cluster snapshot|show snapshot|cluster health)\b", q):
        return "status", "all"
    if re.search(r"\b(deploy|bring up|launch|install|register|create)\b", q):
        if re.search(r"\b(both|all|them|everything|my apps)\b", q) or target == "all":
            return "deploy", "all"
        return "deploy", target
    if re.search(r"\b(reset|tear down|remove|delete|destroy|uninstall)\b", q):
        if re.search(r"\b(both|all|them|everything)\b", q) or target == "all":
            return "reset", "all"
        return "reset", target
    if re.search(
        r"\b(simulat\w*|stimulat\w*|inject|trigger|cause)\b", q,
    ) and re.search(
        r"\b(outage|outrage|failure|error|crash|oom|down|broken|pending|"
        r"restart|restarting|continuously|crashloop)\b",
        q,
    ):
        return "outage", target
    if re.search(r"\b(cause|inject|trigger|simulate|break)\b", q) and re.search(
        r"\b(restart|restarting|continuously|crash\s*loop|crashloop|keeps restarting)\b",
        q,
    ):
        return "outage", target
    if re.search(r"\b(one more|another)\b", q) and re.search(r"\b(outage|outrage)\b", q):
        return "outage", target
    if re.search(r"\b(volume mount|mount failure|volumemount)\b", q):
        return "outage", target
    if re.search(
        r"\b(crash\s*loop|crashloop|oom|out of memory|readiness|liveness|configmap|config map|"
        r"init\s*container|image\s*pull|imagepull|pending|unschedulable|cpu throttl|hostpath|"
        r"privileged|secret env|startup probe|bad command|probe fail)\b",
        q,
    ) and re.search(r"\b(simulat\w*|stimulat\w*|inject|trigger|cause|break)\b", q):
        return "outage", target
    if re.search(r"\b(simulate outage|simulate an outage|break the app|break app|cause outage)\b", q):
        return "outage", target
    if re.search(r"\bbreak\b", q) and re.search(r"\b(fastapi|nginx|app|them|both)\b", q):
        return "outage", target
    if re.search(r"\b(broken|failing|down)\b", q) and re.search(r"\b(simulat|inject|cause)\b", q):
        return "outage", target
    # Chaos Lab cards — "Crash loop and OOM on fastapi" (no "simulate" verb)
    if re.search(
        r"\b(crash\s*loop|crashloop|\boom\b|image\s*pull|network\s*policy|bad\s*rollout|"
        r"rollout\s*stuck|pvc\s*pending|volume\s*mount|http\s*500|memory\s*leak|cpu\s*stress|"
        r"dns\s*failure|network\s*delay|high\s*latency|port\s*mismatch|network\s*loss|"
        r"instant\s*outage|gitops|storage\s*storm|meltdown|nightmare|disaster)\b",
        q,
    ) and re.search(r"\bon\s+(fastapi|nginx|both(\s+apps)?)\b", q):
        return "outage", target
    if re.search(r"\b(auto-?fix|self-?heal|heal|restore|recover|fix)\b", q):
        if re.search(r"\b(both|all|them|everything|cluster|any issues|issues|my apps|apps)\b", q) or target == "all":
            return "heal", "all"
        return "heal", target
    if re.search(r"\bfix (it|this|the app|nginx|fastapi|them)\b", q):
        return "heal", target
    if re.search(
        r"\b(explain with ai|full diagnosis|run diagnosis|ai diagnosis|"
        r"what broke|what happened|what went wrong|diagnose|root cause|why .* down|"
        r"broken|failing)\b",
        q,
    ):
        return "explain", target
    return "chat", None


def _execute_chat_action(
    action: str,
    target: str | None,
    on_step: StepCallback,
    message: str = "",
    history: list | None = None,
) -> dict:
    """Run a mutating demo action from chat; returns action metadata + message."""
    if action == "inject_commands":
        t = target or "all"
        return {
            "action": action,
            "target": t,
            "message": _inject_commands_reply(t, history),
            "apps_status": _apps_status_for_target(t),
        }

    if action == "manual_fix":
        t = target or "all"
        return {
            "action": action,
            "target": t,
            "message": _manual_fix_commands_reply(t, history),
            "apps_status": _apps_status_for_target(t),
        }

    if not target:
        raise ValueError("No workload target specified")

    def step(title: str, detail: str = "", phase: str = "cluster") -> None:
        if on_step:
            on_step({"title": title, "detail": detail, "phase": phase})

    if action == "capabilities":
        return {"action": action, "message": _capabilities_reply(), "ui": "capabilities"}

    if action == "app_count":
        rows = _apps_status_data()
        n = len(cfg.demo_apps())
        deployed = sum(1 for r in rows if r["deployed"])
        healthy = sum(1 for r in rows if r["healthy"])
        lines = [
            f"You have **{n} demo apps** in namespace `{cfg.NAMESPACE}`: **FastAPI** and **Nginx**.\n",
            f"- **Deployed:** {deployed}/{n} · **Healthy:** {healthy}/{n}\n",
        ]
        for r in rows:
            lines.append(f"- **{r['label']}** — {r['state']} · `{r['pod_line']}`")
        return {
            "action": action,
            "message": "\n".join(lines),
            "apps_status": rows,
            "ui": "status_cards",
            "links": resolved_public_app_links(),
        }

    if action == "links":
        link_target = target or "all"
        app = cfg.demo_app(link_target) if link_target != "all" else None
        label = app["label"] if app else "your demo apps"
        return {
            "action": action,
            "target": link_target,
            "message": f"**{label}** — use the cards below to open each app, Argo CD, or health check.",
            "apps_status": _apps_status_for_target(link_target),
            "ui": "status_cards",
            "links": resolved_public_app_links(),
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
            f"- GitOps: `{row['gitops'] or 'not available'}`"
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
        bad = _unsupported_workload_token(message)
        if bad:
            return {"action": action, "message": _unsupported_workload_reply(bad)}
        step("Deploy requested", target, "git")
        if target == "all":
            both_exist = all(
                _app_workloads_exist(cfg.demo_app(a)) for a in ("fastapi", "nginx")
            )
            if both_exist:
                msg, choices = _both_already_deployed_reply()
                return {
                    "action": action,
                    "target": "all",
                    "message": msg,
                    "ui": "choices",
                    "choices": choices,
                    "apps_status": _apps_status_for_target("all"),
                    "links": resolved_public_app_links(),
                }
            deploy_demo_app("fastapi", on_step=on_step)
            deploy_demo_app("nginx", on_step=on_step)
            return _action_result_payload(
                action,
                "all",
                "**Both demo apps deployed.**\n\n"
                "FastAPI and Nginx are registered in Argo CD. "
                "Ask **pod status** to see live health and links.",
                include_cards=True,
            )
        app = cfg.demo_app(target)
        if _app_workloads_exist(app):
            msg, choices = _already_deployed_reply(target)
            return {
                "action": action,
                "target": target,
                "message": msg,
                "ui": "choices",
                "choices": choices,
                "apps_status": _apps_status_for_target(target),
                "links": {target: _app_browser_links(target)},
            }
        deploy_demo_app(target, on_step=on_step)
        return _action_result_payload(
            action,
            target,
            f"**{app['label']} deployed.** Pods should become Ready shortly — ask **pod status** for live state.",
            include_cards=True,
        )

    if action == "reset":
        step("Reset requested", target, "argocd")
        if target == "all":
            reset_demo_app("fastapi", on_step=on_step)
            reset_demo_app("nginx", on_step=on_step)
            return _action_result_payload(action, "all", "**Reset complete** for FastAPI and Nginx.", include_cards=True)
        reset_demo_app(target, on_step=on_step)
        app = cfg.demo_app(target)
        return _action_result_payload(action, target, f"**{app['label']} reset complete.**", include_cards=True)

    if action == "outage":
        if target == "all" and not _user_wants_both_apps(message):
            msg, choices = _outage_target_disambiguation(message)
            return {
                "action": action,
                "message": msg,
                "ui": "choices",
                "choices": choices,
            }
        step("Simulating failure", target, "break")
        if _wants_repeat_same_outage(message):
            hist_target, hist_modes = _last_failure_injection_from_history(history)
            if hist_target and not target:
                target = hist_target
            if not hist_modes and target and target != "all":
                app_obj = cfg.demo_app(target)
                if _app_workloads_exist(app_obj):
                    hist_modes = _app_injected_modes(app_obj) or None
            modes = hist_modes or classify_failure_modes(message)
        else:
            modes = classify_failure_modes(message)
        mode_label = ", ".join(failure_mode_label(m) for m in modes)
        if target and target != "all":
            app = cfg.demo_app(target)
            if _app_workloads_exist(app):
                current = _app_injected_modes(app)
                if current and set(current) == set(modes):
                    pod_line = _app_pod_summary(app)
                    return _action_result_payload(
                        action,
                        target,
                        (
                            f"{format_active_failure_headline(modes, pod_line)} on **{app['label']}** — "
                            "same outage as before; no change applied.\n\n"
                            f"**Observed pod state:** `{pod_line}`\n\n"
                            "Say **auto-fix** to clear it, or choose a different failure type."
                        ),
                        inject_summary=[_inject_summary_item(target, modes, pod_line)],
                    )
        if target == "all":
            _pause_gitops_for_demo_apps()
            inject_items: list[dict] = []
            failed_labels: list[str] = []
            for app_id in ("fastapi", "nginx"):
                r = _simulate_app_error_impl(app_id, message=message, mode=modes)
                inject_items.extend(r.get("inject_summary") or [])
                if not _app_has_active_injection(cfg.demo_app(app_id)):
                    failed_labels.append(cfg.demo_app(app_id)["label"])
            expected_desc = describe_expected_failure(modes)
            observed = "\n".join(
                f"- **{it['label']}** — `{it['pod_line']}`" for it in inject_items
            )
            pending_note = ""
            for it in inject_items:
                line = (it.get("pod_line") or "").lower()
                if "containercreating" in line or "pending" in line:
                    pending_note = (
                        "\n\n**Note:** pods still rolling out — "
                        "`ContainerCreating` / `Pending` often precedes the expected error "
                        "(e.g. ErrImagePull) by 15–60s. Refresh **pod status** or check Argo CD events."
                    )
                    break
            body = (
                f"**{mode_label}** active on **FastAPI and Nginx**.\n\n"
                f"{expected_desc}\n\n"
                f"**Observed in cluster:**\n{observed}"
                f"{pending_note}\n\n"
                "Apps stay **Outage active** until you run **auto-fix both apps**."
            )
            if failed_labels:
                body += (
                    f"\n\n**Warning:** injection stamp missing on **{', '.join(failed_labels)}** "
                    "— Argo CD may have reverted changes. Run **auto-fix both apps**, then simulate again."
                )
            return _action_result_payload(
                action,
                "all",
                body,
                inject_summary=inject_items,
            )
        r = _with_step_stream(
            on_step,
            lambda: _simulate_app_error_impl(target, message=message, mode=modes),
        )
        app = cfg.demo_app(target)
        msg = r.get("message") or f"**{mode_label} active on {app['label']}.**"
        return _action_result_payload(
            action,
            target,
            msg,
            inject_summary=r.get("inject_summary"),
        )

    if action == "heal":
        skip = _already_healthy_reply(target)
        if skip:
            return _action_result_payload(
                action,
                target,
                skip["message"],
                include_cards=True,
                heal_summary=skip.get("heal_summary"),
            )
        step("Auto-fix requested", target, "health")
        if target == "all":
            from concurrent.futures import ThreadPoolExecutor, as_completed
            results_by_app: dict[str, dict] = {}
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = {
                    pool.submit(_auto_fix_app_impl, aid, fast=True): aid
                    for aid in ("fastapi", "nginx")
                }
                for fut in as_completed(futures):
                    aid = futures[fut]
                    try:
                        results_by_app[aid] = fut.result()
                    except Exception as exc:
                        results_by_app[aid] = {"heal_error": str(exc)}
            _wait_post_heal_health(["fastapi", "nginx"], timeout=90)
            summaries = [
                _format_heal_app_summary(aid, results_by_app.get(aid))
                for aid in ("fastapi", "nginx")
            ]
            return _action_result_payload(
                action,
                "all",
                _format_heal_all_message(summaries),
                include_cards=True,
                heal_summary=summaries,
            )
        r = auto_fix_app(target, on_step=on_step, fast=True)
        _wait_post_heal_health([target], timeout=90)
        summary = _format_heal_app_summary(target, r)
        return _action_result_payload(
            action,
            target,
            _format_heal_all_message([summary]),
            include_cards=True,
            heal_summary=[summary],
        )

    if action == "explain":
        step("Running AI diagnosis", target, "ai")
        r = explain_demo_app(target, on_step=on_step)
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
    apps_list = _apps_status_data()
    deployed = [r for r in apps_list if r.get("deployed")]
    healthy = bool(deployed) and all(r["healthy"] for r in deployed)
    return {
        "ok": True,
        "healthy": healthy,
        "holmes_enabled": cfg.HOLMES_ENABLED,
        "chat_actions_enabled": cfg.CHAT_ACTIONS_ENABLED,
        "chat_mode": cfg.CHAT_MODE,
        "chat_modes": ["demo", "agent", "hybrid"],
        "failure_modes": list_failure_modes(),
        "failure_modes_by_category": failure_modes_by_category(),
        "failure_mode_count": len(list_failure_modes()),
        "chaos_mesh": chaos_mesh_info(),
        "demo_scenarios": list_demo_scenarios(),
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
    app_lines = []
    for app_id, app in cfg.demo_apps().items():
        exists = _app_workloads_exist(app)
        pod = _app_pod_summary(app) if exists else "not deployed"
        gitops = _argocd_status_for_app(app) if app.get("gitops") else "n/a"
        app_lines.append(
            f"  - {app['label']} ({app_id}): deployed={exists}, pod={pod}, gitops={gitops}"
        )
    apps_block = "\n".join(app_lines) if app_lines else "  - none"
    kubectl_ref = []
    for app_id, app in cfg.demo_apps().items():
        kubectl_ref.append(
            f"  {app_id}: deployment={app['deployment']}, "
            f"label_selector={app['pod_label']} (NEVER use app=nginx or deployment nginx)"
        )
    return "\n".join([
        f"namespace: {cfg.NAMESPACE}",
        f"demo_app_count: {len(cfg.demo_apps())} (fastapi + nginx)",
        f"deployment: {cfg.DEPLOYMENT_NAME}",
        f"image: {ctx.get('image') or 'unknown'}",
        f"replicas_ready: {ctx.get('ready_replicas', 0)}/{ctx.get('replicas', -1)}",
        f"staging_healthy: {_staging_is_healthy(ctx)}",
        f"argocd_sync: {tree.get('sync_status', 'Unknown')}",
        f"argocd_health: {tree.get('health_status', 'Unknown')}",
        "demo_apps:",
        apps_block,
        "kubectl_reference:",
        "\n".join(kubectl_ref),
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


def _wants_manual_fix_commands(message: str) -> bool:
    """User wants copy-paste kubectl to fix the active error — every failure mode."""
    q = _normalize_query(message)
    if _wants_inject_commands_explanation(message) or _wants_kubectl_check_commands(message):
        return False
    if re.search(r"\bauto-?fix\b", q) and not re.search(
        r"\b(manual|kubectl|command|without|myself|shell)\b", q,
    ):
        return False
    if _wants_manual_fix(message):
        return True
    if re.search(r"\bmanual commands? to fix\b", q):
        return True
    if re.search(r"\b(command|commands|kubectl|steps)\b", q) and re.search(
        r"\b(fix|heal|repair|resolve|remediat|recover|correct)\b", q,
    ):
        return True
    if re.search(r"\b(give|show|need|want|list)\b", q) and re.search(
        r"\b(fix|heal|repair)\b", q,
    ) and re.search(r"\b(command|commands|kubectl|steps)\b", q):
        return True
    if re.search(r"\bhow (do i|to|can i) fix\b", q) and re.search(
        r"\b(manually|kubectl|command|shell|terminal)\b", q,
    ):
        return True
    return False


def _modes_from_pod_symptoms(detail: dict) -> list[str]:
    """Infer failure mode from live pod line when injection stamp is missing."""
    blob = " ".join(
        str(detail.get(k) or "") for k in ("line", "reason", "message", "phase")
    ).lower()
    modes: list[str] = []
    if "errimagepull" in blob or "imagepullbackoff" in blob:
        modes.append("image")
    if "crashloop" in blob:
        modes.append("crash")
    if "oomkilled" in blob or "oom" in blob:
        modes.append("oom")
    if "createcontainerconfigerror" in blob or "mountvolume" in blob:
        modes.append("volume")
    if "failedscheduling" in blob or "unschedulable" in blob:
        modes.append("pending")
    if "networkpolicy" in blob:
        modes.append("network_policy")
    if "0/0" in blob or "replicas=0" in blob:
        modes.append("instant")
    return modes


def _manual_heal_apply_line(app: dict) -> str:
    """Repo-relative kubectl apply — matches Cloud Shell tarball layout."""
    app_id = app.get("id") or ""
    if app_id == "fastapi":
        overlay = cfg.HEAL_OVERLAY_PATH
        if overlay.is_dir() and (overlay / "kustomization.yaml").is_file():
            return "kubectl apply -k deploy/k8s/staging-heal"
        return "kubectl apply -f deploy/k8s/staging-app"
    if app_id == "nginx":
        return "kubectl apply -f deploy/k8s/staging-nginx"
    return f"kubectl apply -f deploy/k8s/{app['deployment']}"


def _manual_fix_commands_reply(
    target: str | None,
    history: list | None = None,
    lang: str = "en",
) -> str:
    """Exact kubectl remediation per failure mode — mirrors Auto-fix under the hood."""
    from failure_modes import failure_mode_label, kubectl_manual_fix_recipes

    ns = cfg.NAMESPACE
    resolved = target or _infer_target_from_history(history) or "all"
    app_ids = list(cfg.demo_apps().keys()) if resolved == "all" else [resolved]
    hist_modes = _infer_modes_from_history(history)

    lines = [
        "**Manual fix commands** — same remediation Auto-fix runs (paste into OCI Cloud Shell)\n",
        f"Namespace: `{ns}`. Run from your repo root: `cd ~/devops-selfheal`\n",
        "**Naming:** Nginx = deployment `nginx-demo`, label `app=nginx-demo`. "
        f"FastAPI = deployment `{cfg.DEPLOYMENT_NAME}`, label `{cfg.POD_LABEL}`.\n",
    ]

    any_work = False
    for app_id in app_ids:
        app = cfg.demo_app(app_id)
        modes = _app_injected_modes(app) or hist_modes
        if not modes:
            detail = _pod_detail_for_label(app["pod_label"])
            if not _app_is_healthy(app) or detail.get("reason"):
                modes = _modes_from_pod_symptoms(detail)
        if not modes and not _app_has_active_injection(app) and _app_is_healthy(app):
            continue
        if not modes:
            modes = ["image"]  # default demo outage pattern

        any_work = True
        dep = app["deployment"]
        ctr = app["container"]
        good = app.get("good_image") or cfg.GOOD_IMAGE
        label = app["pod_label"]
        argo = app.get("argocd_app") or ""
        mode_labels = ", ".join(failure_mode_label(m) for m in modes)
        detail = _pod_detail_for_label(app["pod_label"])
        pod_hint = detail.get("line") or "check pods below"

        lines.append(f"### {app['label']} — fix **{mode_labels}**")
        lines.append(f"Current pod: `{pod_hint}`\n")
        lines.append("```bash")
        lines.extend(kubectl_manual_fix_recipes(modes, app))
        if argo:
            lines.append(f"# Pause Argo CD auto-sync while fixing (GitOps won't fight you)")
            lines.append(
                f"kubectl patch application {argo} -n {cfg.ARGOCD_NAMESPACE} "
                "--type merge -p '{\"spec\":{\"syncPolicy\":{\"automated\":null}}}'"
            )
        lines.append(f"kubectl delete deployment {dep} -n {ns} --ignore-not-found --wait=false")
        lines.append(_manual_heal_apply_line(app))
        lines.append(f"kubectl scale deployment/{dep} -n {ns} --replicas=1")
        lines.append(f"kubectl set image deployment/{dep} {ctr}={good} -n {ns}")
        lines.append(
            f"kubectl patch deployment/{dep} -n {ns} --type=json "
            "-p '[{\"op\":\"replace\",\"path\":\"/spec/template/spec/containers/0/imagePullPolicy\",\"value\":\"Always\"}]'"
        )
        lines.append(
            f"kubectl annotate deployment/{dep} -n {ns} "
            "enlight-lab/injected-modes- enlight-lab/injected-by- enlight-lab/argocd-visible-outage- --overwrite"
        )
        lines.append(
            f"kubectl delete pods -n {ns} -l {label} --wait=false --force --grace-period=0"
        )
        lines.append(f"kubectl rollout status deployment/{dep} -n {ns}")
        lines.append(f"kubectl get pods -n {ns} -l {label}")
        if argo:
            lines.append(f"# Re-sync Argo CD after pod is healthy")
            lines.append(
                f"kubectl patch application {argo} -n {cfg.ARGOCD_NAMESPACE} "
                "--type merge -p '{\"metadata\":{\"annotations\":{\"argocd.argoproj.io/refresh\":\"hard\"}}}'"
            )
        lines.append("```\n")

    if not any_work:
        if lang == "hi":
            return "**कोई सक्रिय आउटेज नहीं** — पहले chaos inject करें, फिर manual fix commands पूछें।"
        return (
            "**No active outage detected** on the selected apps. "
            "Inject a failure first, then ask *manual commands to fix the error*.\n\n"
            "Or say **auto-fix both apps** to heal automatically."
        )

    lines.append(
        "These commands match **Guided demo → Auto-fix**. "
        "Prefer one click? Say **auto-fix fastapi** or **auto-fix both apps**."
    )
    return "\n".join(lines)


def _wants_inject_commands_explanation(message: str) -> bool:
    """User asks what kubectl ran for a prior injection — not a new inject request."""
    q = _normalize_query(message)
    if re.search(r"\bhow to inject\b", q):
        return False
    if re.search(r"\bcommands? you used to inject\b", q):
        return True
    if re.search(r"\bwhat (kubectl|commands?) (did you|you) (run|use|execute)\b", q):
        return True
    if re.search(r"\bhow did you\b", q) and re.search(r"\b(inject|break|simulate|cause)\b", q):
        return True
    if re.search(r"\b(which|what|show|give|list|tell)\b", q) and re.search(
        r"\b(command|commands|kubectl|cmd)\b", q,
    ):
        if re.search(r"\b(used|you run|you use|did you|were run|ran)\b", q):
            return True
        if re.search(r"\b(inject|injected|injection|simulate|simulated|chaos)\b", q) and re.search(
            r"\b(used|you|did|were|ran)\b", q,
        ):
            return True
    return False


def _infer_modes_from_history(history: list | None) -> list[str]:
    """Best-effort mode list from the last assistant outage/inject message."""
    if not history:
        return []
    from failure_modes import classify_failure_modes

    for h in reversed(history[-8:]):
        if h.get("role") != "assistant":
            continue
        blob = str(h.get("content") or "")
        if not blob.strip():
            continue
        if not re.search(
            r"\b(active on|injected|outage|errimage|crash|failure|chaos|simulate)\b",
            blob,
            re.I,
        ):
            continue
        modes = classify_failure_modes(blob)
        if modes:
            return modes
    return []


def _inject_commands_reply(target: str | None, history: list | None = None) -> str:
    """Exact kubectl Enlight Lab ran (or would run) for active chaos — not diagnostic checks."""
    from failure_modes import failure_mode_label, kubectl_inject_command_recipes

    ns = cfg.NAMESPACE
    resolved = target or "all"
    app_ids = list(cfg.demo_apps().keys()) if resolved == "all" else [resolved]

    lines = [
        "**Injection commands** — what Enlight Lab ran on the cluster\n",
        f"Namespace: `{ns}`. These are the **inject** steps (not verify/describe commands).\n",
    ]

    any_active = False
    hist_modes = _infer_modes_from_history(history)

    for app_id in app_ids:
        app = cfg.demo_app(app_id)
        modes = _app_injected_modes(app) or hist_modes
        if not modes:
            continue
        any_active = True
        mode_labels = ", ".join(failure_mode_label(m) for m in modes)
        lines.append(f"### {app['label']} — {mode_labels}")
        lines.append("```bash")
        if app.get("gitops") and app.get("argocd_app"):
            argo = app["argocd_app"]
            lines.append(f"# Pause Argo CD auto-sync (GitOps won't revert chaos before auto-fix)")
            lines.append(
                f"kubectl patch application {argo} -n {cfg.ARGOCD_NAMESPACE} "
                "--type merge -p '{\"spec\":{\"syncPolicy\":{\"automated\":null}}}'"
            )
        lines.extend(kubectl_inject_command_recipes(modes, app))
        lines.append(
            f"kubectl delete pods -n {ns} -l {app['pod_label']} "
            "--wait=false --force --grace-period=0  # roll pods to pick up changes"
        )
        joined = ",".join(modes)[:240]
        lines.append(
            f"kubectl annotate deployment/{app['deployment']} -n {ns} "
            f"enlight-lab/injected-modes={joined} enlight-lab/injected-by=enlight-selfheal --overwrite"
        )
        lines.append("```\n")

    if not any_active:
        lines.append(
            "No active chaos stamp on the cluster right now. "
            "Run a Chaos Lab scenario or say **simulate image pull on both apps**, then ask again.\n"
        )
        if hist_modes:
            labels = ", ".join(failure_mode_label(m) for m in hist_modes)
            lines.append(f"From recent chat, the last injection looked like: **{labels}**.\n")
            lines.append("Typical commands for that scenario:\n")
            for app_id in app_ids:
                app = cfg.demo_app(app_id)
                lines.append(f"### {app['label']} (reconstructed)")
                lines.append("```bash")
                lines.extend(kubectl_inject_command_recipes(hist_modes, app))
                lines.append("```\n")

    lines.append(
        "To **verify** the outage in OCI Cloud Shell, ask: "
        "*give me commands to check the error in cloud shell*."
    )
    return "\n".join(lines)


def _wants_kubectl_check_commands(message: str) -> bool:
    """User wants copy-paste kubectl to verify an outage in Cloud Shell."""
    q = _normalize_query(message)
    if re.search(r"\b(fix|heal|repair|resolve|remediat|recover|correct|undo)\b", q):
        return False
    if re.search(r"\b(cloud\s*shell|oci\s*shell)\b", q) and re.search(
        r"\b(command|commands|check|verify|err|error|see|prove|kubectl|exact|excat)\b", q,
    ):
        return True
    if re.search(r"\b(commands?|command)\b.*\b(check|verify|run|shell|cloud)\b", q):
        return True
    if re.search(r"\b(give|show|need)\b.*\bcommands?\b", q) and re.search(
        r"\b(check|verify|shell|kubectl|cloud|error|err)\b", q,
    ):
        return True
    if re.search(r"\b(exact|exactly|excat|prove)\b.*\b(err|error|failure|outage)\b", q):
        return True
    if "kubectl" in q and any(w in q for w in ("check", "verify", "describe", "command", "prove")):
        return True
    return False


def _kubectl_diagnostic_reply(
    target: str | None = None,
    message: str = "",
    history: list[dict] | None = None,
) -> str:
    """Exact kubectl for enlight-staging — correct labels, no placeholder brackets."""
    ns = cfg.NAMESPACE
    resolved = target or _resolve_app_target(message, history) or _infer_target_from_history(history)
    if resolved == "all" or not resolved:
        app_ids = list(cfg.demo_apps().keys())
    else:
        app_ids = [resolved]

    lines = [
        f"**Cloud Shell commands** — namespace `{ns}`\n",
        "Copy these exactly. **Never** use `app=nginx`, deployment `nginx`, or `<pod-name>` placeholders.\n",
        f"**Nginx** uses label `app=nginx-demo` and deployment `nginx-demo`. "
        f"**FastAPI** uses `app=fastapi` and deployment `{cfg.DEPLOYMENT_NAME}`.\n",
    ]

    lines.append("### Both apps (quick view)")
    lines.append("```bash")
    lines.append(f"kubectl get pods,deploy -n {ns}")
    lines.append(f"kubectl get application -n {cfg.ARGOCD_NAMESPACE} {cfg.ARGOCD_APP} {cfg.NGINX_ARGOCD_APP}")
    lines.append("```\n")

    for app_id in app_ids:
        app = cfg.demo_app(app_id)
        dep = app["deployment"]
        label = app["pod_label"]
        detail = _pod_detail_for_label(app["pod_label"])
        pod_name = detail.get("pod_name") or ""
        injected = _app_injected_modes(app)
        lines.append(f"### {app['label']}")
        lines.append(f"- Deployment: `{dep}`")
        lines.append(f"- Label selector: `{label}`")
        if app_id == "nginx":
            lines.append("- **Not** `app=nginx` — that label does not exist in this cluster")
        if pod_name:
            lines.append(f"- Current pod: `{pod_name}` · `{detail.get('line', '')}`")
        if injected:
            lines.append(
                "- Active chaos: "
                + ", ".join(failure_mode_label(m) for m in injected)
            )
        lines.append("")
        lines.append("```bash")
        lines.append(f"kubectl get pods -n {ns} -l {label}")
        if pod_name:
            lines.append(f"kubectl describe pod {pod_name} -n {ns}")
        else:
            lines.append(f"POD=$(kubectl get pods -n {ns} -l {label} -o jsonpath='{{.items[0].metadata.name}}')")
            lines.append(f"kubectl describe pod \"$POD\" -n {ns}")
        lines.append(f"kubectl describe deployment {dep} -n {ns}")
        lines.append(f"kubectl get events -n {ns} --sort-by=.lastTimestamp | tail -25")
        lines.append("```")
        lines.append("")

    lines.append("**Scheduling / taint / affinity failures (Pending pods):**")
    lines.append("```bash")
    lines.append("kubectl get nodes -o custom-columns=NAME:.metadata.name,TAINTS:.spec.taints")
    lines.append("```")
    lines.append("\nIn **describe pod** → scroll to **Events** for `FailedScheduling`, `FailedMount`, etc.")
    return "\n".join(lines)


def _is_bad_image_outage(ctx: dict) -> bool:
    image = (ctx.get("image") or "").lower()
    reason = (ctx.get("pod_reason") or "").lower()
    return (
        "does-not-exist" in image
        or "errimagepull" in reason
        or "imagepullbackoff" in reason
    )


def _manual_fix_steps_reply(
    ctx: dict,
    tree: dict,
    lang: str = "en",
    history: list | None = None,
    target: str | None = None,
) -> str:
    """Exact kubectl / Argo steps — what Auto-fix runs under the hood."""
    resolved = target or "fastapi"
    reply = _manual_fix_commands_reply(resolved, history, lang)
    if "**No active outage detected**" in reply:
        # Legacy fastapi-only ImagePullBackOff hint when ctx has bad image
        if _is_bad_image_outage(ctx):
            ns = cfg.NAMESPACE
            dep = cfg.DEPLOYMENT_NAME
            ctr = cfg.CONTAINER_NAME
            good = cfg.GOOD_IMAGE
            bad = ctx.get("image") or "unknown"
            label = cfg.POD_LABEL
            k8s_block = (
                f"kubectl set image deployment/{dep} {ctr}={good} -n {ns}\n"
                f"kubectl scale deployment/{dep} -n {ns} --replicas=1\n"
                f"kubectl rollout status deployment/{dep} -n {ns}\n"
                f"kubectl get pods -n {ns} -l {label}"
            )
            return (
                f"**Manual fix (kubectl)**\n\n"
                f"**What broke:** Image `{bad}` does not exist → `ImagePullBackOff`.\n\n"
                f"```bash\n{k8s_block}\n```"
            )
    return reply


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
    if _wants_layman_explain(message) or _is_root_cause_question(message):
        if not re.search(
            r"\b(how to fix|how do i fix|how can i fix|fix this|fix it|remediat|resolve)\b",
            q,
        ):
            return False
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
        if _wants_manual_fix_commands(message):
            target = _resolve_app_target(message, history) or _infer_target_from_history(history) or "all"
            return preamble + _manual_fix_commands_reply(target, history, lang)
        if _wants_manual_fix(message):
            return preamble + _manual_fix_steps_reply(ctx, tree, lang, history=history)
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
    if _wants_kubectl_check_commands(message):
        fix_note = (
            "User wants kubectl CHECK commands for Cloud Shell. "
            "Nginx: deployment nginx-demo, label app=nginx-demo (NOT app=nginx, NOT deployment nginx). "
            "FastAPI: deployment fastapi, label app=fastapi. "
            "Never use angle brackets like <nginx-pod-name>. "
            "Give POD=$(kubectl get pods -l SELECTOR ...) or use pod name from facts.\n"
        )
    elif _wants_manual_fix_commands(message):
        fix_note = (
            "User wants MANUAL kubectl FIX commands for Cloud Shell. "
            "Nginx: deployment nginx-demo, label app=nginx-demo. "
            "FastAPI: deployment fastapi, label app=fastapi. "
            "Give mode-specific undo + delete deployment + apply -k deploy/k8s/staging-heal "
            "or apply -f deploy/k8s/staging-nginx. Never use app=nginx or <pod-name>.\n"
        )
    elif _wants_manual_fix(message):
        fix_note = (
            f"User wants a MANUAL fix via kubectl/terminal — give copy-paste bash commands: "
            f"`kubectl set image deployment/{cfg.DEPLOYMENT_NAME} {cfg.CONTAINER_NAME}={cfg.GOOD_IMAGE} "
            f"-n {cfg.NAMESPACE}`, scale, rollout status, get pods. "
            "Do NOT tell them to use Guided demo Step 4 or Auto-fix unless as optional footnote.\n"
        )
    elif _wants_layman_explain(message) or _is_root_cause_question(message):
        fix_note = (
            "User wants ROOT CAUSE explanation in plain language — explain what broke and why. "
            "Do NOT give kubectl fix commands unless they explicitly ask how to fix.\n"
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


def _try_holmes_fast_answer(
    message: str,
    ctx: dict,
    tree: dict,
    history: list[dict] | None = None,
) -> str | None:
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
        return _cluster_greeting_reply(message)

    if re.match(r"^(how are you|how r u|how's it going|how is it going)\b", q):
        rows = _apps_status_data()
        healthy = sum(1 for r in rows if r["healthy"])
        return (
            f"I'm online on **{cfg.NAMESPACE}** — **{healthy}/{len(rows)}** demo apps healthy.\n\n"
            + "\n".join(f"- **{r['label']}** — {r['state']} · `{r['pod_line']}`" for r in rows)
            + "\n\nAsk *pod status*, *what broke?*, or *auto-fix*."
        )

    if _is_pod_details_telemetry(q):
        return _format_pod_details_reply(ctx, tree, lang)

    if _wants_inject_commands_explanation(message):
        target = _resolve_app_target(message, history) or _infer_target_from_history(history) or "all"
        return _inject_commands_reply(target, history)

    if _wants_manual_fix_commands(message):
        target = _resolve_app_target(message, history) or _infer_target_from_history(history) or "all"
        return _manual_fix_commands_reply(target, history, lang)

    if _wants_kubectl_check_commands(message):
        target = _resolve_app_target(message, history) or _infer_target_from_history(history)
        return _kubectl_diagnostic_reply(target, message, history)

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
    mode: str | None = None,
) -> dict:
    """Free-form HolmesGPT Q&A — separate from the guided demo Step 3."""
    from agent_tools import effective_chat_mode, gemini_agent_chat, should_use_demo_fast_path

    global _last_gemini_failure
    _last_gemini_failure = None
    text = (message or "").strip()
    if not text:
        raise ValueError("Message is required")
    ok_cluster, cluster_msg = _cluster_api_ok()
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

    chat_mode = effective_chat_mode(mode)
    use_demo = should_use_demo_fast_path(text, history, mode)

    if _is_greeting(text):
        rows = _apps_status_data()
        step("Answer ready", "Cluster greeting", "done")
        return result(
            _cluster_greeting_reply(text),
            "action",
            ui="status_cards",
            apps_status=rows,
            links=resolved_public_app_links(),
        )

    if _is_dangerous_operation(text):
        step("Answer ready", "Blocked destructive request", "done")
        return result(_dangerous_operation_reply(), "action")

    if _has_conflicting_instructions(text):
        step("Answer ready", "Conflicting instructions", "done")
        return result(_conflicting_instructions_reply(), "action")

    impossible_dest = _impossible_deploy_destination(text)
    if impossible_dest:
        step("Answer ready", "Unsupported deploy target", "done")
        return result(_impossible_deploy_reply(impossible_dest), "action")

    unsupported = _unsupported_workload_token(text)
    if unsupported:
        step("Answer ready", "Unsupported workload", "done")
        return result(_unsupported_workload_reply(unsupported), "action")

    info_reply = _try_curated_info_reply(text)
    if info_reply:
        step("Answer ready", "Cluster info", "done")
        return result(info_reply, "telemetry")

    diag_target = _resolve_app_target(text, history) or _infer_target_from_history(history)
    scoped_diag = _try_scoped_diagnosis_reply(text, history)
    if scoped_diag and diag_target and diag_target != "all":
        step("Answer ready", f"Diagnosis for {cfg.demo_app(diag_target)['label']}", "done")
        return result(
            scoped_diag,
            "telemetry",
            apps_status=_scoped_apps_status(_apps_status_data(), diag_target),
            links={diag_target: _app_browser_links(diag_target)},
        )

    catalog = _try_failure_catalog_reply(text)
    if catalog:
        reply, catalog_data = catalog
        step("Answer ready", "Failure mode catalog", "done")
        return result(reply, "telemetry", ui="failure_catalog", failure_catalog=catalog_data)

    act_early, target_early = _classify_chat_action(text, history)
    if act_early == "status":
        target_early = _resolve_action_target(act_early, text, history, target_early) or "all"
        rows = _apps_status_data() if target_early == "all" else [
            r for r in _apps_status_data() if r["id"] == target_early
        ]
        step("Answer ready", "Live cluster status", "done")
        return result(
            _format_apps_status_reply() if target_early == "all" else _execute_chat_action(
                "app_status", target_early, on_step, message=text, history=history,
            )["message"],
            "action",
            ui="status_cards",
            apps_status=rows,
            links=resolved_public_app_links(),
        )

    if cfg.CHAT_ACTIONS_ENABLED:
        compound = _try_compound_deploy_break(text)
        if compound:
            deploy_t, break_t = compound
            d_res = _execute_chat_action(
                "deploy", deploy_t, on_step, message=text, history=history,
            )
            o_res = _execute_chat_action(
                "outage", break_t, on_step, message=text, history=history,
            )
            step("Done", "deploy+break", "done")
            return result(
                d_res["message"] + "\n\n---\n\n" + o_res["message"],
                "action",
                action="compound",
                action_target=break_t,
                apps_status=_scoped_apps_status(o_res.get("apps_status"), break_t),
                inject_summary=o_res.get("inject_summary"),
                links=resolved_public_app_links(),
            )

    # Curated kubectl — never let the LLM invent app=nginx or <pod-name> placeholders.
    if _wants_inject_commands_explanation(text):
        target = _resolve_app_target(text, history) or _infer_target_from_history(history) or "all"
        step("Answer ready", "Injection kubectl commands", "done")
        return result(_inject_commands_reply(target, history), "telemetry")

    if _wants_manual_fix_commands(text):
        target = _resolve_app_target(text, history) or _infer_target_from_history(history) or "all"
        lang = _resolved_language(text, history)
        step("Answer ready", "Manual fix kubectl commands", "done")
        return result(_manual_fix_commands_reply(target, history, lang), "telemetry")

    if _wants_kubectl_check_commands(text):
        target = _resolve_app_target(text, history) or _infer_target_from_history(history)
        step("Answer ready", "Cloud Shell kubectl commands", "done")
        return result(_kubectl_diagnostic_reply(target, text, history), "telemetry")

    # Engineer / hybrid agent path (Gemini + tools — like Claude Desktop + MCP)
    if chat_mode in ("agent", "hybrid") and not use_demo and _gemini_key_configured():
        ctx_pre = _incident_context()
        tree_pre = _argocd_app_tree(ctx_pre)
        facts = _holmes_cluster_facts(ctx_pre, tree_pre)
        agent_ok, agent_reply = gemini_agent_chat(
            text, facts, history=history, on_step=on_step,
        )
        if agent_ok and agent_reply.strip():
            rows = _apps_status_data()
            return result(
                agent_reply,
                "agent",
                chat_mode=chat_mode,
                apps_status=rows,
                ui="status_cards",
                links=resolved_public_app_links(),
            )

    if cfg.CHAT_ACTIONS_ENABLED and use_demo:
        if _is_greeting(text):
            rows = _apps_status_data()
            return result(
                _cluster_greeting_reply(text),
                "action",
                ui="status_cards",
                apps_status=rows,
                links=resolved_public_app_links(),
            )
        if _needs_status_disambiguation(text):
            reply, choices = _status_disambiguation_reply()
            return result(reply, "action", ui="choices", choices=choices)
        act_type, target = _classify_chat_action(text, history)
        target = _resolve_action_target(act_type, text, history, target)
        if act_type == "outage" and _wants_repeat_same_outage(text) and not target:
            hist_target, _ = _last_failure_injection_from_history(history)
            if hist_target:
                target = hist_target
        if act_type in ("deploy", "reset", "outage", "heal", "explain") and target is None:
            reply, choices = _action_target_disambiguation(act_type, text)
            return result(reply, "action", ui="choices", choices=choices)
        if act_type != "chat":
            try:
                act = _execute_chat_action(
                    act_type, target, on_step, message=text, history=history,
                )
                step("Done", act_type, "done")
                act_target = act.get("target")
                return result(
                    act["message"],
                    "action",
                    action=act.get("action"),
                    action_target=act_target,
                    apps_status=(
                        None if act.get("inject_summary")
                        else _scoped_apps_status(act.get("apps_status"), act_target)
                    ),
                    heal_summary=act.get("heal_summary"),
                    inject_summary=act.get("inject_summary"),
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

    if _is_fix_question(text) and _wants_manual_fix_commands(text):
        target = _resolve_app_target(text, history) or _infer_target_from_history(history) or "all"
        step("Answer ready", "Manual kubectl fix steps", "done")
        return result(_manual_fix_commands_reply(target, history, lang), "telemetry")

    if _is_fix_question(text) and _wants_manual_fix(text):
        step("Answer ready", "Manual kubectl steps", "done")
        return result(_manual_fix_steps_reply(ctx, tree, lang, history=history), "telemetry")

    if _wants_kubectl_check_commands(text):
        target = _resolve_app_target(text, history) or _infer_target_from_history(history)
        step("Answer ready", "Cloud Shell kubectl commands", "done")
        return result(_kubectl_diagnostic_reply(target, text, history), "telemetry")

    if _is_telemetry_only_intent(text):
        fast = _try_holmes_fast_answer(text, ctx, tree, history)
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
    _timeline_step(
        timeline,
        f"Starting AI-assisted diagnosis for {app['label']}",
        "Read-only — no changes to the cluster",
    )
    _timeline_step(timeline, "Reading deployment spec and replica status", f"deployment/{app['deployment']}")
    ctx = _incident_context_for_app(app)
    _timeline_step(
        timeline,
        "Inspecting pod state and container waiting reasons",
        ctx["pod_line"] or "no pods",
    )
    _timeline_step(timeline, "Collecting recent Kubernetes warning events", cfg.NAMESPACE)

    summary, what_happened, root_cause, simple_explanation = _plain_language_explain(ctx)

    _timeline_step(timeline, "Loading Argo CD application tree", app.get("argocd_app") or "")
    argocd_tree = _argocd_app_tree_for_app(app, ctx)

    _timeline_step(
        timeline,
        "Running k8sgpt analyzers on namespace",
        f"Focus: {app['label']} workload failures",
    )
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
    dep_token = (app.get("deployment") or "").lower()
    label_token = (app.get("pod_label") or "").split("=")[-1].lower()
    priority_lines = [
        s for s in raw_lines
        if dep_token in s.lower()
        or label_token in s.lower()
        or any(k in s for k in ("Error", "BackOff", "ErrImagePull", "failed", "Problem", "ImagePull", "CrashLoop"))
    ]
    findings = _filter_k8sgpt_findings(priority_lines or raw_lines)
    if not findings:
        findings = _filter_k8sgpt_findings(ctx["events"])

    if _app_is_healthy(app):
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
    if not technical and root_cause:
        technical.append(f"{root_cause} — {ctx['pod_line'] or 'see pod status above'}")

    if holmes_ok and holmes_summary:
        simple_explanation = holmes_summary
        if root_cause and root_cause.lower() not in holmes_summary.lower():
            summary = f"{root_cause} — HolmesGPT correlated live cluster signals."

    _timeline_step(timeline, "Building plain-English summary for your client", root_cause, pause=False)

    healthy = _app_is_healthy(app)
    next_step = (
        f"No action required — {app['label']} is healthy. Use Step 2 to simulate another outage, or Reset."
        if healthy
        else f"Step 4 — click Auto-fix to restore the good image for {app['label']} and verify health."
    )

    return {
        "ok": bool(what_happened) or bool(technical),
        "summary": summary,
        "simple_explanation": simple_explanation,
        "what_happened": what_happened,
        "root_cause": root_cause,
        "next_step": next_step,
        "findings": technical[:6],
        "holmes_ok": holmes_ok,
        "holmes_enabled": cfg.HOLMES_ENABLED,
        "holmes_investigation": holmes_summary,
        "holmes_raw": holmes_raw,
        "argocd_tree": argocd_tree,
        "message": summary,
        "timeline": timeline,
        "k8sgpt_ok": code == 0,
        "k8sgpt_raw": out[:4000] if out else "",
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


def auto_fix(on_step: StepCallback = None, *, fast: bool = False) -> dict:
    return _with_step_stream(on_step, lambda: _auto_fix_impl(fast=fast))


def _auto_fix_impl(*, fast: bool = False) -> dict:
    ok, cluster_msg = _cluster_reachable()
    if not ok:
        raise RuntimeError(cluster_msg)

    app = cfg.demo_app("fastapi")
    timeline: list[dict[str, str]] = []
    _timeline_step(timeline, "Starting GitOps recovery", "AI explained only — this step applies the fix")
    argo_note = ""
    heal_error: Exception | None = None

    try:
        ready = _heal_deployment_for_app(app, timeline, fast=fast)
        if not ready:
            _timeline_step(
                timeline,
                "Pod still starting",
                _pod_troubleshoot(),
                phase="k8s",
                pause=False,
            )
    except Exception as e:
        heal_error = e

    argo_note = _argocd_finalize_heal(app, timeline, fast=fast)

    if heal_error is not None and not _pod_running_ready():
        if not fast:
            raise heal_error
        argo_note += f" ({heal_error})"

    _ensure_port_forward()
    _timeline_step(timeline, "Verifying health endpoint", cfg.APP_HEALTH_CHECK_URL, pause=not fast)
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
    summary = _format_heal_app_summary(
        "fastapi",
        {
            "healthy": recovered,
            "app_reachable": recovered,
            "heal_error": str(heal_error) if heal_error else "",
        },
    )
    _timeline_step(
        timeline,
        "Recovery complete" if recovered else "Recovery applied — verify status",
        summary["pod_line"],
        pause=False,
    )
    return {
        "timeline": timeline,
        "argocd": _kubectl_value(code, argo),
        "health": health,
        "app_reachable": recovered,
        "open_url": links.get("app_dashboard") or links.get("app_health", cfg.PUBLIC_APP_HEALTH_URL),
        "staging_url": links.get("app_dashboard"),
        "message": _format_heal_all_message([summary]) + argo_note,
        "heal_summary": [summary],
    }
