"""Gemini function-calling agent — MCP-style cluster tools for Engineer mode."""
from __future__ import annotations

import contextvars
import json
import logging
import re
import urllib.request
from typing import Any, Callable

import config as cfg

log = logging.getLogger(__name__)

_agent_user_message: contextvars.ContextVar[str] = contextvars.ContextVar(
    "agent_user_message", default="",
)
StepCallback = Callable[[dict], None] | None

_READ_RESOURCES = frozenset({
    "pods", "deployments", "events", "services", "ingresses", "replicasets",
})
_DESCRIBE_RESOURCES = frozenset({"pods", "deployments", "services"})

AGENT_TOOL_DECLARATIONS: list[dict[str, Any]] = [
    {
        "name": "get_resources",
        "description": (
            "Read-only kubectl get for pods, deployments, events, services, ingresses, "
            f"replicasets in namespace {cfg.NAMESPACE}."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "resource": {
                    "type": "string",
                    "description": "Kubernetes resource type (pods, deployments, events, etc.)",
                },
                "label_selector": {
                    "type": "string",
                    "description": "Optional label selector, e.g. app=fastapi or app=nginx-demo",
                },
            },
            "required": ["resource"],
        },
    },
    {
        "name": "describe_resource",
        "description": f"kubectl describe for pods, deployments, or services in {cfg.NAMESPACE}.",
        "parameters": {
            "type": "object",
            "properties": {
                "resource": {"type": "string", "description": "pods, deployments, or services"},
                "name": {"type": "string", "description": "Resource name"},
            },
            "required": ["resource", "name"],
        },
    },
    {
        "name": "get_pod_logs",
        "description": f"Fetch container logs for a pod in {cfg.NAMESPACE}.",
        "parameters": {
            "type": "object",
            "properties": {
                "pod_name": {"type": "string"},
                "tail_lines": {"type": "integer", "description": "Default 80"},
            },
            "required": ["pod_name"],
        },
    },
    {
        "name": "cluster_snapshot",
        "description": "Live summary of demo apps (FastAPI, Nginx), pod lines, health, GitOps.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "simulate_failure",
        "description": (
            "Inject supported Kubernetes failures on fastapi or nginx (31 kubectl modes across "
            "pod, deployment, network, node, storage, application chaos). "
            "Combine modes: failure_modes array or 'crash and network policy' in description."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "app": {"type": "string", "description": "fastapi, nginx, or both"},
                "failure_description": {
                    "type": "string",
                    "description": "Natural language, e.g. 'OOM and service unreachable'",
                },
                "failure_mode": {
                    "type": "string",
                    "description": "Single exact mode id (legacy)",
                },
                "failure_modes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Multiple mode ids: crash, oom, image, network_policy, port_mismatch, "
                        "bad_rollout, pvc_pending, http_500, memory_leak, etc."
                    ),
                },
            },
            "required": ["app", "failure_description"],
        },
    },
    {
        "name": "auto_fix",
        "description": "Restore fastapi and/or nginx to healthy state after a simulated failure.",
        "parameters": {
            "type": "object",
            "properties": {
                "app": {
                    "type": "string",
                    "description": "fastapi, nginx, or both",
                },
            },
            "required": ["app"],
        },
    },
    {
        "name": "deploy_app",
        "description": "Deploy fastapi and/or nginx via GitOps (Argo CD).",
        "parameters": {
            "type": "object",
            "properties": {
                "app": {"type": "string", "description": "fastapi, nginx, or both"},
            },
            "required": ["app"],
        },
    },
]


def _gemini_key_ok() -> bool:
    from actions import _gemini_key_configured
    return _gemini_key_configured()


def _model_id() -> str:
    model = cfg.resolved_holmes_model()
    if not model.startswith("gemini/"):
        return ""
    return model.replace("gemini/", "", 1)


def _safe_name(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]", "", (name or "").strip())[:128]


def _normalize_app(app: str) -> str:
    a = (app or "").strip().lower()
    if a in ("both", "all", "both apps"):
        return "all"
    if "nginx" in a:
        return "nginx"
    if "fast" in a or a == "api":
        return "fastapi"
    return a


def execute_agent_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Run one agent tool; returns JSON-serializable result."""
    from actions import (
        _app_has_active_injection,
        _apps_status_data,
        _auto_fix_app_impl,
        _deploy_nginx_app,
        _kubectl,
        _pause_gitops_for_demo_apps,
        _simulate_app_error_impl,
        deploy_application,
        holmes_snapshot,
    )

    ns = cfg.NAMESPACE
    try:
        if name == "get_resources":
            resource = (args.get("resource") or "pods").strip().lower()
            if resource not in _READ_RESOURCES:
                return {"ok": False, "error": f"resource not allowed: {resource}"}
            cmd = ["get", resource, "-n", ns, "-o", "wide"]
            label = (args.get("label_selector") or "").strip()
            if label:
                cmd.extend(["-l", label])
            code, out = _kubectl(*cmd)
            return {"ok": code == 0, "output": out[:12000]}

        if name == "describe_resource":
            resource = (args.get("resource") or "").strip().lower()
            rname = _safe_name(str(args.get("name") or ""))
            if resource not in _DESCRIBE_RESOURCES or not rname:
                return {"ok": False, "error": "invalid resource or name"}
            code, out = _kubectl("describe", resource, rname, "-n", ns)
            return {"ok": code == 0, "output": out[:12000]}

        if name == "get_pod_logs":
            pod = _safe_name(str(args.get("pod_name") or ""))
            if not pod:
                return {"ok": False, "error": "pod_name required"}
            tail = int(args.get("tail_lines") or 80)
            tail = max(10, min(tail, 200))
            code, out = _kubectl("logs", pod, "-n", ns, f"--tail={tail}")
            return {"ok": code == 0, "output": out[:12000]}

        if name == "cluster_snapshot":
            snap = holmes_snapshot()
            rows = _apps_status_data()
            return {"ok": True, "snapshot": snap, "apps": rows}

        if name == "simulate_failure":
            app_raw = str(args.get("app") or "")
            app_id = _normalize_app(app_raw)
            from failure_modes import classify_failure_modes, failure_mode_label
            user_msg = _agent_user_message.get("")
            desc = str(args.get("failure_description") or "").strip()
            combined = f"{user_msg} {desc}".strip() or "outage"
            explicit = [str(x).strip().lower() for x in (args.get("failure_modes") or []) if x]
            if not explicit:
                single = str(args.get("failure_mode") or "").strip().lower()
                if single:
                    explicit = [single]
            modes = explicit or classify_failure_modes(combined)
            mode_label = ", ".join(failure_mode_label(m) for m in modes)

            if app_id == "all":
                _pause_gitops_for_demo_apps()
                results = []
                failed: list[str] = []
                for aid in ("fastapi", "nginx"):
                    r = _simulate_app_error_impl(aid, message=combined, mode=modes)
                    applied = r.get("modes") or r.get("mode") or modes
                    results.append({
                        "app": aid,
                        "applied_modes": applied,
                        "pod_line": r.get("pod_line"),
                        "injected": _app_has_active_injection(cfg.demo_app(aid)),
                    })
                    if not _app_has_active_injection(cfg.demo_app(aid)):
                        failed.append(aid)
                msg = (
                    f"Applied **{mode_label}** to FastAPI and Nginx. "
                    f"Mode ids: `{', '.join(modes)}`."
                )
                if failed:
                    msg += f" Warning: injection missing on {', '.join(failed)}."
                return {
                    "ok": True,
                    "applied_modes": modes,
                    "mode_label": mode_label,
                    "apps": results,
                    "message": msg,
                }

            if app_id not in ("fastapi", "nginx"):
                return {"ok": False, "error": "app must be fastapi, nginx, or both"}
            r = _simulate_app_error_impl(app_id, message=combined, mode=modes)
            applied = r.get("modes") or r.get("mode") or modes
            return {
                "ok": True,
                "applied_modes": applied,
                "mode_label": mode_label,
                "message": r.get("message", ""),
                "pod_line": r.get("pod_line"),
            }
        if name == "auto_fix":
            from actions import _already_healthy_reply

            app_id = _normalize_app(str(args.get("app") or ""))
            if app_id not in ("fastapi", "nginx", "all"):
                return {"ok": False, "error": "app must be fastapi, nginx, or both"}
            skip = _already_healthy_reply(app_id)
            if skip:
                return {"ok": True, "already_healthy": True, "message": skip["message"]}
            if app_id == "all":
                notes = []
                for aid in ("fastapi", "nginx"):
                    try:
                        r = _auto_fix_app_impl(aid, fast=True)
                        notes.append(r.get("message") or f"{aid} recovered")
                    except Exception as exc:
                        notes.append(f"{aid}: partial heal — {exc}")
                return {"ok": True, "message": "\n".join(notes)}
            r = _auto_fix_app_impl(app_id, fast=True)
            return {"ok": True, "message": r.get("message", "")}

        if name == "deploy_app":
            user_msg = _agent_user_message.get("")
            bad = None
            try:
                from actions import _unsupported_workload_reply, _unsupported_workload_token
                bad = _unsupported_workload_token(user_msg)
            except Exception:
                pass
            if bad:
                return {"ok": True, "message": _unsupported_workload_reply(bad), "skipped": True}
            app_id = _normalize_app(str(args.get("app") or ""))
            if app_id == "all":
                deploy_application()
                _deploy_nginx_app()
                return {"ok": True, "message": "Deployed FastAPI and Nginx"}
            if app_id == "fastapi":
                deploy_application()
                return {"ok": True, "message": "Deployed FastAPI"}
            if app_id == "nginx":
                _deploy_nginx_app()
                return {"ok": True, "message": "Deployed Nginx"}
            return {"ok": False, "error": "app must be fastapi, nginx, or both"}

        return {"ok": False, "error": f"unknown tool: {name}"}
    except Exception as exc:
        log.exception("Agent tool %s failed", name)
        return {"ok": False, "error": str(exc)[:500]}


def _format_history_for_gemini(history: list[dict] | None) -> list[dict]:
    contents: list[dict] = []
    for turn in (history or [])[-10:]:
        role = turn.get("role", "user")
        text = str(turn.get("content") or "").strip()
        if not text:
            continue
        gemini_role = "model" if role == "assistant" else "user"
        contents.append({"role": gemini_role, "parts": [{"text": text[:2000]}]})
    return contents


def _agent_system_instruction(cluster_facts: str) -> str:
    from failure_modes import failure_catalog_for_prompt
    apps = ", ".join(a["label"] for a in cfg.demo_apps().values())
    return (
        "You are Cluster Command Deck in Engineer mode — like Claude Desktop with MCP tools.\n"
        f"You have real tools to read and modify namespace `{cfg.NAMESPACE}` ({apps}).\n"
        "Always use tools to inspect the cluster before mutating it.\n"
        "KUBECTL NAMING (never get this wrong):\n"
        f"- FastAPI: deployment `{cfg.DEPLOYMENT_NAME}`, label `{cfg.POD_LABEL}`, Argo app `{cfg.ARGOCD_APP}`\n"
        "- Nginx: deployment `nginx-demo`, label `app=nginx-demo`, Argo app `nginx-staging`\n"
        "- NEVER use `app=nginx`, deployment `nginx`, or angle-bracket placeholders like <pod-name>\n"
        "- Use: POD=$(kubectl get pods -n NS -l app=nginx-demo -o jsonpath='{.items[0].metadata.name}')\n"
        f"{failure_catalog_for_prompt()}\n"
        "Call simulate_failure with failure_description matching the user's exact failure type.\n"
        "Pass failure_mode when you know the exact id (e.g. bad_command for RunContainerError).\n"
        "Use app=both for both demo apps. The backend classifies from description + user message.\n"
        "After simulate_failure, report applied_mode from the tool result — do not guess a different type.\n"
        "Do not tell the user to run commands manually if a tool can do it.\n"
        f"LIVE CLUSTER FACTS:\n{cluster_facts}"
    )


def gemini_agent_chat(
    message: str,
    cluster_facts: str,
    history: list[dict] | None = None,
    on_step: StepCallback = None,
    max_steps: int | None = None,
) -> tuple[bool, str]:
    """Multi-step Gemini agent with function calling (MCP-style)."""
    if not _gemini_key_ok():
        return False, ""
    model_id = _model_id()
    if not model_id:
        return False, ""

    key = __import__("os").environ.get("GEMINI_API_KEY", "").strip()
    steps_limit = max_steps or int(__import__("os").environ.get("HOLMES_CHAT_MAX_STEPS", "8"))
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model_id}:generateContent?key={key}"
    )

    contents = _format_history_for_gemini(history)
    contents.append({"role": "user", "parts": [{"text": message}]})

    system = _agent_system_instruction(cluster_facts)
    token = _agent_user_message.set(message)

    try:
        for step_i in range(steps_limit):
            payload = {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": contents,
                "tools": [{"functionDeclarations": AGENT_TOOL_DECLARATIONS}],
                "generationConfig": {"temperature": 0.2},
            }
            try:
                req = urllib.request.Request(
                    url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=120) as resp:
                    data = json.loads(resp.read().decode("utf-8", errors="replace"))
            except Exception as exc:
                log.warning("Gemini agent step failed: %s", exc)
                return False, str(exc)[:400]

            candidates = data.get("candidates") or []
            if not candidates:
                return False, "No response from Gemini agent"
            parts = (candidates[0].get("content") or {}).get("parts") or []
            if not parts:
                return False, "Empty agent response"

            function_calls = [p["functionCall"] for p in parts if "functionCall" in p]
            text_parts = [p.get("text", "") for p in parts if p.get("text")]

            if function_calls:
                contents.append({"role": "model", "parts": parts})
                response_parts = []
                for fc in function_calls:
                    fname = fc.get("name", "")
                    fargs = fc.get("args") or {}
                    if on_step:
                        title, detail, phase = _friendly_agent_step(fname, fargs)
                        on_step({"title": title, "detail": detail, "phase": phase})
                    tool_result = execute_agent_tool(fname, fargs)
                    response_parts.append({
                        "functionResponse": {
                            "name": fname,
                            "response": tool_result,
                        },
                    })
                contents.append({"role": "user", "parts": response_parts})
                continue

            reply = "\n".join(t for t in text_parts if t).strip()
            if reply:
                return True, reply[:6000]

        return False, "Agent reached max steps — try a shorter request or switch to Demo mode."
    finally:
        _agent_user_message.reset(token)


def _friendly_agent_step(fname: str, fargs: dict) -> tuple[str, str, str]:
    """Human-readable live-operation steps (no raw Tool: JSON)."""
    if fname == "simulate_failure":
        app = str(fargs.get("app") or "workload")
        modes = fargs.get("failure_modes") or []
        desc = str(fargs.get("failure_description") or "").strip()
        detail = ", ".join(modes) if modes else (desc[:100] or "failure injection")
        return "Injecting failure on cluster", f"{app} — {detail}", "break"
    if fname == "auto_fix":
        return "Running auto-fix", str(fargs.get("app") or "apps"), "health"
    if fname == "deploy_app":
        return "Deploying workload", str(fargs.get("app") or "apps"), "k8s"
    if fname == "cluster_snapshot":
        return "Reading cluster status", cfg.NAMESPACE, "k8s"
    if fname in ("get_resources", "describe_resource"):
        res = fargs.get("resource") or fargs.get("name") or "resource"
        return "Checking Kubernetes", str(res), "k8s"
    if fname == "get_pod_logs":
        return "Fetching pod logs", str(fargs.get("pod_name") or ""), "k8s"
    return "Working on cluster", fname.replace("_", " "), "k8s"


def effective_chat_mode(mode: str | None) -> str:
    m = (mode or cfg.CHAT_MODE or "agent").strip().lower()
    return m if m in ("demo", "agent", "hybrid") else "agent"


def should_use_demo_fast_path(
    message: str,
    history: list[dict] | None,
    mode: str | None,
) -> bool:
    """Hybrid: curated kubectl recipes for explicit demo commands; agent for open-ended asks."""
    from actions import (
        _classify_chat_action,
        _is_capabilities_question,
        _is_greeting,
        _needs_status_disambiguation,
        _resolve_action_target,
        _wants_inject_commands_explanation,
        _wants_kubectl_check_commands,
        _wants_manual_fix_commands,
    )

    if not cfg.CHAT_ACTIONS_ENABLED:
        return False

    act_type, target = _classify_chat_action(message, history)
    target = _resolve_action_target(act_type, message, history, target)

    # Cluster mutations + status always use kubectl recipes (all modes — including Engineer).
    if act_type in (
        "capabilities", "app_count", "links", "app_status", "status",
        "inject_commands", "manual_fix",
    ):
        return True
    if act_type in ("deploy", "reset", "outage", "heal", "explain"):
        return True

    chat_mode = effective_chat_mode(mode)
    if chat_mode == "demo":
        return True
    if _is_greeting(message) or _is_capabilities_question(message):
        return True
    if _needs_status_disambiguation(message):
        return True
    if _wants_kubectl_check_commands(message):
        return True
    if _wants_inject_commands_explanation(message):
        return True
    if _wants_manual_fix_commands(message):
        return True
    return False
