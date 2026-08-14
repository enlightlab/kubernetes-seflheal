"""Chaos Mesh integration — accurate network/DNS/stress/pod chaos on demo workloads."""
from __future__ import annotations

import json
import re
import time
from typing import Any

import config as cfg

_CHAOS_API = "chaos-mesh.org/v1alpha1"
_CHAOS_LABEL = "enlight-lab/chaos-demo"
_CHAOS_NS = cfg.NAMESPACE


def _kubectl(*args: str):
    from actions import _kubectl as k
    return k(*args)


def _kubectl_apply_yaml(yaml_text: str) -> tuple[int, str]:
    from actions import _kubectl_apply_yaml as apply
    return apply(yaml_text)


def _kubectl_value(code: int, out: str, fallback: str = "unknown") -> str:
    from actions import _kubectl_value as kv
    return kv(code, out, fallback)


def _label_selector(app: dict) -> dict[str, str]:
    key, _, val = app["pod_label"].partition("=")
    return {key: val}


def _exp_name(app: dict, kind: str) -> str:
    safe = re.sub(r"[^a-z0-9-]", "-", f"{kind}-{app['id']}".lower())[:48]
    return f"enlight-{safe}"


def chaos_mesh_installed() -> bool:
    code, out = _kubectl("get", "crd", "podchaos.chaos-mesh.org", "--no-headers")
    if code != 0:
        return False
    code2, _ = _kubectl("get", "pods", "-n", "chaos-mesh", "-l", "app.kubernetes.io/name=chaos-mesh",
                        "--no-headers")
    return code2 == 0


def chaos_mesh_status() -> dict[str, Any]:
    installed = chaos_mesh_installed()
    active: list[dict[str, str]] = []
    if installed:
        for kind in ("podchaos", "networkchaos", "stresschaos", "dnschaos", "httpchaos"):
            code, out = _kubectl(
                "get", kind, "-n", _CHAOS_NS, "-l", _CHAOS_LABEL,
                "-o", "jsonpath={range .items[*]}{.metadata.name}{\\t}{.kind}{\\n}{end}",
            )
            if code == 0 and out.strip():
                for line in out.strip().splitlines():
                    parts = line.split("\t")
                    if parts:
                        active.append({"name": parts[0], "kind": parts[1] if len(parts) > 1 else kind})
    return {
        "installed": installed,
        "namespace": _CHAOS_NS,
        "active_experiments": active,
        "active_count": len(active),
    }


def clear_chaos_experiments(app: dict | None = None) -> list[str]:
    """Delete Enlight demo Chaos Mesh experiments (all apps or one app)."""
    deleted: list[str] = []
    kinds = ("podchaos", "networkchaos", "stresschaos", "dnschaos", "httpchaos", "iochaos")
    for kind in kinds:
        if app:
            name = _exp_name(app, kind.replace("chaos", ""))
            code, out = _kubectl(
                "delete", kind, name, "-n", _CHAOS_NS, "--ignore-not-found",
            )
            if code == 0 and "deleted" in (out or "").lower():
                deleted.append(f"{kind}/{name}")
        else:
            code, out = _kubectl(
                "delete", kind, "-n", _CHAOS_NS, "-l", _CHAOS_LABEL,
                "--ignore-not-found",
            )
            if code == 0 and out.strip():
                deleted.append(f"{kind} (label {_CHAOS_LABEL})")
    return deleted


def _apply_experiment(kind: str, app: dict, body: dict) -> str:
    if not chaos_mesh_installed():
        raise RuntimeError(
            "Chaos Mesh is not installed. Run: bash deploy/oci/setup-chaos-mesh.sh in Cloud Shell."
        )
    name = _exp_name(app, kind)
    doc = {
        "apiVersion": _CHAOS_API,
        "kind": kind,
        "metadata": {
            "name": name,
            "namespace": _CHAOS_NS,
            "labels": {_CHAOS_LABEL: "true", "enlight-lab/target-app": app["id"]},
        },
        "spec": body,
    }
    code, out = _kubectl_apply_yaml(json.dumps(doc))
    if code != 0:
        raise RuntimeError(_kubectl_value(code, out, f"Chaos Mesh {kind} apply failed"))
    return f"kubectl apply {kind}/{name} -n {_CHAOS_NS}"


def _base_selector(app: dict) -> dict:
    return {
        "mode": "one",
        "selector": {"labelSelectors": _label_selector(app)},
        "duration": "600s",
    }


def inject_pod_kill(app: dict) -> str:
    spec = {**_base_selector(app), "action": "pod-kill"}
    return _apply_experiment("PodChaos", app, spec)


def inject_network_delay(app: dict, *, latency: str = "3s") -> str:
    spec = {
        **_base_selector(app),
        "action": "delay",
        "delay": {"latency": latency, "jitter": "500ms"},
    }
    return _apply_experiment("NetworkChaos", app, spec)


def inject_network_loss(app: dict, *, loss: str = "80") -> str:
    spec = {
        **_base_selector(app),
        "action": "loss",
        "loss": {"loss": loss, "correlation": "25"},
    }
    return _apply_experiment("NetworkChaos", app, spec)


def inject_network_partition(app: dict) -> str:
    spec = {
        **_base_selector(app),
        "action": "partition",
        "direction": "both",
        "externalTargets": ["1.1.1.1", "8.8.8.8"],
    }
    return _apply_experiment("NetworkChaos", app, spec)


def inject_dns_chaos(app: dict) -> str:
    spec = {
        **_base_selector(app),
        "action": "error",
        "patterns": ["*.cluster.local", "*.svc.cluster.local"],
        "duration": "600s",
    }
    return _apply_experiment("DNSChaos", app, spec)


def inject_stress_cpu(app: dict) -> str:
    spec = {
        **_base_selector(app),
        "stressors": {"cpu": {"workers": 1, "load": 80}},
    }
    return _apply_experiment("StressChaos", app, spec)


def inject_stress_memory(app: dict) -> str:
    spec = {
        **_base_selector(app),
        "stressors": {"memory": {"workers": 1, "size": "128MB"}},
    }
    return _apply_experiment("StressChaos", app, spec)


def inject_http_abort(app: dict) -> str:
    port = 80 if "nginx" in app["deployment"] else 8000
    spec = {
        **_base_selector(app),
        "target": "Request",
        "port": port,
        "method": "GET",
        "path": "*",
        "abort": True,
        "statusCode": 500,
        "duration": "600s",
    }
    return _apply_experiment("HTTPChaos", app, spec)


def inject_http_delay(app: dict, *, delay: str = "4s") -> str:
    port = 80 if "nginx" in app["deployment"] else 8000
    spec = {
        **_base_selector(app),
        "target": "Request",
        "port": port,
        "method": "GET",
        "path": "*",
        "delay": delay,
        "duration": "600s",
    }
    return _apply_experiment("HTTPChaos", app, spec)


def wait_chaos_signal(app: dict, *, timeout: int = 45) -> bool:
    """Brief wait after chaos experiment apply."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        code, out = _kubectl(
            "get", "pods", "-n", cfg.NAMESPACE, "-l", app["pod_label"], "--no-headers",
        )
        if code == 0 and out.strip():
            return True
        time.sleep(3)
    return False
