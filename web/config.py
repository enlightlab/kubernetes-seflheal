"""Runtime configuration — Oracle Cloud (OKE) or local kind demo."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# oci = Oracle OKE / any remote cluster (default)
# local = laptop kind cluster with port-forwards
DEPLOY_TARGET = os.environ.get("DEPLOY_TARGET", "oci").strip().lower()


def _flag(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


IN_CLUSTER = _flag("IN_CLUSTER", DEPLOY_TARGET == "oci")
USE_PORT_FORWARD = _flag("USE_PORT_FORWARD", DEPLOY_TARGET == "local")

KUBE_CONTEXT = _env("KUBE_CONTEXT")
KUBECONFIG = _env("KUBECONFIG")

NAMESPACE = _env("NAMESPACE", "enlight-staging")
ARGOCD_NAMESPACE = _env("ARGOCD_NAMESPACE", "argocd")
ARGOCD_APP = _env("ARGOCD_APP", "fastapi-staging")
DEPLOYMENT_NAME = _env("DEPLOYMENT_NAME", "fastapi")
CONTAINER_NAME = _env("CONTAINER_NAME", "api")
POD_LABEL = _env("POD_LABEL", "app=fastapi")

_default_bad = (
    "bom.ocir.io/bmitpaosivqx/enlight-fastapi:DOES-NOT-EXIST"
    if DEPLOY_TARGET == "oci"
    else "enlight-fastapi:DOES-NOT-EXIST"
)
_default_good = (
    "bom.ocir.io/bmitpaosivqx/enlight-fastapi:demo-pass"
    if DEPLOY_TARGET == "oci"
    else "enlight-fastapi:demo-pass"
)
BAD_IMAGE = _env("BAD_IMAGE", _default_bad)
GOOD_IMAGE = _env("GOOD_IMAGE", _default_good)

# instant = scale to 0 (app down in ~3s). crash = exit-1 loop (~15s). image = slow ErrImagePull.
OUTAGE_MODE = _env("OUTAGE_MODE", "image").strip().lower()

# In-cluster health check (server-side, works inside OKE)
_default_health = (
    f"http://{DEPLOYMENT_NAME}.{NAMESPACE}.svc.cluster.local/health"
    if DEPLOY_TARGET == "oci"
    else "http://localhost:30800/health"
)
APP_HEALTH_CHECK_URL = _env("APP_HEALTH_CHECK_URL", _default_health)

_default_dashboard = APP_HEALTH_CHECK_URL.removesuffix("/health") or APP_HEALTH_CHECK_URL
APP_DASHBOARD_CHECK_URL = _env("APP_DASHBOARD_CHECK_URL", _default_dashboard)

ARGOCD_CHECK_URL = _env("ARGOCD_CHECK_URL", "http://argocd-server.argocd.svc.cluster.local")

# Public links shown in the browser — leave empty on OKE; resolved from LoadBalancer IPs in-cluster.
_default_public_health = "" if DEPLOY_TARGET == "oci" else APP_HEALTH_CHECK_URL
_default_public_dashboard = "" if DEPLOY_TARGET == "oci" else APP_DASHBOARD_CHECK_URL
_default_public_argocd = "" if DEPLOY_TARGET == "oci" else ARGOCD_CHECK_URL
PUBLIC_APP_HEALTH_URL = _env("PUBLIC_APP_HEALTH_URL", _default_public_health)
PUBLIC_APP_DASHBOARD_URL = _env("PUBLIC_APP_DASHBOARD_URL", _default_public_dashboard)
PUBLIC_ARGOCD_URL = _env("PUBLIC_ARGOCD_URL", _default_public_argocd)
PUBLIC_ARGOCD_APP_URL = _env(
    "PUBLIC_ARGOCD_APP_URL",
    (
        f"{PUBLIC_ARGOCD_URL.rstrip('/')}/applications/{ARGOCD_NAMESPACE}/{ARGOCD_APP}"
        if PUBLIC_ARGOCD_URL
        else ""
    ),
)

# Manifest apply on heal — local path or bundled overlay in container
_default_overlay = (
    str(ROOT / "deploy" / "k8s" / "staging-heal")
    if DEPLOY_TARGET == "oci"
    else str(Path(_env("ENLIGHT_LAB_ROOT", r"D:\enlight-lab-platform")) / "demos" / "demo2-chat-to-deploy" / "overlays" / "local")
)
HEAL_OVERLAY_PATH = Path(_env("HEAL_OVERLAY_PATH", _default_overlay))
STAGING_APP_PATH = Path(_env("STAGING_APP_PATH", str(ROOT / "deploy" / "k8s" / "staging-app")))

K8SGPT_BIN = _env("K8SGPT_BIN", "k8sgpt")
K8SGPT_TIMEOUT = int(_env("K8SGPT_TIMEOUT", "90"))

HOLMES_BIN = _env("HOLMES_BIN", "holmes")
HOLMES_MODEL = _env("HOLMES_MODEL", "anthropic/claude-3-5-haiku-20241022")
HOLMES_TIMEOUT = int(_env("HOLMES_TIMEOUT", "300"))
HOLMES_MAX_STEPS = int(_env("HOLMES_MAX_STEPS", "10"))
HOLMES_ENABLED = _flag("HOLMES_ENABLED", False)

# robusta = in-cluster Holmes HTTP + Robusta Cloud AI (no Gemini/OpenAI key in selfheal-ui)
# cli = holmes ask subprocess with LLM API keys
# auto = try robusta HTTP first, then CLI
HOLMES_MODE = _env("HOLMES_MODE", "cli").strip().lower()
HOLMES_HTTP_URL = _env(
    "HOLMES_HTTP_URL",
    "http://robusta-holmes.robusta.svc.cluster.local/api/chat",
)
HOLMES_HTTP_MODEL = _env("HOLMES_HTTP_MODEL", "robusta")
HOLMES_HTTP_TIMEOUT = int(_env("HOLMES_HTTP_TIMEOUT", "300"))
ROBUSTA_API_ENDPOINT = _env("ROBUSTA_API_ENDPOINT", "https://api.ap.robusta.dev")


def resolved_holmes_model() -> str:
    """Pick Holmes model based on available API keys in the pod."""
    if HOLMES_MODEL.startswith("gemini/"):
        return HOLMES_MODEL
    if HOLMES_MODEL and not HOLMES_MODEL.startswith(("openai/", "gemini/")):
        return HOLMES_MODEL
    if os.environ.get("GEMINI_API_KEY", "").strip():
        return "gemini/gemini-3.5-flash"
    if os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return "anthropic/claude-3-5-haiku-20241022"
    if os.environ.get("OPENAI_API_KEY", "").strip():
        return "openai/gpt-4o-mini"
    return HOLMES_MODEL or "gemini/gemini-3.5-flash"

ARGOCD_APP_MANIFEST = Path(
    _env(
        "ARGOCD_APP_MANIFEST",
        str(ROOT / "deploy" / "k8s" / "argocd" / "fastapi-staging-app.yaml"),
    )
)

ARGOCD_DISPLAY_USER = _env("ARGOCD_DISPLAY_USER", "admin")
ARGOCD_DISPLAY_PASSWORD = _env("ARGOCD_DISPLAY_PASSWORD", "")

# Pause between narrative steps so presenters can explain (seconds)
DEMO_STEP_PAUSE = float(_env("DEMO_STEP_PAUSE", "2.0"))

# When staging is not live, /demo auto-runs Step 1 deploy on first page load
AUTO_DEPLOY_ON_LOAD = _flag("AUTO_DEPLOY_ON_LOAD", True)


def demo_credentials() -> dict[str, str]:
    return {
        "argocd_user": ARGOCD_DISPLAY_USER,
        "argocd_password": ARGOCD_DISPLAY_PASSWORD,
        "argocd_password_set": bool(ARGOCD_DISPLAY_PASSWORD),
    }


def public_links() -> dict[str, str]:
    return {
        "app_health": PUBLIC_APP_HEALTH_URL,
        "app_dashboard": PUBLIC_APP_DASHBOARD_URL,
        "argocd": PUBLIC_ARGOCD_URL,
        "argocd_app": PUBLIC_ARGOCD_APP_URL,
    }


def runtime_info() -> dict[str, str | bool]:
    return {
        "deploy_target": DEPLOY_TARGET,
        "in_cluster": IN_CLUSTER,
        "use_port_forward": USE_PORT_FORWARD,
        "namespace": NAMESPACE,
        "argocd_app": ARGOCD_APP,
        "kube_context": KUBE_CONTEXT or "(default / in-cluster)",
        "outage_mode": OUTAGE_MODE,
        "good_image": GOOD_IMAGE,
        "auto_deploy_on_load": AUTO_DEPLOY_ON_LOAD,
        "holmes_enabled": HOLMES_ENABLED,
        "holmes_mode": HOLMES_MODE,
        "holmes_model": resolved_holmes_model() if HOLMES_ENABLED else HOLMES_MODEL,
        "holmes_http_url": HOLMES_HTTP_URL,
        **demo_credentials(),
    }
