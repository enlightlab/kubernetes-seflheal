#!/bin/bash
# Proper Argo CD exposure: Ingress + TLS on argocd.enlightlab.com (not a raw LB IP).
set -euo pipefail

ROOT="${ROOT:-$HOME/devops-selfheal}"
ARGO_HOST="${ARGO_HOST:-argocd.enlightlab.com}"
SELFHEAL_HOST="${SELFHEAL_HOST:-selfheal.enlightlab.com}"
INGRESS_IP="${INGRESS_IP:-144.24.100.85}"

echo "=== Argo CD Ingress setup ==="
echo "Host: https://${ARGO_HOST}"
echo "Expected DNS A record: ${ARGO_HOST} -> ${INGRESS_IP}"
echo ""

if ! kubectl -n argocd get deploy argocd-server &>/dev/null; then
  echo "ERROR: argocd-server not found in namespace argocd"
  exit 1
fi

echo "=== 1. Argo CD server.insecure=true (required behind nginx Ingress) ==="
if kubectl -n argocd get configmap argocd-cmd-params-cm &>/dev/null; then
  kubectl -n argocd patch configmap argocd-cmd-params-cm --type merge \
    -p '{"data":{"server.insecure":"true"}}'
else
  echo "WARN: argocd-cmd-params-cm not found — if login fails, set server.insecure manually"
fi
kubectl -n argocd rollout restart deployment/argocd-server
kubectl -n argocd rollout status deployment/argocd-server --timeout=180s

echo ""
echo "=== 2. TLS certificate (cert-manager) ==="
ISSUER=$(kubectl get clusterissuer -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$ISSUER" ]; then
  ISSUER=$(kubectl -n selfheal get certificate -o jsonpath='{.items[0].spec.issuerRef.name}' 2>/dev/null || true)
fi
if [ -n "$ISSUER" ]; then
  echo "Using ClusterIssuer: $ISSUER"
  sed "s/letsencrypt-prod/${ISSUER}/" "$ROOT/deploy/k8s/argocd/argocd-certificate.yaml" | kubectl apply -f -
else
  echo "No ClusterIssuer found — apply argocd-certificate.yaml manually or create TLS secret"
  kubectl apply -f "$ROOT/deploy/k8s/argocd/argocd-certificate.yaml" 2>/dev/null || true
fi

echo ""
echo "=== 3. Ingress ==="
kubectl apply -f "$ROOT/deploy/k8s/argocd/argocd-ingress.yaml"
kubectl -n argocd get ingress argocd-server-ingress

echo ""
echo "=== 4. Update selfheal-ui public links ==="
kubectl -n selfheal patch configmap selfheal-ui-config --type merge -p "{
  \"data\": {
    \"PUBLIC_APP_DASHBOARD_URL\": \"https://${SELFHEAL_HOST}/staging/\",
    \"PUBLIC_APP_HEALTH_URL\": \"https://${SELFHEAL_HOST}/staging/health\",
    \"PUBLIC_ARGOCD_URL\": \"https://${ARGO_HOST}\",
    \"PUBLIC_ARGOCD_APP_URL\": \"https://${ARGO_HOST}/applications/argocd/fastapi-staging\"
  }
}"
kubectl -n selfheal rollout restart deployment/selfheal-ui
kubectl -n selfheal rollout status deployment/selfheal-ui --timeout=180s || true

echo ""
echo "=== 5. Verify ==="
echo "Ensure DNS: ${ARGO_HOST} -> ${INGRESS_IP}"
echo ""
echo "Wait for cert (if cert-manager):"
echo "  kubectl -n argocd get certificate argocd-enlightlab-tls -w"
echo ""
echo "Open:"
echo "  Demo:     https://${SELFHEAL_HOST}/demo"
echo "  Argo CD:  https://${ARGO_HOST}"
echo "  App:      https://${ARGO_HOST}/applications/argocd/fastapi-staging"
echo ""
echo "Login: admin + password from demo sidebar (setup-argocd-demo-login.sh)"
