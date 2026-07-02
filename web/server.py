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
)
from stream import stream_demo_action
import config as cfg

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="Kube Self-Heal Demo", version="1.0.0")
_status_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="status")
_holmes_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="holmes")

if STATIC.exists():
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

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


def _proxy_impl(upstream_base: str | None, path: str, request: Request, unavailable_message: str) -> Response:
    if not upstream_base:
        return JSONResponse({"error": unavailable_message}, status_code=404)
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
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)


def _staging_proxy_impl(path: str, request: Request) -> Response:
    """Expose FastAPI via the demo UI LB (saves a 3rd OCI load balancer)."""
    return _proxy_impl(_STAGING_UPSTREAM, path, request, "staging proxy only available in OCI mode")


def _nginx_proxy_impl(path: str, request: Request) -> Response:
    """Expose nginx via the demo UI LB with its own public page."""
    return _proxy_impl(_NGINX_UPSTREAM, path, request, "nginx proxy only available in OCI mode")


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
    if "agent-v18" in home and "agent-v18" in chat:
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
        "chat_separate": "chat-page-main" in chat,
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
        if not isinstance(history, list):
            history = []
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(
            _holmes_pool, lambda: holmes_chat(message, history=history),
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
    if not isinstance(history, list):
        history = []

    def action(on_step):
        return holmes_chat(message, on_step=on_step, history=history)

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
    return stream_demo_action(auto_fix)


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
    return stream_demo_action(lambda on_step: auto_fix_app(app_id, on_step=on_step))
