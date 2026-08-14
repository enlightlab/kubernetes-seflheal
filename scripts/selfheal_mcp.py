#!/usr/bin/env python3
"""Self-Heal MCP server — plain-English outage injection for Cursor / Claude Desktop.

Add to Cursor mcp.json (Windows example):
{
  "mcpServers": {
    "enlight-selfheal": {
      "command": "python",
      "args": ["D:/devops-selfheal/scripts/selfheal_mcp.py"],
      "env": {
        "SELFHEAL_URL": "https://selfheal.enlightlab.com"
      }
    }
  }
}

Or run against local dev: SELFHEAL_URL=http://localhost:30901
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

SELFHEAL_URL = os.environ.get("SELFHEAL_URL", "https://selfheal.enlightlab.com").rstrip("/")


def _post(path: str, body: dict) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{SELFHEAL_URL}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read().decode())


def _get(path: str) -> dict:
    with urllib.request.urlopen(f"{SELFHEAL_URL}{path}", timeout=60) as resp:
        return json.loads(resp.read().decode())


def _tool_result(text: str, *, is_error: bool = False) -> dict:
    return {
        "content": [{"type": "text", "text": text}],
        "isError": is_error,
    }


TOOLS = [
    {
        "name": "simulate_outage",
        "description": (
            "Inject Kubernetes failures in plain English on fastapi, nginx, or both. "
            "Examples: 'crash loop', 'OOM and network policy', 'DNS failure on nginx'. "
            "40 failure types + Chaos Mesh when installed."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "app": {"type": "string", "description": "fastapi, nginx, or both"},
                "description": {"type": "string", "description": "Plain English failure description"},
            },
            "required": ["app", "description"],
        },
    },
    {
        "name": "run_demo_scenario",
        "description": "Run a curated wow-factor demo scenario (pod meltdown, network nightmare, etc.)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "scenario_id": {
                    "type": "string",
                    "description": "classic_outage, pod_meltdown, network_nightmare, gitops_disaster, full_stack, dns_delay_chaos, ...",
                },
                "app": {"type": "string", "description": "Optional override: fastapi or nginx"},
            },
            "required": ["scenario_id"],
        },
    },
    {
        "name": "auto_fix",
        "description": "Restore fastapi and/or nginx to healthy state after simulated failures",
        "inputSchema": {
            "type": "object",
            "properties": {
                "app": {"type": "string", "description": "fastapi, nginx, or both"},
            },
            "required": ["app"],
        },
    },
    {
        "name": "list_failure_modes",
        "description": "List all supported failure types by category (pod, network, deployment, chaos, ...)",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_demo_scenarios",
        "description": "List curated multi-failure demo scenarios for client presentations",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "cluster_snapshot",
        "description": "Live health snapshot of FastAPI + Nginx apps, pods, GitOps",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _handle_tool(name: str, args: dict) -> dict:
    try:
        if name == "simulate_outage":
            app = (args.get("app") or "fastapi").strip().lower()
            desc = (args.get("description") or "").strip()
            if not desc:
                return _tool_result("description is required", is_error=True)
            r = _post("/api/inject", {"app": app, "message": desc})
            msg = r.get("data", {}).get("message") or json.dumps(r, indent=2)
            return _tool_result(msg)

        if name == "run_demo_scenario":
            sid = (args.get("scenario_id") or "").strip()
            body = {"scenario": sid, "message": ""}
            if args.get("app"):
                body["app"] = args["app"]
            r = _post("/api/inject", body)
            msg = r.get("data", {}).get("message") or json.dumps(r, indent=2)
            return _tool_result(msg)

        if name == "auto_fix":
            app = (args.get("app") or "both").strip().lower()
            msg_text = f"Auto-fix any issues in the cluster" if app in ("both", "all") else f"Auto-fix {app}"
            r = _post("/api/holmes/chat", {"message": msg_text, "mode": "demo"})
            reply = r.get("data", {}).get("reply") or json.dumps(r, indent=2)
            return _tool_result(reply)

        if name == "list_failure_modes":
            r = _get("/api/failure-modes")
            lines = [f"**{r.get('count', 0)} failure modes** (plain English supported)\n"]
            for cat, items in (r.get("by_category") or {}).items():
                lines.append(f"\n{cat.upper()}:")
                for m in items:
                    tag = " [Chaos Mesh]" if m.get("chaos_mesh") else ""
                    lines.append(f"  - {m['label']}{tag}")
            chaos = r.get("chaos_mesh") or {}
            lines.append(f"\nChaos Mesh: {'installed' if chaos.get('installed') else 'not installed (run setup-chaos-mesh.sh)'}")
            return _tool_result("\n".join(lines))

        if name == "list_demo_scenarios":
            r = _get("/api/scenarios")
            lines = ["**Demo scenarios** (one-click wow factor)\n"]
            for s in r.get("scenarios") or []:
                lines.append(f"- **{s['title']}** (`{s['id']}`) — {s['subtitle']}")
            return _tool_result("\n".join(lines))

        if name == "cluster_snapshot":
            r = _get("/api/holmes/snapshot")
            snap = r.get("data") or r
            lines = [f"Namespace: {snap.get('namespace')}", f"Failure modes: {snap.get('failure_mode_count', '?')}"]
            for a in snap.get("apps") or []:
                lines.append(f"- {a['label']}: {a['state']} · {a['pod_line']}")
            return _tool_result("\n".join(lines))

        return _tool_result(f"Unknown tool: {name}", is_error=True)
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:500]
        return _tool_result(f"HTTP {e.code}: {body}", is_error=True)
    except Exception as exc:
        return _tool_result(str(exc), is_error=True)


def _send(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def main() -> None:
    """Minimal MCP stdio loop (JSON-RPC 2.0 subset)."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        rid = req.get("id")
        method = req.get("method", "")
        params = req.get("params") or {}

        if method == "initialize":
            _send({
                "jsonrpc": "2.0",
                "id": rid,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "enlight-selfheal", "version": "1.0.0"},
                },
            })
        elif method == "notifications/initialized":
            pass
        elif method == "tools/list":
            _send({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            name = params.get("name", "")
            args = params.get("arguments") or {}
            result = _handle_tool(name, args)
            _send({"jsonrpc": "2.0", "id": rid, "result": result})
        elif method == "ping":
            _send({"jsonrpc": "2.0", "id": rid, "result": {}})
        else:
            if rid is not None:
                _send({
                    "jsonrpc": "2.0",
                    "id": rid,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                })


if __name__ == "__main__":
    main()
