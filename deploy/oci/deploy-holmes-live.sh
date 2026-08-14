#!/bin/bash
# Deploy HolmesGPT UI + backend to live selfheal-ui without a Docker rebuild.
# Overlays repo files via ConfigMap + volume mounts (survives pod restart).
# Run from repo root in Cloud Shell:  bash deploy/oci/deploy-holmes-live.sh
set -eu

NS=selfheal
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

# Windows tarballs may ship CRLF — bash fails with "set: invalid option" / $'\r'.
find deploy/oci -name '*.sh' -exec sed -i 's/\r$//' {} + 2>/dev/null || true

need() {
  if [ ! -f "$1" ]; then
    echo "Missing $1 — upload the latest web/ folder from your Windows repo."
    exit 1
  fi
}

need web/demo_scenarios.py
need web/chaos_mesh.py
need web/actions.py
need web/failure_modes.py
need web/config.py
need web/server.py
need web/static/demo.html
need web/static/demo.js
need web/static/home.html
need web/static/chat.html
need web/static/holmes.js
need web/static/ccd.css
need web/static/styles.css
need web/static/assets/enlight-lab-mark.png
need web/static/assets/enlight-lab-mark.svg

if ! grep -qE 'el-page|ccd-root' web/static/chat.html; then
  echo "ERROR: web/static/chat.html is old — re-pack from Windows."
  exit 1
fi
if ! grep -qE 'enlight-lab-logo\.png' web/static/chat.html; then
  echo "ERROR: chat.html missing company logo enlight-lab-logo.png."
  exit 1
fi
if ! grep -qE 'enlight-lab-logo\.png' web/static/home.html; then
  echo "ERROR: home.html missing company logo enlight-lab-logo.png."
  exit 1
fi
if ! grep -qE 'el-typing-dots|ccd-loading-dots' web/static/holmes.js; then
  echo "ERROR: holmes.js is old (missing loading indicator) — re-pack from Windows."
  exit 1
fi
if ! grep -q 'gemini_agent_chat' web/agent_tools.py; then
  echo "ERROR: agent_tools.py missing Engineer mode — re-pack from Windows."
  exit 1
fi
if ! grep -q 'FAILURE_MODES' web/failure_modes.py; then
  echo "ERROR: failure_modes.py missing failure catalog — re-pack from Windows."
  exit 1
fi
UI_VER="$(grep -o 'agent-v[0-9]*' web/static/chat.html | head -1)"
if [ -z "$UI_VER" ]; then
  echo "ERROR: chat.html missing ui-build tag — re-pack from Windows and re-upload holmes-deploy.tar.gz"
  exit 1
fi
if ! grep -qE 'el-page|ccd-root' web/static/chat.html; then
  echo "ERROR: chat.html missing operator UI shell — re-pack from Windows."
  exit 1
fi
echo "Packaged chat UI: $UI_VER (expect agent-v77 + el-page)"
if ! grep -qE 'el-hero-state|ccd-hero|ccd-main' web/static/chat.html; then
  echo "ERROR: chat.html missing hero/chat layout — re-pack from Windows."
  exit 1
fi
if grep -q 'id="holmes-sidebar"' web/static/home.html || grep -q 'id="holmes-sidebar"' web/static/chat.html; then
  echo "ERROR: UI still has old sidebar — re-pack from Windows."
  exit 1
fi
if ! grep -q '_direct_gemini_chat' web/actions.py; then
  echo "ERROR: actions.py missing direct Gemini fallback — re-pack from Windows."
  exit 1
fi
if ! grep -q 'gemini_health' web/server.py; then
  echo "ERROR: web/server.py missing /health/gemini — re-upload from Windows."
  exit 1
fi
if ! grep -q 'holmes_cli_health' web/server.py; then
  echo "ERROR: web/server.py missing /health/holmes — re-pack from Windows."
  exit 1
fi
if ! grep -q 'CHAT_ACTIONS_ENABLED' web/config.py; then
  echo "ERROR: web/config.py missing CHAT_ACTIONS_ENABLED — re-pack from Windows."
  exit 1
fi
if ! grep -q 'demo_apps' web/config.py; then
  echo "ERROR: web/config.py missing demo_apps — re-pack from Windows."
  exit 1
fi
if ! grep -q '_fetch_pods_structured' web/actions.py; then
  echo "ERROR: web/actions.py missing accurate pod telemetry — re-upload from Windows."
  exit 1
fi
if ! grep -q 'NGINX_ARGOCD_APP_YAML' web/config.py; then
  echo "ERROR: config.py missing embedded nginx Argo manifest — re-pack from Windows."
  exit 1
fi

echo "=== 1. ConfigMap overlay (Holmes UI + actions.py) ==="
# delete+create avoids kubectl apply's last-applied-configuration annotation (>256KB limit)
kubectl -n "$NS" delete configmap selfheal-holmes-overlay --ignore-not-found
kubectl -n "$NS" create configmap selfheal-holmes-overlay \
  --from-file=actions.py=web/actions.py \
  --from-file=agent_tools.py=web/agent_tools.py \
  --from-file=failure_modes.py=web/failure_modes.py \
  --from-file=demo_scenarios.py=web/demo_scenarios.py \
  --from-file=chaos_mesh.py=web/chaos_mesh.py \
  --from-file=config.py=web/config.py \
  --from-file=server.py=web/server.py \
  --from-file=demo.html=web/static/demo.html \
  --from-file=demo.js=web/static/demo.js \
  --from-file=home.html=web/static/home.html \
  --from-file=chat.html=web/static/chat.html \
  --from-file=holmes.js=web/static/holmes.js \
  --from-file=ccd.css=web/static/ccd.css \
  --from-file=styles.css=web/static/styles.css \
  --from-file=enlight-lab-mark.png=web/static/assets/enlight-lab-mark.png \
  --from-file=enlight-lab-mark.svg=web/static/assets/enlight-lab-mark.svg \
  --from-file=enlight-lab-logo.png=web/static/assets/enlight-lab-logo.png \
  --from-file=enlight-lab-lockup.png=web/static/assets/enlight-lab-lockup.png \
  --from-file=nginx-staging-app.yaml=deploy/k8s/argocd/nginx-staging-app.yaml

echo "=== 1b. Nginx workload manifests (for in-chat deploy without image rebuild) ==="
kubectl -n "$NS" delete configmap selfheal-nginx-k8s --ignore-not-found
kubectl -n "$NS" create configmap selfheal-nginx-k8s \
  --from-file=deploy/k8s/staging-nginx/

echo "=== 2. Enable Holmes + gemini-2.5-flash ==="
kubectl -n "$NS" patch configmap selfheal-ui-config --type merge -p '{
  "data": {
    "HOLMES_ENABLED": "true",
    "HOLMES_MODE": "cli",
    "HOLMES_MODEL": "gemini/gemini-2.5-flash",
    "HOLMES_CHAT_DIRECT": "true",
    "CHAT_ACTIONS_ENABLED": "true",
    "CHAT_LLM_TARGET": "true",
    "CHAT_MODE": "hybrid",
    "PUBLIC_UI_BASE_URL": "https://selfheal.enlightlab.com",
    "PUBLIC_ARGOCD_HOST": "https://argocd.enlightlab.com",
    "PUBLIC_NGINX_DASHBOARD_URL": "https://selfheal.enlightlab.com/nginx/",
    "PUBLIC_NGINX_HEALTH_URL": "https://selfheal.enlightlab.com/nginx/",
    "TOOL_SCHEMA_NO_PARAM_OBJECT_IF_NO_PARAMS": "true",
    "HOLMES_TIMEOUT": "300",
    "HOLMES_MAX_STEPS": "10",
    "HOLMES_CHAT_MAX_STEPS": "8"
  }
}'

echo "=== 3. Mount overlay on deployment ==="
# Idempotent: skip if volume already present
if kubectl -n "$NS" get deployment selfheal-ui -o jsonpath='{.spec.template.spec.volumes[*].name}' | grep -q holmes-overlay; then
  echo "Volume holmes-overlay already mounted — updating ConfigMap only."
else
  kubectl -n "$NS" patch deployment selfheal-ui --type=json -p='[
    {"op":"add","path":"/spec/template/spec/volumes","value":[{"name":"holmes-overlay","configMap":{"name":"selfheal-holmes-overlay"}}]},
    {"op":"add","path":"/spec/template/spec/containers/0/volumeMounts","value":[
      {"name":"holmes-overlay","mountPath":"/app/web/actions.py","subPath":"actions.py"},
      {"name":"holmes-overlay","mountPath":"/app/web/agent_tools.py","subPath":"agent_tools.py"},
      {"name":"holmes-overlay","mountPath":"/app/web/failure_modes.py","subPath":"failure_modes.py"},
      {"name":"holmes-overlay","mountPath":"/app/web/demo_scenarios.py","subPath":"demo_scenarios.py"},
      {"name":"holmes-overlay","mountPath":"/app/web/chaos_mesh.py","subPath":"chaos_mesh.py"},
      {"name":"holmes-overlay","mountPath":"/app/web/config.py","subPath":"config.py"},
      {"name":"holmes-overlay","mountPath":"/app/web/server.py","subPath":"server.py"},
      {"name":"holmes-overlay","mountPath":"/app/web/static/demo.html","subPath":"demo.html"},
      {"name":"holmes-overlay","mountPath":"/app/web/static/demo.js","subPath":"demo.js"},
      {"name":"holmes-overlay","mountPath":"/app/web/static/home.html","subPath":"home.html"},
      {"name":"holmes-overlay","mountPath":"/app/web/static/chat.html","subPath":"chat.html"},
      {"name":"holmes-overlay","mountPath":"/app/web/static/holmes.js","subPath":"holmes.js"},
      {"name":"holmes-overlay","mountPath":"/app/web/static/ccd.css","subPath":"ccd.css"},
      {"name":"holmes-overlay","mountPath":"/app/web/static/styles.css","subPath":"styles.css"},
      {"name":"holmes-overlay","mountPath":"/app/web/static/assets/enlight-lab-mark.png","subPath":"enlight-lab-mark.png"},
      {"name":"holmes-overlay","mountPath":"/app/web/static/assets/enlight-lab-mark.svg","subPath":"enlight-lab-mark.svg"},
      {"name":"holmes-overlay","mountPath":"/app/web/static/assets/enlight-lab-logo.png","subPath":"enlight-lab-logo.png"},
      {"name":"holmes-overlay","mountPath":"/app/deploy/k8s/argocd/nginx-staging-app.yaml","subPath":"nginx-staging-app.yaml"},
      {"name":"nginx-k8s","mountPath":"/app/deploy/k8s/staging-nginx"}
    ]},
    {"op":"add","path":"/spec/template/spec/volumes/-","value":{"name":"nginx-k8s","configMap":{"name":"selfheal-nginx-k8s"}}}
  ]'
fi

echo "=== 3b. Ensure company logo mounts (existing deployments) ==="
if ! kubectl -n "$NS" get deployment selfheal-ui -o json | grep -q 'enlight-lab-mark.png'; then
  kubectl -n "$NS" patch deployment selfheal-ui --type=json -p='[
    {"op":"add","path":"/spec/template/spec/containers/0/volumeMounts/-","value":
      {"name":"holmes-overlay","mountPath":"/app/web/static/assets/enlight-lab-mark.png","subPath":"enlight-lab-mark.png"}}
  ]' || echo "WARN: could not add mark logo mount — check deployment volumeMounts manually"
fi
if ! kubectl -n "$NS" get deployment selfheal-ui -o json | grep -q 'enlight-lab-mark.svg'; then
  kubectl -n "$NS" patch deployment selfheal-ui --type=json -p='[
    {"op":"add","path":"/spec/template/spec/containers/0/volumeMounts/-","value":
      {"name":"holmes-overlay","mountPath":"/app/web/static/assets/enlight-lab-mark.svg","subPath":"enlight-lab-mark.svg"}}
  ]' || echo "WARN: could not add SVG logo mount — check deployment volumeMounts manually"
fi
if ! kubectl -n "$NS" get deployment selfheal-ui -o json | grep -q 'enlight-lab-logo.png'; then
  kubectl -n "$NS" patch deployment selfheal-ui --type=json -p='[
    {"op":"add","path":"/spec/template/spec/containers/0/volumeMounts/-","value":
      {"name":"holmes-overlay","mountPath":"/app/web/static/assets/enlight-lab-logo.png","subPath":"enlight-lab-logo.png"}}
  ]' || echo "WARN: could not add logo lockup mount — check deployment volumeMounts manually"
fi

echo "=== 3c. Ensure home + chat page mounts (v18) ==="
if ! kubectl -n "$NS" get deployment selfheal-ui -o json | grep -q '/app/web/static/home.html'; then
  kubectl -n "$NS" patch deployment selfheal-ui --type=json -p='[
    {"op":"add","path":"/spec/template/spec/containers/0/volumeMounts/-","value":
      {"name":"holmes-overlay","mountPath":"/app/web/static/home.html","subPath":"home.html"}},
    {"op":"add","path":"/spec/template/spec/containers/0/volumeMounts/-","value":
      {"name":"holmes-overlay","mountPath":"/app/web/static/chat.html","subPath":"chat.html"}}
  ]' || echo "WARN: could not add home/chat mounts — check deployment volumeMounts manually"
fi

echo "=== 3e. Ensure nginx manifest mounts (v19+) ==="
if ! kubectl -n "$NS" get deployment selfheal-ui -o json | grep -q 'nginx-staging-app.yaml'; then
  kubectl -n "$NS" patch deployment selfheal-ui --type=json -p='[
    {"op":"add","path":"/spec/template/spec/volumes/-","value":{"name":"nginx-k8s","configMap":{"name":"selfheal-nginx-k8s"}}},
    {"op":"add","path":"/spec/template/spec/containers/0/volumeMounts/-","value":
      {"name":"holmes-overlay","mountPath":"/app/deploy/k8s/argocd/nginx-staging-app.yaml","subPath":"nginx-staging-app.yaml"}},
    {"op":"add","path":"/spec/template/spec/containers/0/volumeMounts/-","value":
      {"name":"nginx-k8s","mountPath":"/app/deploy/k8s/staging-nginx"}}
  ]' || echo "WARN: could not add nginx manifest mounts — embedded YAML fallback still works"
fi

echo "=== 3h. Ensure failure_modes.py mount (v29 catalog) ==="
if ! kubectl -n "$NS" get deployment selfheal-ui -o json | grep -q '/app/web/failure_modes.py'; then
  kubectl -n "$NS" patch deployment selfheal-ui --type=json -p='[
    {"op":"add","path":"/spec/template/spec/containers/0/volumeMounts/-","value":
      {"name":"holmes-overlay","mountPath":"/app/web/failure_modes.py","subPath":"failure_modes.py"}}
  ]' || echo "WARN: could not add failure_modes.py mount"
fi

echo "=== 3g. Ensure agent_tools.py mount (v27 Engineer mode) ==="
if ! kubectl -n "$NS" get deployment selfheal-ui -o json | grep -q '/app/web/agent_tools.py'; then
  kubectl -n "$NS" patch deployment selfheal-ui --type=json -p='[
    {"op":"add","path":"/spec/template/spec/containers/0/volumeMounts/-","value":
      {"name":"holmes-overlay","mountPath":"/app/web/agent_tools.py","subPath":"agent_tools.py"}}
  ]' || echo "WARN: could not add agent_tools.py mount — check deployment volumeMounts manually"
fi

echo "=== 3i. Ensure demo_scenarios.py + chaos_mesh.py mounts (v41+) ==="
for pyfile in demo_scenarios.py chaos_mesh.py; do
  if ! kubectl -n "$NS" get deployment selfheal-ui -o json | grep -q "/app/web/${pyfile}"; then
    kubectl -n "$NS" patch deployment selfheal-ui --type=json -p="[
      {\"op\":\"add\",\"path\":\"/spec/template/spec/containers/0/volumeMounts/-\",\"value\":
        {\"name\":\"holmes-overlay\",\"mountPath\":\"/app/web/${pyfile}\",\"subPath\":\"${pyfile}\"}}
    ]" || echo "WARN: could not add ${pyfile} mount"
  fi
done

echo "=== 3f. Ensure ccd.css mount (v26 MVP) ==="
if ! kubectl -n "$NS" get deployment selfheal-ui -o json | grep -q '/app/web/static/ccd.css'; then
  kubectl -n "$NS" patch deployment selfheal-ui --type=json -p='[
    {"op":"add","path":"/spec/template/spec/containers/0/volumeMounts/-","value":
      {"name":"holmes-overlay","mountPath":"/app/web/static/ccd.css","subPath":"ccd.css"}}
  ]' || echo "WARN: could not add ccd.css mount — check deployment volumeMounts manually"
fi

echo "=== 3d. Remove stale holmes.html mount (old v17 overlay) ==="
HOLMES_MOUNT_INDEX="$(kubectl -n "$NS" get deployment selfheal-ui -o json \
  | jq '.spec.template.spec.containers[0].volumeMounts
    | map(.mountPath == "/app/web/static/holmes.html")
    | index(true)')"
if [ "${HOLMES_MOUNT_INDEX:-null}" != "null" ]; then
  kubectl -n "$NS" patch deployment selfheal-ui --type=json -p="[
    {\"op\":\"remove\",\"path\":\"/spec/template/spec/containers/0/volumeMounts/${HOLMES_MOUNT_INDEX}\"}
  ]" || echo "WARN: could not remove stale holmes.html mount — patch manually if rollout fails"
fi

show_ui_pods() {
  kubectl -n "$NS" get pods -l app=selfheal-ui -o wide 2>/dev/null || true
}

ready_ui_pod() {
  kubectl -n "$NS" get pods -l app=selfheal-ui \
    --field-selector=status.phase=Running \
    -o jsonpath='{.items[?(@.status.conditions[?(@.type=="Ready")].status=="True")].metadata.name}' 2>/dev/null \
    | awk '{print $1}'
}

unstick_ui_rollout() {
  echo "WARN: rollout stuck — forcing Recreate cleanup (scale 0 → delete pods → scale 1)"
  kubectl -n "$NS" scale deployment selfheal-ui --replicas=0
  sleep 8
  kubectl -n "$NS" delete pods -l app=selfheal-ui --force --grace-period=0 2>/dev/null || true
  sleep 3
  kubectl -n "$NS" scale deployment selfheal-ui --replicas=1
}

wait_ui_rollout() {
  local timeout="${1:-300}"
  if kubectl -n "$NS" rollout status deployment/selfheal-ui --timeout="${timeout}s"; then
    return 0
  fi
  echo ""
  echo "=== Rollout timed out — pod status ==="
  show_ui_pods
  echo ""
  echo "=== Recent events ==="
  kubectl -n "$NS" get events --sort-by=.lastTimestamp 2>/dev/null | tail -n 15 || true
  local stuck
  stuck=$(kubectl -n "$NS" get pods -l app=selfheal-ui --no-headers 2>/dev/null \
    | awk '$3 ~ /Terminating|Pending|CrashLoop|ImagePull|Error|CreateContainer/ {print $1}' | head -n 1)
  if [ -n "${stuck:-}" ]; then
    echo ""
    echo "=== Describe stuck pod: $stuck ==="
    kubectl -n "$NS" describe pod "$stuck" 2>/dev/null | tail -n 40 || true
  fi
  unstick_ui_rollout
  kubectl -n "$NS" rollout status deployment/selfheal-ui --timeout="${timeout}s"
}

echo "=== 4. Rollout ==="
# Recreate strategy: old pod must die before the new one starts. Force-delete if it hangs.
kubectl -n "$NS" rollout restart deployment/selfheal-ui
wait_ui_rollout 300 || {
  echo "ERROR: selfheal-ui did not become ready after recovery attempt."
  show_ui_pods
  echo ""
  echo "Manual fix in Cloud Shell:"
  echo "  kubectl -n $NS scale deployment selfheal-ui --replicas=0"
  echo "  kubectl -n $NS delete pods -l app=selfheal-ui --force --grace-period=0"
  echo "  kubectl -n $NS scale deployment selfheal-ui --replicas=1"
  echo "  kubectl -n $NS rollout status deployment/selfheal-ui --timeout=300s"
  exit 1
}

POD="$(ready_ui_pod)"
for i in $(seq 1 12); do
  if [ -n "${POD:-}" ]; then
    break
  fi
  sleep 5
  POD="$(ready_ui_pod)"
done
if [ -z "${POD:-}" ]; then
  POD=$(kubectl -n "$NS" get pods -l app=selfheal-ui \
    --field-selector=status.phase=Running \
    --sort-by=.metadata.creationTimestamp \
    -o jsonpath='{.items[-1].metadata.name}' 2>/dev/null || true)
fi
echo "Pod: ${POD:-none}"

echo "=== 5. Verify ==="
if [ -z "${POD:-}" ]; then
  echo "ERROR: no running selfheal-ui pod found"
  exit 1
fi
kubectl -n "$NS" exec "$POD" -- grep -o 'agent-v[0-9]*' /app/web/static/chat.html | head -1
kubectl -n "$NS" exec "$POD" -- sh -c 'grep -cE "el-page|ccd-root" /app/web/static/chat.html'
kubectl -n "$NS" exec "$POD" -- sh -c 'grep -c enlight-lab-logo.png /app/web/static/chat.html' && echo "logo lockup in chat.html: ok"
kubectl -n "$NS" exec "$POD" -- test -f /app/web/static/ccd.css && echo "ccd.css mounted: ok"
kubectl -n "$NS" exec "$POD" -- test -f /app/web/failure_modes.py && echo "failure_modes.py mounted: ok"
kubectl -n "$NS" exec "$POD" -- test -f /app/web/demo_scenarios.py && echo "demo_scenarios.py mounted: ok"
kubectl -n "$NS" exec "$POD" -- test -f /app/web/chaos_mesh.py && echo "chaos_mesh.py mounted: ok"
kubectl -n "$NS" exec "$POD" -- test -f /app/web/agent_tools.py && echo "agent_tools.py mounted: ok"
kubectl -n "$NS" exec "$POD" -- sh -c '! grep -q holmes-sidebar /app/web/static/home.html' && echo "no legacy sidebar: ok"

echo ""
echo "Done. Home: https://selfheal.enlightlab.com/  Chat: https://selfheal.enlightlab.com/chat"
echo "Verify: curl -s https://selfheal.enlightlab.com/api/ui-version | jq ."
echo "       (expect ui_build: agent-v77, chat_mvp: true, chat_agent_tools: true)"
echo ""
if [ -f deploy/oci/apply-nginx-staging.sh ]; then
  echo "Nginx GitOps (second app): bash deploy/oci/apply-nginx-staging.sh"
else
  echo "Nginx GitOps: re-pack holmes-deploy.tar.gz from Windows (includes deploy/k8s/), then run apply-nginx-staging.sh"
fi
