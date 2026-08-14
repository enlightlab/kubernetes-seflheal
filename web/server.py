"""Kube Self-Heal Demo UI - single-purpose web app."""
from __future__ import annotations

import asyncio
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from actions import (
    auto_fix,
    auto_fix_app,
    deploy_application,
    deploy_demo_app,
    explain_demo_app,
    explain_with_ai,
    gemini_health,
    holmes_chat,
    holmes_cli_health,
    holmes_snapshot,
    platform_status,
    platform_status_for_app,
    reset_staging,
    reset_demo_app,
    resolved_argocd_credentials,
    resolved_public_app_links,
    resolved_public_links,
    simulate_outage,
    simulate_app_outage,
    inject_outage_plain_english,
)
from stream import stream_demo_action
import config as cfg

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="Kube Self-Heal Demo", version="1.0.0")
_status_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="status")
_holmes_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="holmes")
_NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"}

_STAGING_UPSTREAM = (
    f"http://{cfg.DEPLOYMENT_NAME}.{cfg.NAMESPACE}.svc.cluster.local"
    if cfg.DEPLOY_TARGET == "oci"
    else None
)
_NGINX_UPSTREAM = (
    f"http://nginx-demo.{cfg.NAMESPACE}.svc.cluster.local"
    if cfg.DEPLOY_TARGET == "oci"
    else None
)


def _client_wants_json(request: Request, path: str) -> bool:
    accept = (request.headers.get("accept") or "").lower()
    if "application/json" in accept and "text/html" not in accept:
        return True
    return path.strip("/").lower() in ("health", "healthz", "ready", "readyz")


def _proxy_error_response(
    request: Request,
    path: str,
    *,
    status: int,
    title: str,
    detail: str,
) -> Response:
    """Browser-friendly gateway errors — match ingress 502/503 instead of Python tracebacks."""
    if _client_wants_json(request, path):
        return JSONResponse(
            {"status": "unavailable", "error": detail, "code": status},
            status_code=status,
        )
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 0; background: #f8fafc; color: #0f172a; }}
    main {{ max-width: 36rem; margin: 4rem auto; padding: 2rem; background: #fff;
      border: 1px solid #e2e8f0; border-radius: 12px; box-shadow: 0 8px 24px rgba(15,23,42,.06); }}
    h1 {{ font-size: 1.35rem; margin: 0 0 .75rem; }}
    p {{ color: #475569; line-height: 1.55; margin: 0 0 1rem; }}
    a {{ color: #2563eb; text-decoration: none; font-weight: 600; }}
  </style>
</head>
<body>
  <main>
    <h1>{title}</h1>
    <p>{detail}</p>
    <p><a href="/chat">← Back to Enlight Lab</a></p>
  </main>
</body>
</html>"""
    return Response(content=html.encode("utf-8"), status_code=status, media_type="text/html; charset=utf-8")


def _proxy_impl(
    upstream_base: str | None,
    path: str,
    request: Request,
    unavailable_message: str,
    *,
    app_label: str = "Staging app",
) -> Response:
    if not upstream_base:
        return _proxy_error_response(
            request, path, status=404, title="404 Not Found",
            detail=unavailable_message,
        )
    target = f"{upstream_base}/{path}".rstrip("/") or upstream_base
    if request.url.query:
        target = f"{target}?{request.url.query}"
    try:
        req = urllib.request.Request(target, method=request.method)
        with urllib.request.urlopen(req, timeout=10) as upstream:
            return Response(
                content=upstream.read(),
                status_code=upstream.status,
                media_type=upstream.headers.get_content_type(),
            )
    except urllib.error.HTTPError as e:
        return Response(content=e.read(), status_code=e.code)
    except urllib.error.URLError as e:
        reason = e.reason
        if isinstance(reason, TimeoutError):
            return _proxy_error_response(
                request, path, status=503, title="503 Service Unavailable",
                detail=f"{app_label} did not respond in time — it may be recovering from a simulated outage.",
            )
        return _proxy_error_response(
            request, path, status=502, title="502 Bad Gateway",
            detail=f"{app_label} is not reachable — connection refused. This is expected during a chaos outage.",
        )
    except TimeoutError:
        return _proxy_error_response(
            request, path, status=503, title="503 Service Unavailable",
            detail=f"{app_label} did not respond in time — it may be recovering from a simulated outage.",
        )
    except Exception:
        return _proxy_error_response(
            request, path, status=502, title="502 Bad Gateway",
            detail=f"{app_label} is temporarily unavailable.",
        )


def _staging_proxy_impl(path: str, request: Request) -> Response:
    """Expose FastAPI via the demo UI LB (saves a 3rd OCI load balancer)."""
    return _proxy_impl(
        _STAGING_UPSTREAM, path, request, "staging proxy only available in OCI mode",
        app_label="FastAPI staging app",
    )


def _nginx_proxy_impl(path: str, request: Request) -> Response:
    """Expose nginx via the demo UI LB with its own public page."""
    return _proxy_impl(
        _NGINX_UPSTREAM, path, request, "nginx proxy only available in OCI mode",
        app_label="Nginx staging app",
    )


@app.api_route("/staging", methods=["GET", "HEAD"])
@app.api_route("/staging/", methods=["GET", "HEAD"])
def staging_proxy_root(request: Request) -> Response:
    return _staging_proxy_impl("", request)


@app.api_route("/staging/{path:path}", methods=["GET", "HEAD"])
def staging_proxy(path: str, request: Request) -> Response:
    return _staging_proxy_impl(path, request)


@app.api_route("/nginx", methods=["GET", "HEAD"])
@app.api_route("/nginx/", methods=["GET", "HEAD"])
def nginx_proxy_root(request: Request) -> Response:
    return _nginx_proxy_impl("", request)


@app.api_route("/nginx/{path:path}", methods=["GET", "HEAD"])
def nginx_proxy(path: str, request: Request) -> Response:
    return _nginx_proxy_impl(path, request)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(
        STATIC / "home.html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"},
    )


@app.get("/overview")
def overview_page() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/demo")
def demo_page() -> FileResponse:
    return FileResponse(STATIC / "demo.html")


@app.get("/chat")
def chat_page() -> FileResponse:
    return FileResponse(
        STATIC / "chat.html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"},
    )


@app.get("/holmes")
def holmes_page() -> FileResponse:
    """Legacy URL — same chat page."""
    return FileResponse(
        STATIC / "chat.html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"},
    )


@app.get("/api/ui-version")
def api_ui_version() -> JSONResponse:
    """Verify which UI build is mounted in the running pod."""
    home = (STATIC / "home.html").read_text(encoding="utf-8", errors="replace")
    chat = (STATIC / "chat.html").read_text(encoding="utf-8", errors="replace")
    if "agent-v84" in chat:
        ui_build = "agent-v84"
    elif "agent-v83" in chat:
        ui_build = "agent-v83"
    elif "agent-v82" in chat:
        ui_build = "agent-v82"
    elif "agent-v81" in chat:
        ui_build = "agent-v81"
    elif "agent-v80" in chat:
        ui_build = "agent-v80"
    elif "agent-v79" in chat:
        ui_build = "agent-v79"
    elif "agent-v76" in chat:
        ui_build = "agent-v76"
    elif "agent-v75" in chat:
        ui_build = "agent-v75"
    elif "agent-v74" in chat:
        ui_build = "agent-v74"
    elif "agent-v73" in chat:
        ui_build = "agent-v73"
    elif "agent-v72" in chat:
        ui_build = "agent-v72"
    elif "agent-v71" in chat:
        ui_build = "agent-v71"
    elif "agent-v70" in chat:
        ui_build = "agent-v70"
    elif "agent-v69" in chat:
        ui_build = "agent-v69"
    elif "agent-v68" in chat:
        ui_build = "agent-v68"
    elif "agent-v67" in chat:
        ui_build = "agent-v67"
    elif "agent-v66" in chat:
        ui_build = "agent-v66"
    elif "agent-v65" in chat:
        ui_build = "agent-v65"
    elif "agent-v64" in chat:
        ui_build = "agent-v64"
    elif "agent-v63" in chat:
        ui_build = "agent-v63"
    elif "agent-v62" in chat:
        ui_build = "agent-v62"
    elif "agent-v61" in chat:
        ui_build = "agent-v61"
    elif "agent-v60" in chat:
        ui_build = "agent-v60"
    elif "agent-v59" in chat:
        ui_build = "agent-v59"
    elif "agent-v58" in chat:
        ui_build = "agent-v58"
    elif "agent-v57" in chat:
        ui_build = "agent-v57"
    elif "agent-v56" in chat:
        ui_build = "agent-v56"
    elif "agent-v55" in chat:
        ui_build = "agent-v55"
    elif "agent-v54" in chat:
        ui_build = "agent-v54"
    elif "agent-v53" in chat:
        ui_build = "agent-v53"
    elif "agent-v52" in chat:
        ui_build = "agent-v52"
    elif "agent-v51" in chat:
        ui_build = "agent-v51"
    elif "agent-v50" in chat:
        ui_build = "agent-v50"
    elif "agent-v49" in chat:
        ui_build = "agent-v49"
    elif "agent-v48" in chat:
        ui_build = "agent-v48"
    elif "agent-v47" in chat:
        ui_build = "agent-v47"
    elif "agent-v46" in chat:
        ui_build = "agent-v46"
    elif "agent-v45" in chat:
        ui_build = "agent-v45"
    elif "agent-v44" in chat:
        ui_build = "agent-v44"
    elif "agent-v43" in chat:
        ui_build = "agent-v43"
    elif "agent-v42" in chat:
        ui_build = "agent-v42"
    elif "agent-v41" in chat:
        ui_build = "agent-v41"
    elif "agent-v40" in chat:
        ui_build = "agent-v40"
    elif "agent-v39" in chat:
        ui_build = "agent-v39"
    elif "agent-v35" in chat:
        ui_build = "agent-v35"
    elif "agent-v34" in chat:
        ui_build = "agent-v34"
    elif "agent-v33" in chat:
        ui_build = "agent-v33"
    elif "agent-v32" in chat:
        ui_build = "agent-v32"
    elif "agent-v31" in chat:
        ui_build = "agent-v31"
    elif "agent-v30" in chat:
        ui_build = "agent-v30"
    elif "agent-v29" in chat:
        ui_build = "agent-v29"
    elif "agent-v28" in chat:
        ui_build = "agent-v28"
    elif "agent-v27" in chat:
        ui_build = "agent-v26"
    elif "agent-v25" in chat:
        ui_build = "agent-v25"
    elif "agent-v24" in chat:
        ui_build = "agent-v24"
    elif "agent-v23" in chat:
        ui_build = "agent-v23"
    elif "agent-v22" in chat:
        ui_build = "agent-v22"
    elif "agent-v21" in chat:
        ui_build = "agent-v21"
    elif "agent-v20" in chat:
        ui_build = "agent-v20"
    elif "agent-v19" in home and "agent-v19" in chat:
        ui_build = "agent-v19"
    elif "agent-v18" in home and "agent-v18" in chat:
        ui_build = "agent-v18"
    elif "agent-v17" in home or "agent-v17" in chat:
        ui_build = "agent-v17"
    else:
        legacy = (STATIC / "holmes.html").read_text(encoding="utf-8", errors="replace") if (STATIC / "holmes.html").exists() else ""
        ui_build = (
            "agent-v16" if "agent-v16" in legacy else
            ("agent-v15" if "agent-welcome" in legacy else "legacy")
        )
    return JSONResponse({
        "ok": True,
        "ui_build": ui_build,
        "has_sidebar": "holmes-sidebar" in chat or "holmes-sidebar" in home,
        "chat_separate": "chat-page-main" in chat or "chat-deck-main" in chat,
        "chat_simple": "agent-chat-simple" in chat,
        "chat_deck": "agent-chat-deck" in chat,
        "chat_mvp": "el-page" in chat or "ccd-root" in chat or "agent-v27" in chat or "agent-v26" in chat or "agent-v25" in chat,
        "chat_agent_tools": (Path(__file__).parent / "agent_tools.py").exists(),
        "home_page": "home-app" in home,
    })


@app.get("/healthz")
def healthz() -> dict[str, bool]:
    return {"ok": True}


@app.get("/health/gemini")
async def health_gemini() -> JSONResponse:
    try:
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(_status_pool, gemini_health)
        status = 200 if data.get("ok") else 503
        return JSONResponse({"ok": bool(data.get("ok")), "data": data}, status_code=status)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/health/holmes")
async def health_holmes() -> JSONResponse:
    try:
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(_status_pool, holmes_cli_health)
        status = 200 if data.get("ok") else 503
        return JSONResponse({"ok": bool(data.get("ok")), "data": data}, status_code=status)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/config")
def api_config() -> JSONResponse:
    try:
        links = resolved_public_links()
        app_links = resolved_public_app_links()
    except Exception:
        links = cfg.public_links()
        app_links = cfg.public_app_links()
    info = {**cfg.runtime_info(), **resolved_argocd_credentials(), "links": links, "app_links": app_links}
    return JSONResponse({"ok": True, "data": info})


@app.get("/api/status")
async def api_status(demo_app: str = "fastapi") -> JSONResponse:
    try:
        loop = asyncio.get_running_loop()
        app_id = (demo_app or "fastapi").strip().lower()
        if app_id not in cfg.demo_apps():
            app_id = "fastapi"
        data = await loop.run_in_executor(_status_pool, lambda: platform_status_for_app(app_id, resolve_links=True))
        return JSONResponse({"ok": True, "data": data})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/deploy")
def api_deploy() -> JSONResponse:
    try:
        return JSONResponse({"ok": True, "data": deploy_application()})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/reset")
def api_reset() -> JSONResponse:
    try:
        return JSONResponse({"ok": True, "data": reset_staging()})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/failure-modes")
def api_failure_modes() -> JSONResponse:
    from failure_modes import (
        chaos_mesh_info,
        failure_modes_by_category,
        failure_modes_by_category_chaos_lab,
        list_chaos_lab_modes,
        list_failure_modes,
    )
    mesh = chaos_mesh_info()
    chaos_on = bool(mesh.get("installed"))
    return JSONResponse({
        "ok": True,
        "count": len(list_failure_modes()),
        "modes": list_failure_modes(),
        "chaos_lab_modes": list_chaos_lab_modes(chaos_mesh=chaos_on),
        "by_category": failure_modes_by_category(),
        "chaos_lab_by_category": failure_modes_by_category_chaos_lab(chaos_mesh=chaos_on),
        "chaos_mesh": mesh,
    })


@app.get("/api/scenarios")
def api_scenarios() -> JSONResponse:
    from failure_modes import chaos_mesh_info, list_demo_scenarios
    mesh = chaos_mesh_info()
    return JSONResponse({
        "ok": True,
        "scenarios": list_demo_scenarios(include_chaos_mesh=bool(mesh.get("installed"))),
        "chaos_mesh": mesh,
    })


@app.get("/api/chaos/status")
async def api_chaos_status() -> JSONResponse:
    try:
        from failure_modes import chaos_mesh_info
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(_status_pool, chaos_mesh_info)
        return JSONResponse({"ok": True, "data": data})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/inject")
async def api_inject(request: Request) -> JSONResponse:
    """Plain-English failure injection — for MCP, scripts, and automations."""
    try:
        body = await request.json()
        message = str(body.get("message") or body.get("description") or "").strip()
        app_id = str(body.get("app") or body.get("app_id") or "fastapi").strip().lower()
        scenario_id = str(body.get("scenario") or body.get("scenario_id") or "").strip() or None
        if not message and not scenario_id:
            return JSONResponse({"ok": False, "error": "message or scenario required"}, status_code=400)
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(
            _holmes_pool,
            lambda: inject_outage_plain_english(message, app_id=app_id, scenario_id=scenario_id),
        )
        return JSONResponse({"ok": bool(data.get("ok", True)), "data": data})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/outage")
def api_outage() -> JSONResponse:
    try:
        return JSONResponse({"ok": True, "data": simulate_outage()})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/explain")
def api_explain() -> JSONResponse:
    try:
        return JSONResponse({"ok": True, "data": explain_with_ai()})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/heal")
def api_heal() -> JSONResponse:
    try:
        return JSONResponse({"ok": True, "data": auto_fix()})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/holmes/chat")
async def api_holmes_chat(request: Request) -> JSONResponse:
    try:
        body = await request.json()
        message = str(body.get("message", "")).strip()
        history = body.get("history") or []
        chat_mode = str(body.get("mode") or "").strip() or None
        if not isinstance(history, list):
            history = []
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(
            _holmes_pool, lambda: holmes_chat(message, history=history, mode=chat_mode),
        )
        return JSONResponse({"ok": bool(data.get("ok")), "data": data})
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/holmes/snapshot")
async def api_holmes_snapshot() -> JSONResponse:
    try:
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(_status_pool, holmes_snapshot)
        return JSONResponse({"ok": True, "data": data})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/holmes/chat/stream")
async def api_holmes_chat_stream(request: Request) -> StreamingResponse:
    body = await request.json()
    message = str(body.get("message", "")).strip()
    history = body.get("history") or []
    chat_mode = str(body.get("mode") or "").strip() or None
    if not isinstance(history, list):
        history = []

    def action(on_step):
        return holmes_chat(message, on_step=on_step, history=history, mode=chat_mode)

    return stream_demo_action(action)


@app.post("/api/deploy/stream")
def api_deploy_stream():
    return stream_demo_action(deploy_application)


@app.post("/api/reset/stream")
def api_reset_stream():
    return stream_demo_action(reset_staging)


@app.post("/api/outage/stream")
def api_outage_stream():
    return stream_demo_action(simulate_outage)


@app.post("/api/explain/stream")
def api_explain_stream():
    return stream_demo_action(explain_with_ai)


@app.post("/api/heal/stream")
def api_heal_stream():
    return stream_demo_action(lambda on_step: auto_fix(on_step=on_step, fast=True))


@app.post("/api/apps/{app_id}/deploy/stream")
def api_app_deploy_stream(app_id: str):
    return stream_demo_action(lambda on_step: deploy_demo_app(app_id, on_step=on_step))


@app.post("/api/apps/{app_id}/reset/stream")
def api_app_reset_stream(app_id: str):
    return stream_demo_action(lambda on_step: reset_demo_app(app_id, on_step=on_step))


@app.post("/api/apps/{app_id}/outage/stream")
def api_app_outage_stream(app_id: str):
    return stream_demo_action(lambda on_step: simulate_app_outage(app_id, on_step=on_step))


@app.post("/api/apps/{app_id}/explain/stream")
def api_app_explain_stream(app_id: str):
    return stream_demo_action(lambda on_step: explain_demo_app(app_id, on_step=on_step))


@app.post("/api/apps/{app_id}/heal/stream")
def api_app_heal_stream(app_id: str):
    return stream_demo_action(lambda on_step: auto_fix_app(app_id, on_step=on_step, fast=True))


@app.get("/static/holmes.js")
def holmes_js_asset() -> FileResponse:
    return FileResponse(STATIC / "holmes.js", headers=_NO_CACHE)


@app.get("/static/ccd.css")
def ccd_css_asset() -> FileResponse:
    return FileResponse(STATIC / "ccd.css", headers=_NO_CACHE)


if STATIC.exists():
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
