#!/bin/bash
# Deploy HolmesGPT UI + backend to live selfheal-ui without a Docker rebuild.
# Overlays repo files via ConfigMap + volume mounts (survives pod restart).
# Run from repo root in Cloud Shell:  bash deploy/oci/deploy-holmes-live.sh
set -eu

NS=selfheal
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

need() {
  if [ ! -f "$1" ]; then
    echo "Missing $1 — upload the latest web/ folder from your Windows repo."
    exit 1
  fi
}

need web/actions.py
need web/config.py
need web/server.py
need web/static/demo.html
need web/static/demo.js
need web/static/holmes.html
need web/static/holmes.js
need web/static/styles.css

if ! grep -q 'holmes-panel' web/static/demo.html; then
  echo "ERROR: web/static/demo.html has no holmes-panel — repo is too old. Re-upload from Windows."
  exit 1
fi
if ! grep -q 'Running HolmesGPT' web/actions.py; then
  echo "ERROR: web/actions.py has no Holmes step — repo is too old. Re-upload from Windows."
  exit 1
fi

echo "=== 1. ConfigMap overlay (Holmes UI + actions.py) ==="
kubectl -n "$NS" create configmap selfheal-holmes-overlay \
  --from-file=actions.py=web/actions.py \
  --from-file=config.py=web/config.py \
  --from-file=server.py=web/server.py \
  --from-file=demo.html=web/static/demo.html \
  --from-file=demo.js=web/static/demo.js \
  --from-file=holmes.html=web/static/holmes.html \
  --from-file=holmes.js=web/static/holmes.js \
  --from-file=styles.css=web/static/styles.css \
  --dry-run=client -o yaml | kubectl apply -f -

echo "=== 2. Enable Holmes + gemini-3.5-flash ==="
kubectl -n "$NS" patch configmap selfheal-ui-config --type merge -p '{
  "data": {
    "HOLMES_ENABLED": "true",
    "HOLMES_MODE": "cli",
    "HOLMES_MODEL": "gemini/gemini-3.5-flash",
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
      {"name":"holmes-overlay","mountPath":"/app/web/config.py","subPath":"config.py"},
      {"name":"holmes-overlay","mountPath":"/app/web/server.py","subPath":"server.py"},
      {"name":"holmes-overlay","mountPath":"/app/web/static/demo.html","subPath":"demo.html"},
      {"name":"holmes-overlay","mountPath":"/app/web/static/demo.js","subPath":"demo.js"},
      {"name":"holmes-overlay","mountPath":"/app/web/static/holmes.html","subPath":"holmes.html"},
      {"name":"holmes-overlay","mountPath":"/app/web/static/holmes.js","subPath":"holmes.js"},
      {"name":"holmes-overlay","mountPath":"/app/web/static/styles.css","subPath":"styles.css"}
    ]}
  ]'
fi

echo "=== 4. Rollout ==="
kubectl -n "$NS" rollout restart deployment/selfheal-ui
kubectl -n "$NS" rollout status deployment/selfheal-ui --timeout=180s

POD=$(kubectl -n "$NS" get pods -l app=selfheal-ui -o jsonpath='{.items[0].metadata.name}')
echo "Pod: $POD"

echo "=== 5. Verify ==="
kubectl -n "$NS" exec "$POD" -- grep -c holmes-panel /app/web/static/demo.html
kubectl -n "$NS" exec "$POD" -- grep -c 'Running HolmesGPT' /app/web/actions.py
kubectl -n "$NS" exec "$POD" -- grep -c '/holmes' /app/web/server.py

echo ""
echo "Done. Open https://selfheal.enlightlab.com/holmes (chat) or /demo (wizard)."
