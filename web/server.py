"""Kube Self-Heal Demo UI - single-purpose web app."""
from __future__ import annotations

import asyncio
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from actions import (
    auto_fix,
    deploy_application,
    explain_with_ai,
    holmes_chat,
    platform_status,
    reset_staging,
    resolved_argocd_credentials,
    resolved_public_links,
    simulate_outage,
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


def _staging_proxy_impl(path: str, request: Request) -> Response:
    """Expose staging FastAPI via the demo UI LB (saves a 3rd OCI load balancer)."""
    if not _STAGING_UPSTREAM:
        return JSONResponse({"error": "staging proxy only available in OCI mode"}, status_code=404)
    target = f"{_STAGING_UPSTREAM}/{path}".rstrip("/") or _STAGING_UPSTREAM
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


@app.api_route("/staging", methods=["GET", "HEAD"])
@app.api_route("/staging/", methods=["GET", "HEAD"])
def staging_proxy_root(request: Request) -> Response:
    return _staging_proxy_impl("", request)


@app.api_route("/staging/{path:path}", methods=["GET", "HEAD"])
def staging_proxy(path: str, request: Request) -> Response:
    return _staging_proxy_impl(path, request)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/demo")
def demo_page() -> FileResponse:
    return FileResponse(STATIC / "demo.html")


@app.get("/holmes")
def holmes_page() -> FileResponse:
    return FileResponse(STATIC / "holmes.html")


@app.get("/healthz")
def healthz() -> dict[str, bool]:
    return {"ok": True}


@app.get("/api/config")
def api_config() -> JSONResponse:
    try:
        links = resolved_public_links()
    except Exception:
        links = cfg.public_links()
    info = {**cfg.runtime_info(), **resolved_argocd_credentials(), "links": links}
    return JSONResponse({"ok": True, "data": info})


@app.get("/api/status")
async def api_status() -> JSONResponse:
    try:
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(_status_pool, platform_status)
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
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(_holmes_pool, holmes_chat, message)
        return JSONResponse({"ok": bool(data.get("ok")), "data": data})
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


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
