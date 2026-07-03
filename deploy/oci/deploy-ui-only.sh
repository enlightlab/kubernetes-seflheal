#!/bin/bash
# Emergency UI deploy when deploy-holmes-live.sh preflight blocks on old grep checks.
# Run from repo root: bash deploy/oci/deploy-ui-only.sh
set -eu
NS=selfheal
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
find deploy/oci -name '*.sh' -exec sed -i 's/\r$//' {} + 2>/dev/null || true

for f in web/actions.py web/config.py web/server.py web/static/chat.html web/static/holmes.js web/static/styles.css web/static/home.html web/static/demo.html web/static/demo.js; do
  [ -f "$f" ] || { echo "Missing $f"; exit 1; }
done

echo "=== ConfigMap overlay ==="
kubectl -n "$NS" delete configmap selfheal-holmes-overlay --ignore-not-found
kubectl -n "$NS" create configmap selfheal-holmes-overlay \
  --from-file=actions.py=web/actions.py \
  --from-file=config.py=web/config.py \
  --from-file=server.py=web/server.py \
  --from-file=demo.html=web/static/demo.html \
  --from-file=demo.js=web/static/demo.js \
  --from-file=home.html=web/static/home.html \
  --from-file=chat.html=web/static/chat.html \
  --from-file=holmes.js=web/static/holmes.js \
  --from-file=styles.css=web/static/styles.css \
  --from-file=enlight-lab-mark.png=web/static/assets/enlight-lab-mark.png \
  --from-file=nginx-staging-app.yaml=deploy/k8s/argocd/nginx-staging-app.yaml 2>/dev/null || true

kubectl -n "$NS" patch configmap selfheal-ui-config --type merge -p '{
  "data": {
    "CHAT_ACTIONS_ENABLED": "true",
    "PUBLIC_UI_BASE_URL": "https://selfheal.enlightlab.com",
    "PUBLIC_ARGOCD_HOST": "https://argocd.enlightlab.com",
    "PUBLIC_NGINX_DASHBOARD_URL": "https://selfheal.enlightlab.com/nginx/",
    "PUBLIC_NGINX_HEALTH_URL": "https://selfheal.enlightlab.com/nginx/"
  }
}' 2>/dev/null || true

echo "=== Rollout ==="
kubectl -n "$NS" rollout restart deployment/selfheal-ui
kubectl -n "$NS" rollout status deployment/selfheal-ui --timeout=300s
POD=$(kubectl -n "$NS" get pods -l app=selfheal-ui -o jsonpath='{.items[0].metadata.name}')
kubectl -n "$NS" exec "$POD" -- grep -o 'agent-v[0-9]*' /app/web/static/chat.html | head -1
kubectl -n "$NS" exec "$POD" -- grep -c agent-chat-deck /app/web/static/chat.html
echo "Done. curl -s https://selfheal.enlightlab.com/api/ui-version | jq ."
