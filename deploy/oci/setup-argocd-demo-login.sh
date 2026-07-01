#!/bin/bash
# Set a known Argo CD admin password for demos and sync it to the selfheal UI.
# The initial-admin-secret often goes stale (password changed / secret deleted).
#
# Usage (OCI Cloud Shell):
#   export DEMO_ARGOCD_PASSWORD='EnlightDemo2026!'   # optional
#   bash deploy/oci/setup-argocd-demo-login.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DEMO_PASS="${DEMO_ARGOCD_PASSWORD:-EnlightDemo2026!}"

echo "=== 1. RBAC so UI can read login secret ==="
kubectl apply -f "$ROOT/deploy/k8s/selfheal-argocd-rbac.yaml"

echo "=== 2. Set Argo CD admin password (bcrypt in argocd-secret) ==="
if ! kubectl -n argocd get deploy argocd-server &>/dev/null; then
  echo "ERROR: argocd-server not found in namespace argocd"
  exit 1
fi

kubectl -n argocd wait --for=condition=available deployment/argocd-server --timeout=120s

BCRYPT=$(kubectl -n argocd exec deploy/argocd-server -- argocd account bcrypt --password "$DEMO_PASS" | tr -d '\n\r')
MTIME=$(date -u +%Y-%m-%dT%H:%M:%SZ)

python3 - "$BCRYPT" "$MTIME" <<'PY'
import json, sys
bcrypt, mtime = sys.argv[1], sys.argv[2]
patch = {"stringData": {"admin.password": bcrypt, "admin.passwordMtime": mtime}}
with open("/tmp/argocd-secret-patch.json", "w", encoding="utf-8") as f:
    json.dump(patch, f)
PY

kubectl -n argocd patch secret argocd-secret --type merge --patch-file /tmp/argocd-secret-patch.json
rm -f /tmp/argocd-secret-patch.json

echo "=== 3. Store plaintext for demo UI (selfheal namespace) ==="
kubectl -n selfheal create secret generic argocd-demo-login \
  --from-literal=password="$DEMO_PASS" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "=== 4. Restart Argo CD + selfheal UI ==="
kubectl -n argocd rollout restart deployment/argocd-server
kubectl -n argocd rollout status deployment/argocd-server --timeout=180s || true
kubectl -n selfheal rollout restart deployment/selfheal-ui
kubectl -n selfheal rollout status deployment/selfheal-ui --timeout=180s || true

ARGO_IP=$(kubectl -n argocd get svc argocd-server -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || true)
echo ""
echo "OK — Argo CD login:"
echo "  URL:      https://${ARGO_IP:-YOUR_ARGO_LB}/"
echo "  Username: admin"
echo "  Password: $DEMO_PASS"
echo ""
echo "Hard-refresh http://YOUR_UI_IP/demo — password appears in sidebar."
