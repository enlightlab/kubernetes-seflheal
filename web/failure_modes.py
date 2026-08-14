"""Kubernetes failure injection catalog — demo-safe recipes the AI agent can invoke."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable

import config as cfg

InjectFn = Callable[[dict], str]
ClearFn = Callable[[dict], None]


@dataclass(frozen=True)
class FailureMode:
    id: str
    label: str
    patterns: tuple[str, ...]
    inject: InjectFn
    clear: ClearFn | None = None
    category: str = "pod"
    tier: str = "kubectl"


def _kubectl(*args: str):
    from actions import _kubectl as k
    return k(*args)


def _kubectl_must(*args: str, action: str = "kubectl"):
    from actions import _kubectl_must as km
    return km(*args, action=action)


def _kubectl_value(code: int, out: str, fallback: str = "unknown") -> str:
    from actions import _kubectl_value as kv
    return kv(code, out, fallback)


def _patch_file(name: str, body: str):
    from actions import _patch_file as pf
    return pf(name, body)


def _restart_pods(app: dict) -> None:
    _kubectl(
        "delete", "pods", "-n", cfg.NAMESPACE, "-l", app["pod_label"],
        "--wait=false", "--force", "--grace-period=0",
    )


def _append_deployment_volume_mount(app: dict, volume: dict, mount: dict) -> None:
    """Add volume + mount even when deployment has no volumes/volumeMounts yet (e.g. fastapi)."""
    dep = _dep(app)
    code, raw = _kubectl("get", "deployment", dep, "-n", cfg.NAMESPACE, "-o", "json")
    if code != 0:
        raise RuntimeError(_kubectl_value(code, raw, f"deployment {dep} not found"))
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid deployment JSON for {dep}") from exc
    spec = doc.get("spec", {}).get("template", {}).get("spec") or {}
    vols = list(spec.get("volumes") or [])
    if not any(v.get("name") == volume.get("name") for v in vols):
        vols.append(volume)
    ctrs = spec.get("containers") or []
    if not ctrs:
        raise RuntimeError(f"deployment {dep} has no containers")
    mounts = list(ctrs[0].get("volumeMounts") or [])
    if not any(m.get("name") == mount.get("name") for m in mounts):
        mounts.append(mount)
    patch = _patch_file(
        f"vol-add-{dep}.json",
        json.dumps([
            {"op": "replace", "path": "/spec/template/spec/volumes", "value": vols},
            {"op": "replace", "path": "/spec/template/spec/containers/0/volumeMounts", "value": mounts},
        ]),
    )
    _kubectl_must(
        "patch", "deployment", dep, "-n", cfg.NAMESPACE,
        "--type", "json", f"--patch-file={patch}",
        action=f"Add volume mount on {dep}",
    )


def _dep(app: dict) -> str:
    return app["deployment"]


def _probe_port(app: dict) -> int:
    return 80 if "nginx" in _dep(app) else 8000


def _svc_name(app: dict) -> str:
    return app.get("service") or _dep(app)


def _np_name(app: dict) -> str:
    return f"enlight-deny-{ _dep(app) }"


# --- inject helpers ---

def _inject_image(app: dict) -> str:
    dep, ctr = _dep(app), app["container"]
    bad = app.get("bad_image") or cfg.BAD_IMAGE
    cmd = f"kubectl set image deployment/{dep} {ctr}={bad} -n {cfg.NAMESPACE}"
    _kubectl_must(
        "set", "image", f"deployment/{dep}", f"{ctr}={bad}",
        "-n", cfg.NAMESPACE, action="Inject bad image",
    )
    _restart_pods(app)
    return cmd


def _inject_crash(app: dict) -> str:
    dep = _dep(app)
    patch = _patch_file(
        f"crash-{dep}.json",
        '[{"op":"add","path":"/spec/template/spec/containers/0/command",'
        '"value":["/bin/sh","-c","exit 1"]},'
        '{"op":"replace","path":"/spec/template/spec/containers/0/args","value":[]}]',
    )
    code, out = _kubectl(
        "patch", "deployment", dep, "-n", cfg.NAMESPACE,
        "--type", "json", f"--patch-file={patch}",
    )
    if code != 0:
        raise RuntimeError(_kubectl_value(code, out, f"crash inject failed on {dep}"))
    _restart_pods(app)
    return f"kubectl patch deployment/{dep} -n {cfg.NAMESPACE} --type=json # crash"


def _inject_oom(app: dict) -> str:
    """Memory bomb with a schedulable limit — produces OOMKilled (exit 137), not FailedCreatePodSandBox."""
    dep = _dep(app)
    patch = _patch_file(
        f"oom-{dep}.json",
        json.dumps([
            {
                "op": "replace",
                "path": "/spec/template/spec/containers/0/command",
                "value": ["/bin/sh", "-c"],
            },
            {
                "op": "replace",
                "path": "/spec/template/spec/containers/0/args",
                "value": [
                    "while true; do dd if=/dev/zero of=/tmp/oom.bin bs=1M count=64 2>/dev/null; sleep 1; done",
                ],
            },
            {
                "op": "add",
                "path": "/spec/template/spec/containers/0/resources",
                "value": {
                    "limits": {"memory": "48Mi"},
                    "requests": {"memory": "32Mi", "cpu": "50m"},
                },
            },
        ]),
    )
    code, out = _kubectl(
        "patch", "deployment", dep, "-n", cfg.NAMESPACE,
        "--type", "json", f"--patch-file={patch}",
    )
    if code != 0:
        raise RuntimeError(_kubectl_value(code, out, f"OOM inject failed on {dep}"))
    _restart_pods(app)
    return f"kubectl patch deployment/{dep} -n {cfg.NAMESPACE} --type=json # oom memory bomb 48Mi"


def _inject_crash_oom_combo(app: dict) -> str:
    """Crash loop on main container + OOM sidecar — both signals visible in events."""
    dep = _dep(app)
    patch = _patch_file(
        f"crash-oom-{dep}.json",
        json.dumps([
            {
                "op": "replace",
                "path": "/spec/template/spec/containers/0/command",
                "value": ["/bin/sh", "-c"],
            },
            {
                "op": "replace",
                "path": "/spec/template/spec/containers/0/args",
                "value": ["exit 1"],
            },
            {
                "op": "add",
                "path": "/spec/template/spec/containers/0/resources",
                "value": {
                    "limits": {"memory": "128Mi", "cpu": "200m"},
                    "requests": {"memory": "64Mi", "cpu": "50m"},
                },
            },
            {
                "op": "add",
                "path": "/spec/template/spec/containers/-",
                "value": {
                    "name": "demo-oom-bomb",
                    "image": "busybox:1.36",
                    "command": ["sh", "-c"],
                    "args": [
                        "while true; do dd if=/dev/zero of=/dev/shm/bomb bs=1M count=64 2>/dev/null; sleep 1; done",
                    ],
                    "resources": {
                        "limits": {"memory": "48Mi"},
                        "requests": {"memory": "32Mi", "cpu": "25m"},
                    },
                },
            },
        ]),
    )
    _kubectl_must(
        "patch", "deployment", dep, "-n", cfg.NAMESPACE,
        "--type", "json", f"--patch-file={patch}",
        action="Crash loop + OOM sidecar",
    )
    _restart_pods(app)
    return f"kubectl patch deployment/{dep} -n {cfg.NAMESPACE} # crash+oom combo"


def _inject_instant(app: dict) -> str:
    dep = _dep(app)
    cmd = f"kubectl scale deployment/{dep} -n {cfg.NAMESPACE} --replicas=0"
    _kubectl_must("scale", f"deployment/{dep}", "-n", cfg.NAMESPACE, "--replicas=0", action="Scale to zero")
    return cmd


def _inject_pending(app: dict) -> str:
    dep = _dep(app)
    patch = _patch_file(
        f"pending-{dep}.json",
        '[{"op":"add","path":"/spec/template/spec/nodeSelector",'
        '"value":{"kubernetes.io/hostname":"node-does-not-exist-demo"}}]',
    )
    _kubectl_must(
        "patch", "deployment", dep, "-n", cfg.NAMESPACE,
        "--type", "json", f"--patch-file={patch}",
        action="Inject pending scheduling",
    )
    return f"kubectl patch deployment/{dep} -n {cfg.NAMESPACE} --type=json # nodeSelector"


def _inject_volume(app: dict) -> str:
    dep = _dep(app)
    _append_deployment_volume_mount(
        app,
        {"name": "demo-bad-vol", "secret": {"secretName": "enlight-volume-does-not-exist-demo"}},
        {"name": "demo-bad-vol", "mountPath": "/mnt/demo-bad-volume", "readOnly": True},
    )
    _restart_pods(app)
    return f"kubectl patch deployment/{dep} -n {cfg.NAMESPACE} # bad volumeMount"


def _inject_configmap(app: dict) -> str:
    dep = _dep(app)
    patch = _patch_file(
        f"cm-env-{dep}.json",
        '[{"op":"add","path":"/spec/template/spec/containers/0/envFrom",'
        '"value":[{"configMapRef":{"name":"enlight-configmap-does-not-exist-demo"}}]}]',
    )
    _kubectl_must(
        "patch", "deployment", dep, "-n", cfg.NAMESPACE,
        "--type", "json", f"--patch-file={patch}",
        action="Inject missing ConfigMap envFrom",
    )
    _restart_pods(app)
    return f"kubectl patch deployment/{dep} -n {cfg.NAMESPACE} --type=json # missing ConfigMap"


def _inject_secret_env(app: dict) -> str:
    dep = _dep(app)
    patch = _patch_file(
        f"secret-env-{dep}.json",
        '[{"op":"add","path":"/spec/template/spec/containers/0/env/-",'
        '"value":{"name":"DEMO_BAD_SECRET","valueFrom":{"secretKeyRef":'
        '{"name":"enlight-secret-does-not-exist-demo","key":"token"}}}}]',
    )
    _kubectl_must(
        "patch", "deployment", dep, "-n", cfg.NAMESPACE,
        "--type", "json", f"--patch-file={patch}",
        action="Inject missing Secret env",
    )
    _restart_pods(app)
    return f"kubectl patch deployment/{dep} -n {cfg.NAMESPACE} --type=json # missing Secret env"


def _inject_readiness(app: dict) -> str:
    dep, port = _dep(app), _probe_port(app)
    patch = _patch_file(
        f"ready-{dep}.json",
        json.dumps([{
            "op": "replace",
            "path": "/spec/template/spec/containers/0/readinessProbe",
            "value": {
                "httpGet": {"path": "/", "port": 31999},
                "initialDelaySeconds": 2,
                "periodSeconds": 3,
                "failureThreshold": 1,
            },
        }]),
    )
    _kubectl_must(
        "patch", "deployment", dep, "-n", cfg.NAMESPACE,
        "--type", "json", f"--patch-file={patch}",
        action="Inject failing readiness probe",
    )
    _restart_pods(app)
    return f"kubectl patch deployment/{dep} -n {cfg.NAMESPACE} # readinessProbe port {port}+31999"


def _pods_fully_ready(app: dict) -> bool:
    """True when every running pod has all containers Ready (Argo CD would show Healthy)."""
    code, out = _kubectl(
        "get", "pods", "-n", cfg.NAMESPACE, "-l", app["pod_label"], "-o", "json",
    )
    if code != 0 or not out.strip():
        return False
    try:
        items = json.loads(out).get("items") or []
    except json.JSONDecodeError:
        return False
    saw_pod = False
    for item in items:
        phase = (item.get("status", {}).get("phase") or "").lower()
        if phase in ("failed", "succeeded", "terminating"):
            continue
        saw_pod = True
        statuses = item.get("status", {}).get("containerStatuses") or []
        if not statuses:
            return False
        if not all(cs.get("ready") for cs in statuses):
            return False
    return saw_pod


def _ensure_argocd_visible_degradation(app: dict, modes: list[str]) -> str | None:
    """If pods still look Healthy, break readiness so Argo CD shows Degraded for clients."""
    if "instant" in modes:
        return None
    if not _pods_fully_ready(app):
        return None
    dep = _dep(app)
    log = _inject_readiness(app)
    _kubectl(
        "annotate", "deployment", dep, "-n", cfg.NAMESPACE,
        "enlight-lab/argocd-visible-outage=true",
        "--overwrite",
    )
    return f"{log} # Argo CD will show Degraded/Progressing"


def _inject_liveness(app: dict) -> str:
    dep = _dep(app)
    patch = _patch_file(
        f"live-{dep}.json",
        json.dumps([{
            "op": "replace",
            "path": "/spec/template/spec/containers/0/livenessProbe",
            "value": {
                "httpGet": {"path": "/healthz-does-not-exist", "port": 31998},
                "initialDelaySeconds": 3,
                "periodSeconds": 5,
                "failureThreshold": 1,
            },
        }]),
    )
    _kubectl_must(
        "patch", "deployment", dep, "-n", cfg.NAMESPACE,
        "--type", "json", f"--patch-file={patch}",
        action="Inject failing liveness probe",
    )
    _restart_pods(app)
    return f"kubectl patch deployment/{dep} -n {cfg.NAMESPACE} # livenessProbe failure"


def _inject_startup(app: dict) -> str:
    dep = _dep(app)
    patch = _patch_file(
        f"startup-{dep}.json",
        json.dumps([{
            "op": "add",
            "path": "/spec/template/spec/containers/0/startupProbe",
            "value": {
                "httpGet": {"path": "/", "port": 31997},
                "periodSeconds": 2,
                "failureThreshold": 1,
            },
        }]),
    )
    _kubectl_must(
        "patch", "deployment", dep, "-n", cfg.NAMESPACE,
        "--type", "json", f"--patch-file={patch}",
        action="Inject failing startup probe",
    )
    _restart_pods(app)
    return f"kubectl patch deployment/{dep} -n {cfg.NAMESPACE} # startupProbe failure"


def _inject_init_crash(app: dict) -> str:
    dep = _dep(app)
    patch = _patch_file(
        f"init-{dep}.json",
        json.dumps([{
            "op": "add",
            "path": "/spec/template/spec/initContainers",
            "value": [{
                "name": "demo-init-fail",
                "image": "busybox:1.36",
                "command": ["sh", "-c", "exit 1"],
            }],
        }]),
    )
    _kubectl_must(
        "patch", "deployment", dep, "-n", cfg.NAMESPACE,
        "--type", "json", f"--patch-file={patch}",
        action="Inject init container crash",
    )
    _restart_pods(app)
    return f"kubectl patch deployment/{dep} -n {cfg.NAMESPACE} # initContainer CrashLoop"


def _inject_cpu_throttle(app: dict) -> str:
    dep = _dep(app)
    patch = _patch_file(
        f"cpu-{dep}.json",
        '[{"op":"add","path":"/spec/template/spec/containers/0/resources",'
        '"value":{"limits":{"cpu":"1m"},"requests":{"cpu":"1m"}}}]',
    )
    _kubectl_must(
        "patch", "deployment", dep, "-n", cfg.NAMESPACE,
        "--type", "json", f"--patch-file={patch}",
        action="Inject extreme CPU limit",
    )
    _restart_pods(app)
    return f"kubectl patch deployment/{dep} -n {cfg.NAMESPACE} # CPU limit 1m (throttling)"


def _inject_affinity(app: dict) -> str:
    dep = _dep(app)
    patch = _patch_file(
        f"affinity-{dep}.json",
        json.dumps([{
            "op": "add",
            "path": "/spec/template/spec/affinity",
            "value": {
                "nodeAffinity": {
                    "requiredDuringSchedulingIgnoredDuringExecution": {
                        "nodeSelectorTerms": [{
                            "matchExpressions": [{
                                "key": "demo-failure-zone",
                                "operator": "In",
                                "values": ["zone-does-not-exist"],
                            }],
                        }],
                    },
                },
            },
        }]),
    )
    _kubectl_must(
        "patch", "deployment", dep, "-n", cfg.NAMESPACE,
        "--type", "json", f"--patch-file={patch}",
        action="Inject impossible node affinity",
    )
    return f"kubectl patch deployment/{dep} -n {cfg.NAMESPACE} # nodeAffinity unschedulable"


def _inject_hostpath(app: dict) -> str:
    dep = _dep(app)
    patch = _patch_file(
        f"hostpath-{dep}.json",
        '[{"op":"add","path":"/spec/template/spec/volumes/-",'
        '"value":{"name":"demo-bad-hostpath","hostPath":{"path":"/var/demo/path/does/not/exist","type":"Directory"}}},'
        '{"op":"add","path":"/spec/template/spec/containers/0/volumeMounts/-",'
        '"value":{"name":"demo-bad-hostpath","mountPath":"/mnt/host-bad"}}]',
    )
    _kubectl_must(
        "patch", "deployment", dep, "-n", cfg.NAMESPACE,
        "--type", "json", f"--patch-file={patch}",
        action="Inject invalid hostPath volume",
    )
    _restart_pods(app)
    return f"kubectl patch deployment/{dep} -n {cfg.NAMESPACE} # hostPath mount failure"


def _inject_bad_command(app: dict) -> str:
    dep = _dep(app)
    patch = _patch_file(
        f"cmd-{dep}.json",
        '[{"op":"replace","path":"/spec/template/spec/containers/0/command",'
        '"value":["/usr/bin/binary-does-not-exist-demo"]},'
        '{"op":"replace","path":"/spec/template/spec/containers/0/args","value":[]}]',
    )
    _kubectl_must(
        "patch", "deployment", dep, "-n", cfg.NAMESPACE,
        "--type", "json", f"--patch-file={patch}",
        action="Inject invalid container command",
    )
    _restart_pods(app)
    return f"kubectl patch deployment/{dep} -n {cfg.NAMESPACE} # command not found"


def _inject_privileged(app: dict) -> str:
    """Security context that may fail on restricted clusters — RunContainerError / forbidden."""
    dep = _dep(app)
    patch = _patch_file(
        f"priv-{dep}.json",
        json.dumps([{
            "op": "add",
            "path": "/spec/template/spec/containers/0/securityContext",
            "value": {"privileged": True},
        }]),
    )
    _kubectl_must(
        "patch", "deployment", dep, "-n", cfg.NAMESPACE,
        "--type", "json", f"--patch-file={patch}",
        action="Inject privileged securityContext",
    )
    _restart_pods(app)
    return f"kubectl patch deployment/{dep} -n {cfg.NAMESPACE} # privileged / PSA denial"


def _inject_service_selector(app: dict) -> str:
    svc = _svc_name(app)
    patch = _patch_file(
        f"svc-sel-{svc}.json",
        json.dumps([{
            "op": "replace",
            "path": "/spec/selector",
            "value": {"app": "enlight-demo-selector-does-not-exist"},
        }]),
    )
    _kubectl_must(
        "patch", "service", svc, "-n", cfg.NAMESPACE,
        "--type", "json", f"--patch-file={patch}",
        action="Break service selector",
    )
    return f"kubectl patch service/{svc} -n {cfg.NAMESPACE} # selector mismatch"


def _inject_port_mismatch(app: dict) -> str:
    svc = _svc_name(app)
    patch = _patch_file(
        f"svc-port-{svc}.json",
        json.dumps([{"op": "replace", "path": "/spec/ports/0/targetPort", "value": 31999}]),
    )
    _kubectl_must(
        "patch", "service", svc, "-n", cfg.NAMESPACE,
        "--type", "json", f"--patch-file={patch}",
        action="Wrong service targetPort",
    )
    return f"kubectl patch service/{svc} -n {cfg.NAMESPACE} # targetPort 31999"


def _inject_network_policy(app: dict) -> str:
    dep = _dep(app)
    key, _, val = app["pod_label"].partition("=")
    name = _np_name(app)
    manifest = f"""apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: {name}
  namespace: {cfg.NAMESPACE}
spec:
  podSelector:
    matchLabels:
      {key}: {val}
  policyTypes:
    - Ingress
    - Egress
  ingress: []
  egress: []
"""
    code, out = _kubectl_apply_yaml(manifest)
    if code != 0:
        raise RuntimeError(_kubectl_value(code, out, f"NetworkPolicy inject failed on {dep}"))
    _restart_pods(app)
    return f"kubectl apply NetworkPolicy/{name} -n {cfg.NAMESPACE} # deny all traffic"


def _kubectl_apply_yaml(yaml_text: str) -> tuple[int, str]:
    from actions import _kubectl_apply_yaml as apply
    return apply(yaml_text)


def _inject_ingress_bad(app: dict) -> str:
    ing = _dep(app)
    code, _ = _kubectl("get", "ingress", ing, "-n", cfg.NAMESPACE)
    if code != 0:
        raise RuntimeError(f"No Ingress {ing} — ingress_bad applies to nginx only")
    patch = _patch_file(
        f"ing-{ing}.json",
        json.dumps([{
            "op": "replace",
            "path": "/spec/rules/0/http/paths/0/backend/service/name",
            "value": "enlight-service-does-not-exist",
        }]),
    )
    _kubectl_must(
        "patch", "ingress", ing, "-n", cfg.NAMESPACE,
        "--type", "json", f"--patch-file={patch}",
        action="Ingress wrong backend",
    )
    return f"kubectl patch ingress/{ing} -n {cfg.NAMESPACE} # bad backend service"


def _inject_bad_rollout(app: dict) -> str:
    dep, ctr = _dep(app), app["container"]
    bad = app.get("bad_image") or cfg.BAD_IMAGE
    _kubectl_must(
        "scale", f"deployment/{dep}", "-n", cfg.NAMESPACE, "--replicas=2",
        action="Scale to 2 for bad rollout",
    )
    _kubectl_must(
        "set", "image", f"deployment/{dep}", f"{ctr}={bad}",
        "-n", cfg.NAMESPACE, action="Bad image during rollout",
    )
    _restart_pods(app)
    return f"kubectl scale + set image deployment/{dep} -n {cfg.NAMESPACE} # bad rollout"


def _inject_rollout_stuck(app: dict) -> str:
    dep = _dep(app)
    _kubectl_must(
        "rollout", "pause", f"deployment/{dep}", "-n", cfg.NAMESPACE,
        action="Pause deployment rollout",
    )
    return f"kubectl rollout pause deployment/{dep} -n {cfg.NAMESPACE}"


def _pvc_pending_name(app: dict) -> str:
    return f"enlight-pvc-pending-{_dep(app)}"


def _ensure_pending_pvc(app: dict) -> str:
    """PVC that never binds — storageClassName does not exist."""
    pvc_name = _pvc_pending_name(app)
    manifest = (
        f"apiVersion: v1\n"
        f"kind: PersistentVolumeClaim\n"
        f"metadata:\n"
        f"  name: {pvc_name}\n"
        f"  namespace: {cfg.NAMESPACE}\n"
        f"spec:\n"
        f"  accessModes:\n"
        f"    - ReadWriteOnce\n"
        f"  storageClassName: enlight-storage-does-not-exist\n"
        f"  resources:\n"
        f"    requests:\n"
        f"      storage: 1Gi\n"
    )
    path = _patch_file(f"pvc-create-{pvc_name}.yaml", manifest)
    _kubectl_must("apply", "-f", str(path), action=f"Create pending PVC {pvc_name}")
    return pvc_name


def _inject_pvc_pending(app: dict) -> str:
    dep = _dep(app)
    pvc_name = _ensure_pending_pvc(app)
    _append_deployment_volume_mount(
        app,
        {"name": "demo-pvc-pending", "persistentVolumeClaim": {"claimName": pvc_name}},
        {"name": "demo-pvc-pending", "mountPath": "/mnt/pvc-demo"},
    )
    _restart_pods(app)
    return f"kubectl patch deployment/{dep} -n {cfg.NAMESPACE} # unbound PVC {pvc_name}"


def _inject_readonly_root(app: dict) -> str:
    dep = _dep(app)
    patch = _patch_file(
        f"rofs-{dep}.json",
        json.dumps([{
            "op": "add",
            "path": "/spec/template/spec/containers/0/securityContext",
            "value": {"readOnlyRootFilesystem": True},
        }]),
    )
    _kubectl_must(
        "patch", "deployment", dep, "-n", cfg.NAMESPACE,
        "--type", "json", f"--patch-file={patch}",
        action="Read-only root filesystem",
    )
    _restart_pods(app)
    return f"kubectl patch deployment/{dep} -n {cfg.NAMESPACE} # readOnlyRootFilesystem"


def _inject_deadlock(app: dict) -> str:
    dep = _dep(app)
    patch = _patch_file(
        f"hang-{dep}.json",
        json.dumps([
            {
                "op": "replace",
                "path": "/spec/template/spec/containers/0/command",
                "value": ["/bin/sh", "-c", "sleep 86400"],
            },
            {"op": "replace", "path": "/spec/template/spec/containers/0/args", "value": []},
            {
                "op": "replace",
                "path": "/spec/template/spec/containers/0/livenessProbe",
                "value": {
                    "exec": {"command": ["sh", "-c", "exit 1"]},
                    "initialDelaySeconds": 15,
                    "periodSeconds": 10,
                    "failureThreshold": 1,
                },
            },
        ]),
    )
    _kubectl_must(
        "patch", "deployment", dep, "-n", cfg.NAMESPACE,
        "--type", "json", f"--patch-file={patch}",
        action="Deadlock / hang + liveness kill",
    )
    _restart_pods(app)
    return f"kubectl patch deployment/{dep} -n {cfg.NAMESPACE} # hang + liveness"


def _inject_memory_leak(app: dict) -> str:
    dep = _dep(app)
    patch = _patch_file(
        f"leak-{dep}.json",
        json.dumps([
            {
                "op": "replace",
                "path": "/spec/template/spec/containers/0/command",
                "value": ["/bin/sh", "-c"],
            },
            {
                "op": "replace",
                "path": "/spec/template/spec/containers/0/args",
                "value": [
                    "while true; do dd if=/dev/zero of=/tmp/leak.bin bs=1M count=8 2>/dev/null; sleep 2; done",
                ],
            },
            {
                "op": "add",
                "path": "/spec/template/spec/containers/0/resources",
                "value": {"limits": {"memory": "48Mi"}, "requests": {"memory": "32Mi"}},
            },
        ]),
    )
    _kubectl_must(
        "patch", "deployment", dep, "-n", cfg.NAMESPACE,
        "--type", "json", f"--patch-file={patch}",
        action="Memory leak simulation",
    )
    _restart_pods(app)
    return f"kubectl patch deployment/{dep} -n {cfg.NAMESPACE} # memory leak loop"


def _inject_cpu_stress(app: dict) -> str:
    """CPU pressure without unschedulable resource requests (demo OKE has ~2 small nodes)."""
    dep = _dep(app)
    patch = _patch_file(
        f"stress-{dep}.json",
        json.dumps([{
            "op": "add",
            "path": "/spec/template/spec/containers/-",
            "value": {
                "name": "demo-cpu-stress",
                "image": "busybox:1.36",
                "command": ["sh", "-c", "while true; do :; done"],
                "resources": {
                    "limits": {"cpu": "1000m", "memory": "64Mi"},
                    "requests": {"cpu": "50m", "memory": "32Mi"},
                },
            },
        }]),
    )
    _kubectl_must(
        "patch", "deployment", dep, "-n", cfg.NAMESPACE,
        "--type", "json", f"--patch-file={patch}",
        action="CPU stress sidecar",
    )
    _restart_pods(app)
    return f"kubectl patch deployment/{dep} -n {cfg.NAMESPACE} # cpu stress sidecar"


def _inject_dns_failure_kubectl(app: dict) -> str:
    """Break DNS inside the pod without Chaos Mesh — bad nameserver."""
    dep = _dep(app)
    patch = _patch_file(
        f"dns-bad-{dep}.json",
        json.dumps([
            {"op": "replace", "path": "/spec/template/spec/dnsPolicy", "value": "None"},
            {
                "op": "add",
                "path": "/spec/template/spec/dnsConfig",
                "value": {
                    "nameservers": ["203.0.113.1"],
                    "searches": ["svc.cluster.local", "cluster.local"],
                    "options": [{"name": "ndots", "value": "1"}],
                },
            },
        ]),
    )
    _kubectl_must(
        "patch", "deployment", dep, "-n", cfg.NAMESPACE,
        "--type", "json", f"--patch-file={patch}",
        action="DNS failure (bad nameserver)",
    )
    _restart_pods(app)
    return f"kubectl patch deployment/{dep} -n {cfg.NAMESPACE} # dnsPolicy=None bad DNS"


def _inject_http_500(app: dict) -> str:
    dep = _dep(app)
    if "nginx" in dep:
        patch = _patch_file(
            "cm-500-nginx.json",
            json.dumps({
                "data": {
                    "index.html": (
                        "<!DOCTYPE html><html><head><title>500</title></head>"
                        "<body><h1>500 Internal Server Error</h1>"
                        "<p>Demo chaos — HTTP 500 flood simulation.</p></body></html>"
                    ),
                },
            }),
        )
        _kubectl_must(
            "patch", "configmap", "nginx-demo-site", "-n", cfg.NAMESPACE,
            "--type", "merge", f"--patch-file={patch}",
            action="Nginx 500 error page",
        )
        probe_patch = _patch_file(
            f"http500-probe-{dep}.json",
            json.dumps([{
                "op": "replace",
                "path": "/spec/template/spec/containers/0/readinessProbe",
                "value": {
                    "httpGet": {"path": "/returns-500-demo", "port": 80},
                    "initialDelaySeconds": 3,
                    "periodSeconds": 3,
                    "failureThreshold": 1,
                },
            }]),
        )
        _kubectl_must(
            "patch", "deployment", dep, "-n", cfg.NAMESPACE,
            "--type", "json", f"--patch-file={probe_patch}",
            action="Nginx failing readiness (HTTP 500 sim)",
        )
        _kubectl(
            "annotate", "deployment", dep, "-n", cfg.NAMESPACE,
            "demo.enlight/http500=true", "--overwrite",
        )
        _restart_pods(app)
        return "kubectl patch configmap/nginx-demo-site + failing readiness # HTTP 500"
    patch = _patch_file(
        f"http500-{dep}.json",
        json.dumps([{
            "op": "replace",
            "path": "/spec/template/spec/containers/0/readinessProbe",
            "value": {
                "httpGet": {"path": "/returns-500-demo", "port": _probe_port(app)},
                "initialDelaySeconds": 3,
                "periodSeconds": 3,
                "failureThreshold": 1,
            },
        }]),
    )
    _kubectl_must(
        "patch", "deployment", dep, "-n", cfg.NAMESPACE,
        "--type", "json", f"--patch-file={patch}",
        action="HTTP 500 / unreachable endpoint probe",
    )
    _restart_pods(app)
    return f"kubectl patch deployment/{dep} -n {cfg.NAMESPACE} # probe on failing path"


def _inject_high_latency(app: dict) -> str:
    """Simulate slow responses via long readiness delay (app chaos tier)."""
    dep = _dep(app)
    patch = _patch_file(
        f"latency-{dep}.json",
        json.dumps([{
            "op": "replace",
            "path": "/spec/template/spec/containers/0/readinessProbe",
            "value": {
                "httpGet": {"path": "/", "port": _probe_port(app)},
                "initialDelaySeconds": 120,
                "periodSeconds": 30,
                "timeoutSeconds": 25,
                "failureThreshold": 10,
            },
        }]),
    )
    _kubectl_must(
        "patch", "deployment", dep, "-n", cfg.NAMESPACE,
        "--type", "json", f"--patch-file={patch}",
        action="High latency (slow readiness)",
    )
    _restart_pods(app)
    return f"kubectl patch deployment/{dep} -n {cfg.NAMESPACE} # high latency probe"


# --- Chaos Mesh injectors (accurate network/DNS/stress — fallback to kubectl if not installed) ---

def _inject_dns_failure(app: dict) -> str:
    try:
        import chaos_mesh as cm
        if cm.chaos_mesh_installed():
            return cm.inject_dns_chaos(app)
    except Exception:
        pass
    return _inject_dns_failure_kubectl(app)


def _inject_network_delay_kubectl(app: dict) -> str:
    return _inject_high_latency(app)


def _inject_network_delay_chaos(app: dict) -> str:
    try:
        import chaos_mesh as cm
        if cm.chaos_mesh_installed():
            return cm.inject_network_delay(app, latency="4s")
    except Exception:
        pass
    return _inject_high_latency(app)


def _inject_network_loss_chaos(app: dict) -> str:
    try:
        import chaos_mesh as cm
        if cm.chaos_mesh_installed():
            return cm.inject_network_loss(app)
    except Exception:
        pass
    return _inject_network_policy(app)


def _inject_network_partition_chaos(app: dict) -> str:
    try:
        import chaos_mesh as cm
        if cm.chaos_mesh_installed():
            return cm.inject_network_partition(app)
    except Exception:
        pass
    return _inject_network_policy(app)


def _inject_pod_kill_chaos(app: dict) -> str:
    try:
        import chaos_mesh as cm
        if cm.chaos_mesh_installed():
            return cm.inject_pod_kill(app)
    except Exception:
        pass
    return _inject_crash(app)


def _inject_stress_cpu_chaos(app: dict) -> str:
    try:
        import chaos_mesh as cm
        if cm.chaos_mesh_installed():
            return cm.inject_stress_cpu(app)
    except Exception:
        pass
    return _inject_cpu_stress(app)


def _inject_stress_memory_chaos(app: dict) -> str:
    try:
        import chaos_mesh as cm
        if cm.chaos_mesh_installed():
            return cm.inject_stress_memory(app)
    except Exception:
        pass
    return _inject_memory_leak(app)


def _inject_http_abort_chaos(app: dict) -> str:
    try:
        import chaos_mesh as cm
        if cm.chaos_mesh_installed():
            return cm.inject_http_abort(app)
    except Exception:
        pass
    return _inject_http_500(app)


def _inject_http_delay_chaos(app: dict) -> str:
    try:
        import chaos_mesh as cm
        if cm.chaos_mesh_installed():
            return cm.inject_http_delay(app, delay="5s")
    except Exception:
        pass
    return _inject_high_latency(app)


def _clear_chaos(app: dict) -> None:
    try:
        import chaos_mesh as cm
        cm.clear_chaos_experiments(app)
    except Exception:
        pass


def _inject_toleration_mismatch(app: dict) -> str:
    dep = _dep(app)
    patch = _patch_file(
        f"taint-{dep}.json",
        json.dumps([
            {
                "op": "add",
                "path": "/spec/template/spec/tolerations",
                "value": [{
                    "key": "demo-enlight-no-node",
                    "operator": "Exists",
                    "effect": "NoSchedule",
                }],
            },
            {
                "op": "add",
                "path": "/spec/template/spec/nodeSelector",
                "value": {"kubernetes.io/hostname": "node-with-demo-taint-only"},
            },
        ]),
    )
    _kubectl_must(
        "patch", "deployment", dep, "-n", cfg.NAMESPACE,
        "--type", "json", f"--patch-file={patch}",
        action="Taint / node selector mismatch",
    )
    return f"kubectl patch deployment/{dep} -n {cfg.NAMESPACE} # toleration + nodeSelector"


# --- clear helpers ---

def _patch_remove_paths(app: dict, paths: list[str]) -> None:
    dep = _dep(app)
    ops = [{"op": "remove", "path": p} for p in paths]
    patch = _patch_file(f"clear-{dep}-{'-'.join(p.replace('/','') for p in paths)}.json", json.dumps(ops))
    _kubectl(
        "patch", "deployment", dep, "-n", cfg.NAMESPACE,
        "--type", "json", f"--patch-file={patch}",
    )


def _clear_crash(app: dict) -> None:
    _patch_remove_paths(app, [
        "/spec/template/spec/containers/0/command",
        "/spec/template/spec/containers/0/args",
    ])


def _clear_resources(app: dict) -> None:
    _patch_remove_paths(app, ["/spec/template/spec/containers/0/resources"])


def _clear_node_selector(app: dict) -> None:
    _patch_remove_paths(app, ["/spec/template/spec/nodeSelector"])


def _clear_affinity(app: dict) -> None:
    _patch_remove_paths(app, ["/spec/template/spec/affinity"])


def _clear_probes(app: dict) -> None:
    for p in (
        "/spec/template/spec/containers/0/readinessProbe",
        "/spec/template/spec/containers/0/livenessProbe",
        "/spec/template/spec/containers/0/startupProbe",
    ):
        _patch_remove_paths(app, [p])


def _clear_init(app: dict) -> None:
    _patch_remove_paths(app, ["/spec/template/spec/initContainers"])


def _clear_security(app: dict) -> None:
    _patch_remove_paths(app, ["/spec/template/spec/containers/0/securityContext"])


def _clear_envfrom(app: dict) -> None:
    _patch_remove_paths(app, ["/spec/template/spec/containers/0/envFrom"])


def _clear_toleration(app: dict) -> None:
    _patch_remove_paths(app, [
        "/spec/template/spec/tolerations",
        "/spec/template/spec/nodeSelector",
    ])


def _clear_service(app: dict) -> None:
    svc = _svc_name(app)
    key, _, val = app["pod_label"].partition("=")
    target = _probe_port(app)
    patch = _patch_file(
        f"svc-restore-{svc}.json",
        json.dumps([
            {"op": "replace", "path": "/spec/selector", "value": {key: val}},
            {"op": "replace", "path": "/spec/ports/0/targetPort", "value": target},
        ]),
    )
    _kubectl(
        "patch", "service", svc, "-n", cfg.NAMESPACE,
        "--type", "json", f"--patch-file={patch}",
    )


def _clear_network_policy(app: dict) -> None:
    _kubectl(
        "delete", "networkpolicy", _np_name(app),
        "-n", cfg.NAMESPACE, "--ignore-not-found",
    )


def _clear_ingress(app: dict) -> None:
    ing = _dep(app)
    code, _ = _kubectl("get", "ingress", ing, "-n", cfg.NAMESPACE)
    if code != 0:
        return
    patch = _patch_file(
        f"ing-restore-{ing}.json",
        json.dumps([{
            "op": "replace",
            "path": "/spec/rules/0/http/paths/0/backend/service/name",
            "value": ing,
        }]),
    )
    _kubectl(
        "patch", "ingress", ing, "-n", cfg.NAMESPACE,
        "--type", "json", f"--patch-file={patch}",
    )


def _clear_rollout_pause(app: dict) -> None:
    _kubectl(
        "rollout", "resume", f"deployment/{_dep(app)}", "-n", cfg.NAMESPACE,
    )


def _clear_stress_sidecar(app: dict) -> None:
    dep = _dep(app)
    code, raw = _kubectl("get", "deployment", dep, "-n", cfg.NAMESPACE, "-o", "json")
    if code != 0:
        return
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError:
        return
    ctrs = [
        c for c in (doc.get("spec", {}).get("template", {}).get("spec", {}).get("containers") or [])
        if c.get("name") not in ("demo-cpu-stress", "demo-oom-bomb")
    ]
    patch = _patch_file(
        f"clear-stress-{dep}.json",
        json.dumps([{"op": "replace", "path": "/spec/template/spec/containers", "value": ctrs}]),
    )
    _kubectl(
        "patch", "deployment", dep, "-n", cfg.NAMESPACE,
        "--type", "json", f"--patch-file={patch}",
    )


def _clear_dns_config(app: dict) -> None:
    dep = _dep(app)
    code, raw = _kubectl("get", "deployment", dep, "-n", cfg.NAMESPACE, "-o", "json")
    if code != 0:
        return
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError:
        return
    spec = doc.get("spec", {}).get("template", {}).get("spec", {})
    ops: list[dict] = []
    if "dnsConfig" in spec:
        ops.append({"op": "remove", "path": "/spec/template/spec/dnsConfig"})
    if spec.get("dnsPolicy") == "None":
        ops.append({"op": "replace", "path": "/spec/template/spec/dnsPolicy", "value": "ClusterFirst"})
    if not ops:
        return
    patch = _patch_file(f"clear-dns-{dep}.json", json.dumps(ops))
    _kubectl(
        "patch", "deployment", dep, "-n", cfg.NAMESPACE,
        "--type", "json", f"--patch-file={patch}",
    )


def _stamp_injected_modes(app: dict, modes: list[str]) -> None:
    if not modes:
        return
    dep = _dep(app)
    joined = ",".join(modes)[:240]
    _kubectl(
        "annotate", "deployment", dep, "-n", cfg.NAMESPACE,
        f"enlight-lab/injected-modes={joined}",
        "enlight-lab/injected-by=enlight-selfheal",
        "--overwrite",
    )


def _clear_injection_stamp(app: dict) -> None:
    dep = _dep(app)
    _kubectl(
        "annotate", "deployment", dep, "-n", cfg.NAMESPACE,
        "enlight-lab/injected-modes-", "enlight-lab/injected-by-",
        "enlight-lab/argocd-visible-outage-",
        "--overwrite",
    )


def _clear_pvc_volume(app: dict) -> None:
    dep = _dep(app)
    pvc_name = _pvc_pending_name(app)
    _kubectl(
        "delete", "pvc", pvc_name, "-n", cfg.NAMESPACE,
        "--ignore-not-found", "--wait=false",
    )
    code, raw = _kubectl("get", "deployment", dep, "-n", cfg.NAMESPACE, "-o", "json")
    if code != 0:
        return
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError:
        return
    spec = doc.get("spec", {}).get("template", {}).get("spec", {})
    vols = [v for v in (spec.get("volumes") or []) if v.get("name") != "demo-pvc-pending"]
    ctrs = spec.get("containers") or [{}]
    mounts = [m for m in (ctrs[0].get("volumeMounts") or []) if m.get("name") != "demo-pvc-pending"]
    patch = _patch_file(
        f"clear-pvc-{dep}.json",
        json.dumps([
            {"op": "replace", "path": "/spec/template/spec/volumes", "value": vols},
            {"op": "replace", "path": "/spec/template/spec/containers/0/volumeMounts", "value": mounts},
        ]),
    )
    _kubectl(
        "patch", "deployment", dep, "-n", cfg.NAMESPACE,
        "--type", "json", f"--patch-file={patch}",
    )


def _clear_demo_volumes(app: dict) -> None:
    dep = _dep(app)
    code, raw = _kubectl("get", "deployment", dep, "-n", cfg.NAMESPACE, "-o", "json")
    if code != 0:
        return
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError:
        return
    spec = doc.get("spec", {}).get("template", {}).get("spec", {})
    demo_vol_names = {"demo-bad-vol", "demo-bad-hostpath", "demo-pvc-pending"}
    vols = [v for v in (spec.get("volumes") or []) if v.get("name") not in demo_vol_names]
    ctrs = spec.get("containers") or [{}]
    mounts = [
        m for m in (ctrs[0].get("volumeMounts") or [])
        if m.get("name") not in demo_vol_names
    ]
    envs = [
        e for e in (ctrs[0].get("env") or [])
        if e.get("name") != "DEMO_BAD_SECRET"
    ]
    patch = _patch_file(
        f"clear-vols-{dep}.json",
        json.dumps([
            {"op": "replace", "path": "/spec/template/spec/volumes", "value": vols},
            {"op": "replace", "path": "/spec/template/spec/containers/0/volumeMounts", "value": mounts},
            {"op": "replace", "path": "/spec/template/spec/containers/0/env", "value": envs},
        ]),
    )
    _kubectl(
        "patch", "deployment", dep, "-n", cfg.NAMESPACE,
        "--type", "json", f"--patch-file={patch}",
    )


# Order matters — first match wins when classifying natural language.
FAILURE_MODES: tuple[FailureMode, ...] = (
    # Pod-level
    FailureMode("init", "InitContainerCrashLoopBackOff",
                (r"\binit\s*container", r"\binit crash", r"\binit.?container fail"),
                _inject_init_crash, _clear_init, "pod"),
    FailureMode("startup", "Startup probe failure",
                (r"\bstartup\s*probe", r"\bstartup fail"),
                _inject_startup, _clear_probes, "pod"),
    FailureMode("readiness", "Readiness probe failure (pod not Ready)",
                (r"\breadiness\s*probe", r"\breadiness fail", r"\bnot ready\b", r"\b0/1\s*ready"),
                _inject_readiness, _clear_probes, "pod"),
    FailureMode("liveness", "Liveness probe failure / restart loop",
                (r"\bliveness\s*probe", r"\bliveness fail", r"\bhealth\s*check fail"),
                _inject_liveness, _clear_probes, "pod"),
    FailureMode("bad_command", "RunContainerError / command not found",
                (r"\bcommand not found", r"\bbad command", r"\bwrong command", r"\bexec format",
                 r"\bruncontainererror", r"\brun\s*container\s*error"),
                _inject_bad_command, _clear_crash, "pod"),
    FailureMode("privileged", "Security policy / privileged container denied",
                (r"\bprivileged", r"\bpsa\b", r"\bpod security", r"\bsecurity policy",
                 r"\bseccomp"),
                _inject_privileged, _clear_security, "pod"),
    FailureMode("oom", "OOMKilled (memory limit)",
                (r"\boom\b", r"\bout of memory", r"\bmemory limit", r"\boomkilled"),
                _inject_oom, _clear_resources, "pod"),
    FailureMode("cpu_throttle", "CPU throttling (extreme CPU limit)",
                (r"\bcpu throttl", r"\bcpu limit", r"\bthrottl"),
                _inject_cpu_throttle, _clear_resources, "pod"),
    FailureMode("crash", "CrashLoopBackOff",
                (r"\bcrash\s*loop", r"\bcrashloop", r"\bkeeps crashing", r"\bexit 1"),
                _inject_crash, _clear_crash, "pod"),
    FailureMode("image", "Image pull failure",
                (r"\berrimage", r"\bimage\s*pull", r"\bpull back", r"\bbad image", r"\binvalid image",
                 r"\bimagepull", r"\bimagpull", r"\btag not found", r"\bprivate registry"),
                _inject_image, None, "pod"),
    FailureMode("deadlock", "Deadlock / hang (liveness kills pod)",
                (r"\bdeadlock", r"\bhang", r"\bfrozen", r"\bunresponsive"),
                _inject_deadlock, _clear_crash, "pod"),
    # Deployment-level
    FailureMode("instant", "Zero replicas / scaled to zero",
                (r"\bscale.*0", r"\bzero replica", r"\bscaled to 0", r"\bno pods", r"\bgoes dark"),
                _inject_instant, None, "deployment"),
    FailureMode("configmap", "Wrong / missing ConfigMap",
                (r"\bconfigmap", r"\bconfig map", r"\bmissing config", r"\bwrong config"),
                _inject_configmap, _clear_envfrom, "deployment"),
    FailureMode("secret_env", "Wrong / missing Secret",
                (r"\bsecret\s*env", r"\bsecret\s*ref", r"\bmissing secret", r"\bwrong secret",
                 r"\bdb password", r"\bapi key"),
                _inject_secret_env, _clear_demo_volumes, "deployment"),
    FailureMode("bad_rollout", "Bad rollout (new pods crash)",
                (r"\bbad rollout", r"\brollout fail", r"\bnew version crash", r"\bbuggy deploy"),
                _inject_bad_rollout, _clear_crash, "deployment"),
    FailureMode("rollout_stuck", "Rollout stuck / paused",
                (r"\brollout stuck", r"\brollout pause", r"\bmid.?update", r"\bcoexist"),
                _inject_rollout_stuck, _clear_rollout_pause, "deployment"),
    # Network-level
    FailureMode("service_selector", "Service unreachable (selector mismatch)",
                (r"\bservice unreachable", r"\bselector mismatch", r"\bno endpoints",
                 r"\bservice.*down"),
                _inject_service_selector, _clear_service, "network"),
    FailureMode("port_mismatch", "Port mismatch (service → wrong targetPort)",
                (r"\bport mismatch", r"\bwrong port", r"\btargetport", r"\bcontainer port"),
                _inject_port_mismatch, _clear_service, "network"),
    FailureMode("network_policy", "NetworkPolicy blocks all traffic",
                (r"\bnetwork\s*policy", r"\bfirewall", r"\btraffic block", r"\bdeny.*traffic"),
                _inject_network_policy, _clear_network_policy, "network"),
    FailureMode("ingress_bad", "Ingress misconfiguration (wrong backend)",
                (r"\bingress", r"\bexternal traffic", r"\brouting fail", r"\bwrong backend"),
                _inject_ingress_bad, _clear_ingress, "network"),
    # Node-level
    FailureMode("pending", "Pending forever (impossible nodeSelector)",
                (r"\bpending forever", r"\bnot enough cpu", r"\bnot enough ram",
                 r"\bcan.?t schedule", r"\bpending\b"),
                _inject_pending, _clear_node_selector, "node"),
    FailureMode("affinity", "Node affinity / unschedulable",
                (r"\bnode affinity", r"\banti.?affinity", r"\baffinity fail"),
                _inject_affinity, _clear_affinity, "node"),
    FailureMode("toleration", "Taint toleration mismatch",
                (r"\btaint", r"\btoleration", r"\bno toleration"),
                _inject_toleration_mismatch, _clear_toleration, "node"),
    # Storage-level
    FailureMode("volume", "Volume mount failure",
                (r"\bvolume\s*mount", r"\bvolumemount", r"\bmount failure", r"\bfailed mount",
                 r"\bcreatecontainerconfigerror"),
                _inject_volume, _clear_demo_volumes, "storage"),
    FailureMode("hostpath", "HostPath volume failure",
                (r"\bhostpath", r"\bhost\s*path", r"\bhost volume"),
                _inject_hostpath, _clear_demo_volumes, "storage"),
    FailureMode("pvc_pending", "PVC pending (volume never binds)",
                (r"\bpvc pending", r"\bpersistent volume", r"\bvolume claim", r"\bunbound pvc"),
                _inject_pvc_pending, _clear_pvc_volume, "storage"),
    FailureMode("readonly_root", "Read-only volume / filesystem",
                (r"\bread.?only", r"\bpermission denied", r"\bro filesystem"),
                _inject_readonly_root, _clear_security, "storage"),
    # Application chaos (demo-safe)
    FailureMode("memory_leak", "Memory leak simulation",
                (r"\bmemory leak", r"\bleak sim", r"\bslowly consume"),
                _inject_memory_leak, _clear_crash, "application"),
    FailureMode("cpu_stress", "CPU spike / stress",
                (r"\bcpu spike", r"\bcpu stress", r"\bstress test", r"\bmax.*cpu"),
                _inject_cpu_stress, _clear_stress_sidecar, "application"),
    FailureMode("http_500", "HTTP 500 errors",
                (r"\bhttp 500", r"\b5xx", r"\b500 flood", r"\binternal server error"),
                _inject_http_500, _clear_probes, "application"),
    FailureMode("high_latency", "High latency / slow responses",
                (r"\bhigh latency", r"\bslow response", r"\blatency inject", r"\b2.?5s delay"),
                _inject_high_latency, _clear_probes, "application"),
    # Chaos Mesh (accurate network/DNS/HTTP — falls back to kubectl if not installed)
    FailureMode("dns_failure", "DNS failure / name resolution broken",
                (r"\bdns failure", r"\bcoredns", r"\bdns chaos", r"\bname resolution",
                 r"\bdns broken"),
                _inject_dns_failure, _clear_chaos, "network", "chaos_mesh"),
    FailureMode("network_delay", "Network latency injection (Chaos Mesh)",
                (r"\bnetwork delay", r"\blatency injection", r"\b3s delay", r"\b4s delay",
                 r"\bslow network"),
                _inject_network_delay_chaos, _clear_chaos, "network", "chaos_mesh"),
    FailureMode("network_loss", "Packet loss (Chaos Mesh)",
                (r"\bpacket loss", r"\bnetwork loss", r"\blossy network"),
                _inject_network_loss_chaos, _clear_chaos, "network", "chaos_mesh"),
    FailureMode("network_partition", "Network partition (Chaos Mesh)",
                (r"\bnetwork partition", r"\bpartition", r"\bsplit brain"),
                _inject_network_partition_chaos, _clear_chaos, "network", "chaos_mesh"),
    FailureMode("pod_kill", "Random pod kill storm (Chaos Mesh)",
                (r"\bpod kill", r"\bkill pod", r"\bpod chaos"),
                _inject_pod_kill_chaos, _clear_chaos, "pod", "chaos_mesh"),
    FailureMode("http_abort", "HTTP 500 abort (Chaos Mesh)",
                (r"\bhttp abort", r"\babort request", r"\bchaos 500"),
                _inject_http_abort_chaos, _clear_chaos, "application", "chaos_mesh"),
    FailureMode("http_delay", "HTTP latency injection (Chaos Mesh)",
                (r"\bhttp delay", r"\brequest delay", r"\b5s delay"),
                _inject_http_delay_chaos, _clear_chaos, "application", "chaos_mesh"),
    FailureMode("stress_chaos_cpu", "CPU stress (Chaos Mesh)",
                (r"\bchaos cpu", r"\bcpu stress chaos"),
                _inject_stress_cpu_chaos, _clear_chaos, "application", "chaos_mesh"),
    FailureMode("stress_chaos_memory", "Memory stress (Chaos Mesh)",
                (r"\bchaos memory", r"\bmemory stress chaos"),
                _inject_stress_memory_chaos, _clear_chaos, "application", "chaos_mesh"),
)

_MODE_BY_ID = {m.id: m for m in FAILURE_MODES}

# High-priority phrases — checked before generic per-mode patterns (first match wins).
_PRIORITY_RULES: tuple[tuple[str, str], ...] = (
    (r"run\s*container\s*error|runcontainererror|command\s+not\s+found", "bad_command"),
    (r"\bbad_command\b", "bad_command"),
    (r"errimagepull|imagepullbackoff|image\s*pull\s*back|pull\s*back\s*off", "image"),
    (r"volume\s*mount|volumemount|mount\s+failure|failed\s+mount", "volume"),
    (r"missing\s+configmap|config\s*map\s+missing", "configmap"),
    (r"init\s*container", "init"),
    (r"startup\s*probe", "startup"),
    (r"readiness\s*probe", "readiness"),
    (r"liveness\s*probe", "liveness"),
    (r"\boom\b|out\s+of\s+memory|oomkilled", "oom"),
    (r"cpu\s+throttl", "cpu_throttle"),
    (r"crash\s*loop|crashloop", "crash"),
    (r"restart\s+continuously|continuously\s+restart|keep(s)?\s+restarting", "crash"),
    (r"stop.*traffic|receiving traffic|without crashing|not ready.*traffic", "readiness"),
    (r"\bprivileged\b|pod\s+security|\bpsa\b|security\s+policy|\bseccomp\b", "privileged"),
    (r"hostpath|host\s+path", "hostpath"),
    (r"secret\s+env|missing\s+secret", "secret_env"),
    (r"node\s+affinity|anti.?affinity", "affinity"),
    (r"\bpending\b|unschedulable|scheduling\s+fail", "pending"),
    (r"scale.*0|zero\s+replica", "instant"),
    (r"bad\s+rollout|rollout\s+fail", "bad_rollout"),
    (r"rollout\s+stuck|rollout\s+pause", "rollout_stuck"),
    (r"service\s+unreachable|selector\s+mismatch", "service_selector"),
    (r"port\s+mismatch|wrong\s+port", "port_mismatch"),
    (r"network\s+policy|traffic\s+block", "network_policy"),
    (r"\bingress\b|wrong\s+backend", "ingress_bad"),
    (r"\btaint\b|toleration", "toleration"),
    (r"pvc\s+pending|volume\s+claim", "pvc_pending"),
    (r"read.?only|permission\s+denied", "readonly_root"),
    (r"chaos\s+cpu|stress\s+chaos\s+cpu", "stress_chaos_cpu"),
    (r"chaos\s+memory|stress\s+chaos\s+memory", "stress_chaos_memory"),
    (r"memory\s+leak", "memory_leak"),
    (r"cpu\s+spike|cpu\s+stress", "cpu_stress"),
    (r"http\s+500|5xx", "http_500"),
    (r"high\s+latency|slow\s+response", "high_latency"),
    (r"deadlock|\bhang\b", "deadlock"),
    (r"dns\s+failure|coredns|name\s+resolution", "dns_failure"),
    (r"network\s+delay|latency\s+injection", "network_delay"),
    (r"packet\s+loss|network\s+loss", "network_loss"),
    (r"network\s+partition", "network_partition"),
    (r"pod\s+kill", "pod_kill"),
    (r"http\s+abort|chaos\s+500", "http_abort"),
    (r"http\s+delay|request\s+delay", "http_delay"),
)


def classify_failure_modes(message: str, default: str | None = None) -> list[str]:
    """Detect one or more failure modes from natural language (multi-failure)."""
    q = re.sub(r"\s+", " ", (message or "").lower()).strip()
    if not q:
        return [(default or cfg.OUTAGE_MODE or "crash").lower()]

    segments = re.split(
        r"\s+and\s+|\s*,\s*|\s*\+\s*|\s+also\s+|\s+plus\s+|\s+with\s+",
        q,
        flags=re.IGNORECASE,
    )
    found: list[str] = []
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        mid = classify_failure_mode(seg, default=None)
        if mid and mid not in found:
            found.append(mid)
        if len(found) >= 4:
            break

    if not found:
        found = [classify_failure_mode(message, default)]
    return found


def classify_failure_mode(message: str, default: str | None = None) -> str:
    q = re.sub(r"\s+", " ", (message or "").lower()).strip()
    if not q:
        return (default or cfg.OUTAGE_MODE or "crash").lower()

    for pat, mode_id in _PRIORITY_RULES:
        if re.search(pat, q):
            return mode_id

    for mode in FAILURE_MODES:
        mid = mode.id.replace("_", r"[_\s-]?")
        if re.search(rf"\b{mid}\b", q):
            return mode.id

    for mode in FAILURE_MODES:
        for pat in mode.patterns:
            if re.search(pat, q):
                return mode.id

    if re.search(r"\b(outage|outrage|failure|break it|break the app|take down)\b", q):
        return default or "crash"
    return (default or cfg.OUTAGE_MODE or "crash").lower()


def failure_mode_label(mode_id: str) -> str:
    m = _MODE_BY_ID.get(mode_id)
    return m.label if m else mode_id


def failure_mode_display_tokens(mode_id: str) -> list[str]:
    """Distinct error names for UI chips (QA: ErrImagePull vs ImagePullBackOff are separate)."""
    if mode_id == "image":
        return ["ErrImagePull", "ImagePullBackOff"]
    signals = MODE_EXPECTED_SIGNALS.get(mode_id, ())
    if signals:
        return list(signals[:3])
    return [failure_mode_label(mode_id)]


def inject_mode_chips(mode_ids: list[str]) -> list[dict[str, str]]:
    chips: list[dict[str, str]] = []
    for mid in mode_ids:
        tokens = failure_mode_display_tokens(mid)
        if mid == "image" and len(tokens) > 1:
            for tok in tokens:
                chips.append({"id": f"{mid}:{tok.lower()}", "label": tok})
        else:
            chips.append({"id": mid, "label": failure_mode_label(mid)})
    return chips


def observed_image_pull_phase(pod_line: str) -> str:
    low = (pod_line or "").lower()
    if "errimagepull" in low:
        return "ErrImagePull"
    if "imagepullbackoff" in low:
        return "ImagePullBackOff"
    return "ImagePullBackOff"


def format_active_failure_headline(mode_ids: list[str], pod_line: str = "") -> str:
    parts: list[str] = []
    for mid in mode_ids:
        if mid == "image":
            observed = observed_image_pull_phase(pod_line)
            other = "ImagePullBackOff" if observed == "ErrImagePull" else "ErrImagePull"
            parts.append(
                f"**Image pull failure** is already active "
                f"(observed: **{observed}**; related phase: **{other}**)"
            )
        else:
            parts.append(f"**{failure_mode_label(mid)}** is already active")
    return "; ".join(parts)


def kubectl_inject_command_recipes(mode_ids: list[str], app: dict) -> list[str]:
    """kubectl equivalents for injected failure modes — read-only documentation, no cluster mutation."""
    ns = cfg.NAMESPACE
    dep = _dep(app)
    ctr = app["container"]
    bad = app.get("bad_image") or cfg.BAD_IMAGE
    svc = _svc_name(app)
    ing = app.get("ingress") or dep
    np = _np_name(app)
    pvc = f"demo-pvc-pending-{dep}"
    port = _probe_port(app)
    modes = list(dict.fromkeys(mode_ids))
    out: list[str] = []
    skip: set[str] = set()
    if {"crash", "oom"}.issubset(set(modes)):
        out.append(
            f"kubectl patch deployment/{dep} -n {ns} --type=json  "
            "# crash+OOM combo (main container exit 1 + demo-oom-bomb sidecar)"
        )
        skip = {"crash", "oom"}

    recipes: dict[str, str] = {
        "image": f"kubectl set image deployment/{dep} {ctr}={bad} -n {ns}",
        "crash": f"kubectl patch deployment/{dep} -n {ns} --type=json  # crash loop (exit 1)",
        "oom": f"kubectl patch deployment/{dep} -n {ns} --type=json  # OOM memory bomb (48Mi limit)",
        "instant": f"kubectl scale deployment/{dep} -n {ns} --replicas=0",
        "pending": f"kubectl patch deployment/{dep} -n {ns} --type=json  # nodeSelector non-existent node",
        "volume": f"kubectl patch deployment/{dep} -n {ns}  # bad volumeMount / missing secret volume",
        "configmap": f"kubectl patch deployment/{dep} -n {ns} --type=json  # envFrom missing ConfigMap",
        "secret_env": f"kubectl patch deployment/{dep} -n {ns} --type=json  # env from missing Secret",
        "readiness": (
            f"kubectl patch deployment/{dep} -n {ns}  "
            f"# readinessProbe on port {port + 31999} (always fails)"
        ),
        "liveness": f"kubectl patch deployment/{dep} -n {ns}  # livenessProbe on /healthz-does-not-exist",
        "startup": f"kubectl patch deployment/{dep} -n {ns}  # startupProbe on wrong port",
        "init": f"kubectl patch deployment/{dep} -n {ns}  # initContainer CrashLoop",
        "cpu_throttle": f"kubectl patch deployment/{dep} -n {ns}  # CPU limit 1m (throttling)",
        "cpu_stress": f"kubectl patch deployment/{dep} -n {ns}  # demo-cpu-stress sidecar",
        "memory_leak": f"kubectl patch deployment/{dep} -n {ns}  # memory leak loop",
        "http_500": f"kubectl patch deployment/{dep} -n {ns}  # readiness probe on failing HTTP path",
        "high_latency": f"kubectl patch deployment/{dep} -n {ns}  # probe with high timeout / latency",
        "bad_command": f"kubectl patch deployment/{dep} -n {ns}  # command not found",
        "privileged": f"kubectl patch deployment/{dep} -n {ns}  # privileged container (PSA denial)",
        "hostpath": f"kubectl patch deployment/{dep} -n {ns}  # hostPath mount failure",
        "affinity": f"kubectl patch deployment/{dep} -n {ns}  # nodeAffinity unschedulable",
        "service_selector": f"kubectl patch service/{svc} -n {ns}  # selector mismatch (no endpoints)",
        "port_mismatch": f"kubectl patch service/{svc} -n {ns}  # targetPort 31999",
        "network_policy": f"kubectl apply -f networkpolicy-{np}.yaml -n {ns}  # deny all traffic",
        "ingress_bad": f"kubectl patch ingress/{ing} -n {ns}  # bad backend service",
        "bad_rollout": (
            f"kubectl scale deployment/{dep} -n {ns} --replicas=2 && "
            f"kubectl set image deployment/{dep} {ctr}={bad} -n {ns}  # bad rollout"
        ),
        "rollout_stuck": f"kubectl rollout pause deployment/{dep} -n {ns}",
        "pvc_pending": f"kubectl patch deployment/{dep} -n {ns}  # unbound PVC {pvc}",
        "readonly_root": f"kubectl patch deployment/{dep} -n {ns}  # readOnlyRootFilesystem",
        "deadlock": f"kubectl patch deployment/{dep} -n {ns}  # hang + failing liveness",
        "dns_failure": f"kubectl patch deployment/{dep} -n {ns}  # dnsPolicy=None + bad nameserver",
        "toleration": f"kubectl patch deployment/{dep} -n {ns}  # toleration + nodeSelector on tainted node",
        "dns_delay": f"kubectl apply -f -  # Chaos Mesh DNSChaos delay",
        "network_delay": f"kubectl apply -f -  # Chaos Mesh NetworkChaos delay",
        "network_loss": f"kubectl apply -f -  # Chaos Mesh NetworkChaos loss",
        "network_partition": f"kubectl apply -f -  # Chaos Mesh NetworkChaos partition",
        "pod_kill": f"kubectl apply -f -  # Chaos Mesh PodChaos kill",
        "http_abort": f"kubectl apply -f -  # Chaos Mesh HTTPChaos abort",
        "http_delay": f"kubectl apply -f -  # Chaos Mesh HTTPChaos delay",
        "stress_chaos_cpu": f"kubectl apply -f -  # Chaos Mesh StressChaos CPU",
        "stress_chaos_memory": f"kubectl apply -f -  # Chaos Mesh StressChaos memory",
    }
    for mid in modes:
        if mid in skip:
            continue
        if mid in recipes:
            out.append(recipes[mid])
        else:
            out.append(
                f"# {failure_mode_label(mid)} — kubectl patch/apply on deployment/{dep} -n {ns}"
            )
    return out


def kubectl_manual_fix_recipes(mode_ids: list[str], app: dict) -> list[str]:
    """Mode-specific undo kubectl — mirrors clear_* helpers before full manifest heal."""
    ns = cfg.NAMESPACE
    dep = _dep(app)
    ctr = app["container"]
    good = app.get("good_image") or cfg.GOOD_IMAGE
    svc = _svc_name(app)
    np = _np_name(app)
    ing = app.get("ingress") or dep
    pvc = f"demo-pvc-pending-{dep}"
    modes = set(mode_ids)
    out: list[str] = []

    if modes & CHAOS_MESH_ONLY_MODE_IDS:
        out.append(
            f"kubectl delete networkchaos,httpchaos,stresschaos,podchaos,dnschaos "
            f"-n {ns} -l enlight-lab/demo --ignore-not-found"
        )

    if "network_policy" in modes:
        out.append(f"kubectl delete networkpolicy {np} -n {ns} --ignore-not-found")

    if "ingress_bad" in modes:
        out.append(
            f"kubectl patch ingress/{ing} -n {ns} --type=json "
            f"-p '[{{\"op\":\"replace\",\"path\":\"/spec/rules/0/http/paths/0/backend/service/name\","
            f"\"value\":\"{dep}\"}}]'"
        )

    if "rollout_stuck" in modes:
        out.append(f"kubectl rollout resume deployment/{dep} -n {ns}")

    if "instant" in modes:
        out.append(f"kubectl scale deployment/{dep} -n {ns} --replicas=1")

    if "pvc_pending" in modes:
        out.append(f"kubectl delete pvc {pvc} -n {ns} --ignore-not-found")

    if "image" in modes or "bad_rollout" in modes:
        out.append(f"kubectl set image deployment/{dep} {ctr}={good} -n {ns}  # restore good image")

    patch_modes = modes & {
        "crash", "oom", "readiness", "liveness", "startup", "init", "configmap",
        "secret_env", "volume", "cpu_stress", "memory_leak", "http_500", "high_latency",
        "bad_command", "privileged", "hostpath", "affinity", "pending", "toleration",
        "cpu_throttle", "deadlock", "dns_failure", "readonly_root", "port_mismatch",
        "service_selector",
    }
    if patch_modes:
        labels = ", ".join(failure_mode_label(m) for m in sorted(patch_modes))
        out.append(f"# Undo {labels} — delete deployment + re-apply known-good manifests (next steps)")

    return out


def inject_failure_mode(mode_id: str, app: dict) -> str:
    m = _MODE_BY_ID.get((mode_id or "").lower())
    if not m:
        raise ValueError(f"Unknown failure mode: {mode_id}")
    return m.inject(app)


def inject_failure_modes(mode_ids: list[str], app: dict) -> list[str]:
    """Apply multiple failure recipes on one workload (demo-safe combo outages)."""
    logs: list[str] = []
    modes = list(dict.fromkeys(mode_ids))
    applied: list[str] = []
    skip: set[str] = set()
    if {"crash", "oom"}.issubset(set(modes)):
        logs.append(_inject_crash_oom_combo(app))
        applied.extend(["crash", "oom"])
        skip = {"crash", "oom"}
    try:
        for mode_id in modes:
            if mode_id in skip:
                continue
            logs.append(inject_failure_mode(mode_id, app))
            applied.append(mode_id)
    except Exception:
        if applied:
            _stamp_injected_modes(app, applied)
        raise
    argo_log = _ensure_argocd_visible_degradation(app, modes)
    if argo_log:
        logs.append(argo_log)
    _stamp_injected_modes(app, modes)
    return logs


# What you should see in Argo CD / kubectl events for each injected mode.
MODE_EXPECTED_SIGNALS: dict[str, tuple[str, ...]] = {
    "image": ("ErrImagePull", "ImagePullBackOff"),
    "crash": ("CrashLoopBackOff",),
    "oom": ("OOMKilled",),
    "instant": ("replicas=0",),
    "bad_rollout": ("CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull"),
    "rollout_stuck": ("ProgressDeadlineExceeded",),
    "network_policy": ("NetworkPolicy", "connection refused", "timeout"),
    "port_mismatch": ("Unhealthy", "connection refused"),
    "service_selector": ("Unhealthy", "no endpoints"),
    "ingress_bad": ("502", "503", "Unhealthy"),
    "volume": ("CreateContainerConfigError", "MountVolume.SetUp failed", "secret not found"),
    "pvc_pending": ("PersistentVolumeClaim", "unbound", "WaitForFirstConsumer", "provisioning failed"),
    "pending": ("FailedScheduling", "node-does-not-exist"),
    "dns_failure": ("DNSChaos", "lookup", "nameserver", "i/o timeout"),
    "network_delay": ("NetworkChaos", "delay", "timeout"),
    "network_loss": ("NetworkChaos", "loss", "packet"),
    "network_partition": ("NetworkChaos", "partition"),
    "pod_kill": ("PodChaos", "Killing", "pod killed"),
    "http_abort": ("HTTPChaos", "500", "abort"),
    "http_delay": ("HTTPChaos", "delay"),
    "stress_chaos_cpu": ("StressChaos", "CPU", "throttl"),
    "stress_chaos_memory": ("StressChaos", "OOMKilled", "memory"),
    "cpu_stress": ("demo-cpu-stress", "Unhealthy", "Readiness", "Degraded"),
    "cpu_throttle": ("CPUThrottling", "throttl"),
    "memory_leak": ("OOMKilled", "memory"),
    "http_500": ("Unhealthy", "Readiness", "probe failed"),
    "high_latency": ("Unhealthy", "Readiness", "probe failed"),
    "readiness": ("Unhealthy", "Readiness"),
    "liveness": ("CrashLoopBackOff", "Liveness"),
    "init": ("Init:CrashLoopBackOff", "Init:Error"),
    "startup": ("Startup probe failed", "Unhealthy"),
    "bad_command": ("RunContainerError", "command not found"),
    "privileged": ("CreateContainerConfigError", "privileged", "PSA"),
    "configmap": ("CreateContainerConfigError", "configmap", "not found"),
    "secret_env": ("CreateContainerConfigError", "secret", "not found"),
    "affinity": ("FailedScheduling", "affinity"),
    "toleration": ("FailedScheduling", "taint", "toleration"),
    "hostpath": ("FailedMount", "hostPath"),
    "readonly_root": ("Read-only file system", "permission denied"),
    "deadlock": ("CrashLoopBackOff", "Liveness", "hang"),
}


def expected_signals_for_modes(mode_ids: list[str]) -> list[str]:
    out: list[str] = []
    for mid in mode_ids:
        for sig in MODE_EXPECTED_SIGNALS.get(mid, (failure_mode_label(mid),)):
            if sig not in out:
                out.append(sig)
    return out


def describe_expected_failure(mode_ids: list[str]) -> str:
    """Plain English — what Argo CD / events should show for these modes."""
    if not mode_ids:
        return ""
    parts = []
    for mid in mode_ids:
        label = failure_mode_label(mid)
        signals = MODE_EXPECTED_SIGNALS.get(mid, ())
        if signals:
            if mid == "image":
                parts.append(
                    f"**{label}** → look for `ErrImagePull`, then `ImagePullBackOff`"
                )
            else:
                parts.append(f"**{label}** → look for `{signals[0]}`" + (
                    f" (or {', '.join(f'`{s}`' for s in signals[1:2])})" if len(signals) > 1 else ""
                ))
        else:
            parts.append(f"**{label}**")
    return "Expected in Argo CD events: " + "; ".join(parts)


def describe_expected_failure_plain(mode_ids: list[str]) -> str:
    """UI-safe expected signals (no markdown)."""
    if not mode_ids:
        return ""
    parts = []
    for mid in mode_ids:
        label = failure_mode_label(mid)
        signals = MODE_EXPECTED_SIGNALS.get(mid, ())
        if mid == "image":
            parts.append(
                f"{label}: ErrImagePull (initial pull fails), "
                f"then ImagePullBackOff (retry backoff)"
            )
        elif mid in SERVICE_LEVEL_MODE_IDS:
            parts.append(
                f"{label}: service-level outage (pod may stay Running) — "
                f"look for {signals[0] if signals else '502/timeout'}"
            )
        elif signals:
            parts.append(f"{label}: look for {signals[0]}")
        else:
            parts.append(label)
    return "Expected: " + "; ".join(parts)


FAILURE_MODE_BLURBS: dict[str, str] = {
    "init": "Init container crashes before the main app starts.",
    "startup": "Startup probe never passes — pod keeps restarting.",
    "readiness": "Pod runs but is Not Ready — no traffic routed to it.",
    "liveness": "Liveness probe fails — Kubernetes restarts the container in a loop.",
    "bad_command": "Container command missing or invalid — RunContainerError.",
    "privileged": "Privileged container blocked by cluster security policy.",
    "oom": "Container exceeds memory limit and is OOMKilled.",
    "cpu_throttle": "CPU limit so low the app is effectively frozen.",
    "crash": "Container exits with error — CrashLoopBackOff.",
    "image": "Bad image tag — first ErrImagePull, then ImagePullBackOff on retries.",
    "deadlock": "App hangs until liveness probe kills and restarts it.",
    "instant": "Deployment scaled to zero replicas — total outage.",
    "configmap": "Missing or wrong ConfigMap reference.",
    "secret_env": "Missing Secret used for environment variables.",
    "bad_rollout": "New revision has broken image/config — new pods crash.",
    "rollout_stuck": "Deployment paused mid-rollout — mixed versions stuck.",
    "service_selector": "Service selector mismatch — no endpoints (502).",
    "port_mismatch": "Service forwards to wrong container port (502).",
    "network_policy": "NetworkPolicy blocks traffic — pod may stay Running.",
    "ingress_bad": "Ingress points to wrong backend (502/503).",
    "pending": "Impossible scheduling — pod Pending forever.",
    "affinity": "Node affinity cannot be satisfied.",
    "toleration": "Pod cannot tolerate node taints — stays Pending.",
    "volume": "Volume mount missing or invalid.",
    "hostpath": "HostPath volume path invalid on the node.",
    "pvc_pending": "PVC never binds — pod waits forever.",
    "readonly_root": "Read-only filesystem — writes fail.",
    "memory_leak": "Simulated memory leak until OOM or slowdown.",
    "cpu_stress": "CPU stress sidecar — very slow responses.",
    "http_500": "App returns HTTP 500 / failing health checks.",
    "high_latency": "Probe or app responses delayed several seconds.",
    "dns_failure": "DNS resolution broken for the pod.",
    "network_delay": "Artificial network latency (Chaos Mesh or fallback).",
    "network_loss": "Packet loss on network path.",
    "network_partition": "Pod isolated from other services.",
    "pod_kill": "Random pod kills on a schedule.",
    "http_abort": "HTTP 500 injected at network layer.",
    "http_delay": "HTTP requests artificially delayed.",
    "stress_chaos_cpu": "Chaos Mesh CPU burn inside the pod.",
    "stress_chaos_memory": "Chaos Mesh memory pressure inside the pod.",
}


# (headline with {app}, root_cause label, layman paragraph with {app} and example)
FAILURE_MODE_LAYMAN: dict[str, tuple[str, str, str]] = {
    "init": (
        "{app} cannot start — the init container crashes before the main app runs.",
        "Init container crash",
        "In simple terms: an init container is setup work that must finish before the real app starts. "
        "We made that setup crash on purpose.\n\n**Example:** Like a restaurant whose prep kitchen fails every "
        "morning — the dining room never opens.",
    ),
    "startup": (
        "{app} cannot pass its startup health check — Kubernetes keeps restarting it.",
        "Startup probe failure",
        "In simple terms: after the container boots, Kubernetes runs a startup check ('are you really ready?'). "
        "We configured that check to always fail. The pod may later show CrashLoopBackOff, but the injected "
        "issue is the failing startup probe — not a bad image.\n\n**Example:** A shop must pass a safety "
        "inspection before opening; we made the inspection always fail, so it never officially opens.",
    ),
    "readiness": (
        "{app} is running but marked Not Ready — no traffic is sent to it.",
        "Readiness probe failure",
        "In simple terms: the app process is up, but Kubernetes refuses to send users to it because the "
        "readiness check fails.\n\n**Example:** A store with lights on and staff inside, but a 'CLOSED' sign "
        "on the door — customers are turned away.",
    ),
    "liveness": (
        "{app} keeps failing liveness checks and Kubernetes restarts it in a loop.",
        "Liveness probe failure",
        "In simple terms: Kubernetes periodically asks 'are you still alive?' We made that check fail, so it "
        "kills and restarts the container repeatedly.\n\n**Example:** A night guard who must ping HQ every "
        "minute — we block the phone, so security keeps sending a replacement guard over and over.",
    ),
    "bad_command": (
        "{app} cannot start — the container command is missing or invalid.",
        "RunContainerError / bad command",
        "In simple terms: Kubernetes tried to run the app but the start command does not exist or is wrong.\n\n"
        "**Example:** Telling an employee to open the store using a key that was never cut.",
    ),
    "privileged": (
        "{app} was blocked — the cluster security policy forbids privileged containers.",
        "Privileged container denied",
        "In simple terms: the deployment asked for elevated permissions the cluster is not allowed to grant.\n\n"
        "**Example:** A contractor requesting a master key to every room — building security says no.",
    ),
    "oom": (
        "{app} was killed for using more memory than its limit allows.",
        "OOMKilled",
        "In simple terms: the app used too much RAM and Kubernetes shut it down to protect the server.\n\n"
        "**Example:** A food truck that exceeds its propane tank size — the safety valve shuts the grill off.",
    ),
    "cpu_throttle": (
        "{app} is starved for CPU — it runs extremely slowly.",
        "CPU throttling",
        "In simple terms: we set the CPU limit so low the app can barely think.\n\n"
        "**Example:** Running a video call on a phone stuck at 1% battery saver — everything freezes.",
    ),
    "crash": (
        "{app} exits immediately after start — CrashLoopBackOff.",
        "CrashLoopBackOff",
        "In simple terms: the container starts, crashes right away, and Kubernetes retries in a loop.\n\n"
        "**Example:** A vending machine that powers on, errors, and reboots endlessly.",
    ),
    "image": (
        "{app} cannot pull its container image from the registry.",
        "Image pull failure (ErrImagePull / ImagePullBackOff)",
        "In simple terms: we pointed the deployment at an image tag that does not exist.\n\n"
        "**Example:** A delivery driver given a wrong address — they keep coming back empty-handed.",
    ),
    "deadlock": (
        "{app} hangs until the liveness probe kills and restarts it.",
        "Deadlock / hang",
        "In simple terms: the app freezes in place until Kubernetes decides it is dead and restarts it.\n\n"
        "**Example:** A computer with a spinning cursor that never finishes loading — you force-restart it.",
    ),
    "instant": (
        "{app} was scaled to zero replicas — nothing is serving traffic.",
        "Scaled to zero replicas",
        "In simple terms: we turned off every running copy of the app at once.\n\n"
        "**Example:** Closing all store locations simultaneously — the brand still exists, but no doors are open.",
    ),
    "configmap": (
        "{app} cannot start — it references a missing or wrong ConfigMap.",
        "ConfigMap missing / wrong",
        "In simple terms: the app needs a settings file that is not there or has the wrong name.\n\n"
        "**Example:** A recipe that says 'see appendix A' but appendix A was never printed.",
    ),
    "secret_env": (
        "{app} cannot start — a required Secret for passwords/keys is missing.",
        "Secret missing / wrong",
        "In simple terms: the app needs a password or API key from a vault entry that does not exist.\n\n"
        "**Example:** An ATM that cannot read the bank's encryption key — it refuses to operate.",
    ),
    "bad_rollout": (
        "{app} rollout is failing — new pods crash while old ones may still run.",
        "Bad rollout",
        "In simple terms: a new version was deployed but the new pods are broken.\n\n"
        "**Example:** A franchise ships a broken menu PDF — new locations open with wrong prices.",
    ),
    "rollout_stuck": (
        "{app} rollout is paused mid-update — mixed versions stuck.",
        "Rollout stuck / paused",
        "In simple terms: an update started but was frozen halfway, so old and new versions coexist.\n\n"
        "**Example:** Renovating half a building while the other half still uses old wiring.",
    ),
    "service_selector": (
        "{app} Service has no endpoints — traffic cannot reach pods.",
        "Service selector mismatch",
        "In simple terms: the load balancer is looking for pods with the wrong label, so it finds nobody.\n\n"
        "**Example:** A receptionist calling extension 100 when all staff moved to extension 200.",
    ),
    "port_mismatch": (
        "{app} Service forwards traffic to the wrong container port.",
        "Port mismatch",
        "In simple terms: callers dial the right number but are connected to the wrong department.\n\n"
        "**Example:** A doorbell wired to the garage instead of the front desk.",
    ),
    "network_policy": (
        "Network traffic to {app} is blocked by a firewall rule.",
        "NetworkPolicy blocks traffic",
        "In simple terms: a firewall rule stops other services from talking to the app (the pod may still show Running).\n\n"
        "**Example:** A store with open lights but a security gate that blocks all customers.",
    ),
    "ingress_bad": (
        "External traffic to {app} hits the wrong backend — 502/503 errors.",
        "Ingress misconfiguration",
        "In simple terms: the public URL routes to the wrong service behind the scenes.\n\n"
        "**Example:** Highway signs pointing to a closed warehouse instead of the open store.",
    ),
    "pending": (
        "{app} cannot be scheduled — no node satisfies impossible requirements.",
        "Pending forever (unschedulable)",
        "In simple terms: Kubernetes cannot find any server that meets impossible placement rules.\n\n"
        "**Example:** Hiring for a role that requires living on the moon — no candidate can ever qualify.",
    ),
    "affinity": (
        "{app} cannot be placed — node affinity rules cannot be satisfied.",
        "Node affinity failure",
        "In simple terms: the pod insists on running on a specific type of server that is not available.\n\n"
        "**Example:** A VIP guest who will only sit at table 7, but table 7 was removed.",
    ),
    "toleration": (
        "{app} cannot run — it does not tolerate required node taints.",
        "Taint / toleration mismatch",
        "In simple terms: nodes are marked 'do not place normal workloads here' and the pod lacks permission.\n\n"
        "**Example:** A no-pets apartment building — the tenant shows up with a dog and is turned away.",
    ),
    "volume": (
        "{app} cannot mount a required volume.",
        "Volume mount failure",
        "In simple terms: the app expects a disk or folder that is missing or misconfigured.\n\n"
        "**Example:** A printer told to read paper from tray 2 when tray 2 was never installed.",
    ),
    "hostpath": (
        "{app} cannot use a HostPath volume — path invalid on the node.",
        "HostPath volume failure",
        "In simple terms: the pod tries to read a folder directly on the server that does not exist.\n\n"
        "**Example:** Instructions to load stock from '/warehouse/back' but that folder was deleted.",
    ),
    "pvc_pending": (
        "{app} is waiting forever — its persistent volume claim never binds.",
        "PVC pending",
        "In simple terms: the app ordered storage that the cluster cannot provision.\n\n"
        "**Example:** A tenant waiting for a storage unit that was never built.",
    ),
    "readonly_root": (
        "{app} cannot write files — filesystem is read-only.",
        "Read-only filesystem",
        "In simple terms: the app needs to save data but the disk is locked to read-only.\n\n"
        "**Example:** A student told to submit homework on a whiteboard that cannot be written on.",
    ),
    "memory_leak": (
        "{app} is slowly leaking memory until it slows down or is OOMKilled.",
        "Memory leak simulation",
        "In simple terms: the app keeps hoarding RAM like a slow leak in a balloon.\n\n"
        "**Example:** Leaving a faucet dripping until the sink overflows.",
    ),
    "cpu_stress": (
        "{app} is under artificial CPU stress — responses become very slow.",
        "CPU stress",
        "In simple terms: we added a sidecar that burns CPU so the app struggles to respond.\n\n"
        "**Example:** Trying to work while someone runs a blender next to your desk all day.",
    ),
    "http_500": (
        "{app} returns HTTP 500 errors to clients.",
        "HTTP 500 errors",
        "In simple terms: the app is up but deliberately answers requests with 'internal server error'.\n\n"
        "**Example:** A help desk that always says 'system down' even though people are at their desks.",
    ),
    "high_latency": (
        "{app} responds very slowly — high latency injected.",
        "High latency",
        "In simple terms: every request is delayed several seconds on purpose.\n\n"
        "**Example:** A drive-through where every order waits five minutes before the kitchen starts.",
    ),
    "dns_failure": (
        "{app} cannot resolve DNS names — network lookups fail.",
        "DNS failure",
        "In simple terms: the app cannot look up other services by name (like a broken phone book).\n\n"
        "**Example:** Trying to call 'Pizza Place' but every directory lists a wrong number.",
    ),
    "network_delay": (
        "Network delay is injected for {app} — packets arrive late.",
        "Network latency (Chaos Mesh)",
        "In simple terms: artificial lag was added to network traffic to/from the pod.\n\n"
        "**Example:** A video call on a satellite link with a 3-second echo.",
    ),
    "network_loss": (
        "Packet loss is injected for {app} — connections become unreliable.",
        "Packet loss (Chaos Mesh)",
        "In simple terms: random network packets are dropped on purpose.\n\n"
        "**Example:** A bad cellphone signal where every third word is lost.",
    ),
    "network_partition": (
        "{app} is network-isolated from other services.",
        "Network partition (Chaos Mesh)",
        "In simple terms: the pod is cut off from the rest of the cluster like an island.\n\n"
        "**Example:** A store that loses phone, internet, and road access simultaneously.",
    ),
    "pod_kill": (
        "Random pod kills are scheduled for {app}.",
        "Pod kill storm (Chaos Mesh)",
        "In simple terms: Kubernetes will randomly delete pods on a timer.\n\n"
        "**Example:** Rolling blackouts that shut off power unpredictably throughout the day.",
    ),
    "http_abort": (
        "HTTP requests to {app} are aborted with 500 errors (Chaos Mesh).",
        "HTTP 500 abort (Chaos Mesh)",
        "In simple terms: traffic reaches the pod but Chaos Mesh forces failures at the network layer.\n\n"
        "**Example:** A toll booth that raises the barrier then immediately says 'transaction failed'.",
    ),
    "http_delay": (
        "HTTP requests to {app} are delayed (Chaos Mesh).",
        "HTTP latency injection (Chaos Mesh)",
        "In simple terms: Chaos Mesh slows HTTP traffic before it hits the application.\n\n"
        "**Example:** Every web page loads through intentional buffering.",
    ),
    "stress_chaos_cpu": (
        "Chaos Mesh is burning CPU inside {app}.",
        "CPU stress (Chaos Mesh)",
        "In simple terms: an intentional CPU hog runs inside the pod.\n\n"
        "**Example:** Running a marathon while carrying an extra 50-pound backpack.",
    ),
    "stress_chaos_memory": (
        "Chaos Mesh is pressuring memory inside {app}.",
        "Memory stress (Chaos Mesh)",
        "In simple terms: Chaos Mesh allocates memory to squeeze the real application.\n\n"
        "**Example:** Filling a desk drawer with bricks so there is no room to work.",
    ),
}


def failure_mode_layman_explain(
    mode_id: str, app_label: str = "the app",
) -> tuple[str, str, str]:
    """Return (headline, root_cause, simple_paragraph) for one failure mode."""
    entry = FAILURE_MODE_LAYMAN.get(mode_id)
    if not entry:
        label = failure_mode_label(mode_id)
        return (
            f"{app_label} — {label}.",
            label,
            f"In simple terms: we simulated **{label}** on {app_label}.",
        )
    headline, root, simple = entry
    return headline.format(app=app_label), root, simple.format(app=app_label)


def failure_mode_blurb(mode_id: str) -> str:
    return FAILURE_MODE_BLURBS.get(mode_id, failure_mode_label(mode_id))


def is_failure_catalog_request(message: str) -> bool:
    q = re.sub(r"\s+", " ", (message or "").lower()).strip()
    if re.search(
        r"\b(root cause|what caused|why (is|isn'?t|are|aren'?t)|what went wrong|"
        r"what broke|what happened|not working|explain)\b",
        q,
    ):
        return False
    if re.search(r"\b40\s+failure|\bfailure\s+catalog\b", q):
        return True
    if re.search(
        r"\b(list|show|send|give)\b.{0,30}\b(all\s+)?(\d+\s+)?(failure|chaos)\s*"
        r"(mode|type|list|catalog)?\b",
        q,
    ):
        return True
    if re.search(r"\blist\s+all\s+(failure|chaos|outage)\b", q):
        return True
    return False


def failure_modes_catalog_data() -> dict:
    """Structured catalog for chat UI cards."""
    by_cat: dict[str, list[dict[str, str]]] = {}
    for m in FAILURE_MODES:
        _, _, layman = failure_mode_layman_explain(m.id, "the app")
        by_cat.setdefault(m.category, []).append({
            "id": m.id,
            "label": m.label,
            "blurb": failure_mode_blurb(m.id),
            "layman": layman,
            "category": m.category,
            "tier": m.tier,
            "sample_prompt": f"Simulate {m.label.lower()} on fastapi",
            "sample_prompt_nginx": f"Simulate {m.label.lower()} on nginx",
            "service_level": m.id in SERVICE_LEVEL_MODE_IDS,
        })
    return {
        "count": len(FAILURE_MODES),
        "by_category": by_cat,
        "modes": list_failure_modes(),
    }


def failure_modes_catalog_reply() -> str:
    lines = [
        f"**{len(FAILURE_MODES)} failure modes** available for demo on FastAPI and Nginx.\n",
        "Pick any mode below (or type the sample prompt). Say **auto-fix** to recover.\n",
    ]
    cat_labels = {
        "pod": "Pod", "deployment": "Deployment", "network": "Network",
        "node": "Node", "storage": "Storage", "application": "Application",
    }
    for cat in ("pod", "deployment", "network", "node", "storage", "application"):
        items = [m for m in FAILURE_MODES if m.category == cat]
        if not items:
            continue
        lines.append(f"\n**{cat_labels.get(cat, cat.title())}** ({len(items)})")
        for m in items:
            blurb = failure_mode_blurb(m.id)
            lines.append(f"- **{m.label}** (`{m.id}`) — {blurb}")
    return "\n".join(lines)


def clear_all_failure_injections(app: dict) -> None:
    _clear_chaos(app)
    _clear_network_policy(app)
    _clear_service(app)
    _clear_ingress(app)
    _clear_rollout_pause(app)
    _clear_stress_sidecar(app)
    _clear_dns_config(app)
    _clear_injection_stamp(app)
    _clear_pvc_volume(app)
    _clear_demo_volumes(app)
    _clear_crash(app)
    _clear_probes(app)
    _clear_init(app)
    _clear_node_selector(app)
    _clear_toleration(app)
    _clear_affinity(app)
    _clear_security(app)
    _clear_envfrom(app)
    _clear_resources(app)


def failure_catalog_for_prompt() -> str:
    by_cat: dict[str, list[str]] = {}
    for m in FAILURE_MODES:
        by_cat.setdefault(m.category, []).append(f"{m.id}: {m.label}")
    lines = ["Supported simulate_failure types (natural language or failure_mode id):"]
    for cat in ("pod", "deployment", "network", "node", "storage", "application"):
        if cat in by_cat:
            lines.append(f"\n{cat.upper()}:")
            lines.extend(f"  - {x}" for x in by_cat[cat])
    lines.append("\nCombine with 'and' or comma: e.g. crash loop and network policy on nginx")
    return "\n".join(lines)


def list_failure_modes() -> list[dict[str, str]]:
    return [
        {
            "id": m.id,
            "label": m.label,
            "category": m.category,
            "tier": m.tier,
            "chaos_mesh": m.tier == "chaos_mesh",
            "blurb": failure_mode_blurb(m.id),
            "sample_prompt": f"Simulate {m.label.lower()} on fastapi",
            "service_level": m.id in SERVICE_LEVEL_MODE_IDS,
        }
        for m in FAILURE_MODES
    ]


# Proven demo modes — visible signals in pod status, events, or inject cards.
CHAOS_LAB_MODE_IDS: frozenset[str] = frozenset({
    "image", "crash", "oom", "instant",
    "bad_rollout", "rollout_stuck", "configmap", "secret_env",
    "readiness", "liveness", "startup", "init", "bad_command",
    "pending", "affinity", "toleration", "cpu_throttle", "deadlock",
    "network_policy", "port_mismatch", "service_selector", "ingress_bad",
    "volume", "pvc_pending", "hostpath", "privileged", "readonly_root",
    "http_500", "memory_leak", "cpu_stress", "high_latency",
})

CHAOS_MESH_ONLY_MODE_IDS: frozenset[str] = frozenset({
    "dns_failure", "network_delay", "network_loss", "network_partition",
    "pod_kill", "http_abort", "http_delay", "stress_chaos_cpu", "stress_chaos_memory",
})

# Pod may stay Running — UI must list injected mode names (not only CrashLoopBackOff).
SERVICE_LEVEL_MODE_IDS: frozenset[str] = frozenset({
    "network_policy", "port_mismatch", "service_selector", "ingress_bad",
    "dns_failure", "network_delay", "network_loss", "network_partition", "high_latency",
})


def list_chaos_lab_modes(*, chaos_mesh: bool = False) -> list[dict[str, str]]:
    allowed = set(CHAOS_LAB_MODE_IDS)
    if chaos_mesh:
        allowed |= CHAOS_MESH_ONLY_MODE_IDS
    return [m for m in list_failure_modes() if m["id"] in allowed]


def failure_modes_by_category_chaos_lab(*, chaos_mesh: bool = False) -> dict[str, list[dict[str, str]]]:
    allowed = {m["id"] for m in list_chaos_lab_modes(chaos_mesh=chaos_mesh)}
    out: dict[str, list[dict[str, str]]] = {}
    for cat, items in failure_modes_by_category().items():
        filtered = [x for x in items if x["id"] in allowed]
        if filtered:
            out[cat] = filtered
    return out


def failure_modes_by_category() -> dict[str, list[dict[str, str]]]:
    out: dict[str, list[dict[str, str]]] = {}
    for m in FAILURE_MODES:
        out.setdefault(m.category, []).append({
            "id": m.id,
            "label": m.label,
            "tier": m.tier,
            "chaos_mesh": m.tier == "chaos_mesh",
        })
    return out


def chaos_mesh_info() -> dict:
    try:
        import chaos_mesh as cm
        return cm.chaos_mesh_status()
    except Exception as exc:
        return {"installed": False, "error": str(exc), "active_count": 0, "active_experiments": []}


# --- Demo scenarios (bundled here so live overlay only needs failure_modes.py) ---

@dataclass(frozen=True)
class DemoScenario:
    id: str
    title: str
    subtitle: str
    prompt: str
    modes: tuple[str, ...]
    apps: tuple[str, ...]
    icon: str
    tier: str


DEMO_SCENARIOS: tuple[DemoScenario, ...] = (
    DemoScenario(
        "classic_outage", "Classic outage", "Bad image — ErrImagePull in ~60s",
        "Simulate image pull failure on fastapi", ("image",), ("fastapi",), "💥", "classic",
    ),
    DemoScenario(
        "pod_meltdown", "Pod meltdown", "Crash loop + OOM — double failure",
        "Crash loop and OOM on fastapi", ("crash", "oom"), ("fastapi",), "🔥", "advanced",
    ),
    DemoScenario(
        "network_nightmare", "Network nightmare", "Policy block + port mismatch + delay",
        "Network policy block and port mismatch and high latency on both apps",
        ("network_policy", "port_mismatch", "high_latency"), ("fastapi", "nginx"), "🌐", "advanced",
    ),
    DemoScenario(
        "gitops_disaster", "GitOps disaster", "Bad rollout stuck mid-update",
        "Bad rollout and rollout stuck on fastapi", ("bad_rollout", "rollout_stuck"), ("fastapi",), "⚙️", "advanced",
    ),
    DemoScenario(
        "storage_storm", "Storage storm", "PVC pending + volume mount failure",
        "PVC pending and volume mount failure on both apps",
        ("pvc_pending", "volume"), ("fastapi", "nginx"), "💾", "advanced",
    ),
    DemoScenario(
        "app_chaos", "Application chaos", "HTTP 500 + memory leak + CPU stress",
        "HTTP 500 and memory leak and CPU stress on both apps",
        ("http_500", "memory_leak", "cpu_stress"), ("fastapi", "nginx"), "⚡", "advanced",
    ),
    DemoScenario(
        "dns_delay_chaos", "Chaos Mesh: DNS + latency", "Requires Chaos Mesh — DNS errors + 4s delay",
        "DNS failure and network delay chaos on nginx", ("dns_failure", "network_delay"), ("nginx",), "🌀", "chaos",
    ),
    DemoScenario(
        "full_stack", "Full stack drill", "Both apps — crash + network policy",
        "Crash loop and network policy on both apps",
        ("crash", "network_policy"), ("fastapi", "nginx"), "🎯", "advanced",
    ),
)

_SCENARIO_BY_ID = {s.id: s for s in DEMO_SCENARIOS}


def list_demo_scenarios(*, include_chaos_mesh: bool | None = None) -> list[dict]:
    if include_chaos_mesh is None:
        include_chaos_mesh = bool(chaos_mesh_info().get("installed"))
    rows = []
    for s in DEMO_SCENARIOS:
        if s.tier == "chaos" and not include_chaos_mesh:
            continue
        rows.append({
            "id": s.id,
            "title": s.title,
            "subtitle": s.subtitle,
            "prompt": s.prompt,
            "modes": list(s.modes),
            "apps": list(s.apps),
            "icon": s.icon,
            "tier": s.tier,
        })
    return rows


def scenario_by_id(scenario_id: str) -> DemoScenario | None:
    return _SCENARIO_BY_ID.get((scenario_id or "").strip().lower())
