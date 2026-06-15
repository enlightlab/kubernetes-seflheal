"""Enlight Lab Demo Control Center - local host UI on port 30900."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from actions import (
    demo1_explain,
    demo1_heal,
    demo1_inject,
    demo2_dispatch,
    demo3_catalog,
    demo3_run,
    demo4_infrastructure_status,
    demo4_run,
    demo5_pr,
    demo5_status,
    platform_status,
)

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="Enlight Demo Control", version="1.0.0")

if STATIC.exists():
    app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/status")
def api_status() -> JSONResponse:
    try:
        return JSONResponse({"ok": True, "data": platform_status()})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/demo/2/{variant}")
def api_demo2(variant: str) -> JSONResponse:
    if variant not in ("compliant", "non-compliant"):
        return JSONResponse({"ok": False, "error": "invalid variant"}, status_code=400)
    try:
        return JSONResponse({"ok": True, "data": demo2_dispatch(variant)})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/demo/1/inject")
def api_demo1_inject() -> JSONResponse:
    try:
        return JSONResponse({"ok": True, "data": demo1_inject()})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/demo/1/explain")
def api_demo1_explain() -> JSONResponse:
    try:
        return JSONResponse({"ok": True, "data": demo1_explain()})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/demo/1/heal")
def api_demo1_heal() -> JSONResponse:
    try:
        return JSONResponse({"ok": True, "data": demo1_heal()})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/drift")
def drift_page() -> FileResponse:
    return FileResponse(STATIC / "drift.html")


@app.get("/api/demo/4/status")
def api_demo4_status() -> JSONResponse:
    try:
        return JSONResponse({"ok": True, "data": demo4_infrastructure_status()})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/demo/4/{phase}")
def api_demo4(phase: str) -> JSONResponse:
    if phase not in ("drift", "reconcile", "baseline"):
        return JSONResponse({"ok": False, "error": "invalid phase"}, status_code=400)
    try:
        return JSONResponse({"ok": True, "data": demo4_run(phase)})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/demo/5/status")
def api_demo5_status() -> JSONResponse:
    try:
        return JSONResponse({"ok": True, "data": demo5_status()})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/demo/5/{variant}")
def api_demo5(variant: str) -> JSONResponse:
    if variant not in ("compliant", "non-compliant"):
        return JSONResponse({"ok": False, "error": "invalid variant"}, status_code=400)
    try:
        return JSONResponse({"ok": True, "data": demo5_pr(variant)})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/demo/3/catalog")
def api_demo3_catalog() -> JSONResponse:
    try:
        return JSONResponse({"ok": True, "data": demo3_catalog()})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/demo/3/{phase}")
def api_demo3(phase: str, service: str = "auto") -> JSONResponse:
    allowed = ("scaffold", "pr", "deploy", "runbook", "investigate", "full")
    if phase not in allowed:
        return JSONResponse({"ok": False, "error": "invalid phase"}, status_code=400)
    try:
        return JSONResponse({"ok": True, "data": demo3_run(phase, service)})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
