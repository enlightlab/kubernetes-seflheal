"""Kube Self-Heal Demo UI - single-purpose web app."""
from __future__ import annotations

import urllib.error
import urllib.request
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from actions import auto_fix, explain_with_ai, platform_status, resolved_public_links, simulate_outage
import config as cfg

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="Kube Self-Heal Demo", version="1.0.0")

if STATIC.exists():
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

_STAGING_UPSTREAM = (
    f"http://{cfg.DEPLOYMENT_NAME}.{cfg.NAMESPACE}.svc.cluster.local"
    if cfg.DEPLOY_TARGET == "oci"
    else None
)


@app.api_route("/staging/{path:path}", methods=["GET", "HEAD"])
def staging_proxy(path: str, request: Request) -> Response:
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


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/healthz")
def healthz() -> dict[str, bool]:
    return {"ok": True}


@app.get("/api/config")
def api_config() -> JSONResponse:
    try:
        links = resolved_public_links()
    except Exception:
        links = cfg.public_links()
    return JSONResponse({
        "ok": True,
        "data": {
            **cfg.runtime_info(),
            "links": links,
        },
    })


@app.get("/api/status")
def api_status() -> JSONResponse:
    try:
        return JSONResponse({"ok": True, "data": platform_status()})
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
