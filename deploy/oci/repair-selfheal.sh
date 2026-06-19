#!/bin/bash
# Repair selfheal-ui when http://EXTERNAL-IP/ returns ERR_CONNECTION_RESET
set -euo pipefail

echo "=== Current state ==="
kubectl -n selfheal get pods,svc,endpoints
kubectl -n selfheal get svc selfheal-ui -o wide

UI_IP=$(kubectl -n selfheal get svc selfheal-ui -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
echo "selfheal-ui EXTERNAL-IP: ${UI_IP:-<none>}"

echo ""
echo "=== Force-recreate selfheal-ui pod ==="
kubectl -n selfheal scale deployment selfheal-ui --replicas=0
sleep 5
kubectl -n selfheal delete pods -l app=selfheal-ui --force --grace-period=0 2>/dev/null || true
kubectl -n selfheal scale deployment selfheal-ui --replicas=1
kubectl -n selfheal rollout status deployment/selfheal-ui --timeout=300s

echo ""
echo "=== Endpoints (must show pod IP:30901) ==="
kubectl -n selfheal get endpoints selfheal-ui

echo ""
echo "=== In-cluster curl ==="
kubectl -n selfheal run curl-test --rm -i --restart=Never --image=curlimages/curl:latest -- \
  curl -sS -m 10 "http://selfheal-ui.selfheal.svc.cluster.local/healthz" || true

echo ""
echo "=== Public curl (from Cloud Shell) ==="
if [ -n "${UI_IP:-}" ]; then
  curl -sS -m 10 "http://${UI_IP}/healthz" && echo ""
  curl -sS -m 10 "http://${UI_IP}/staging/health" && echo ""
fi

ARGO_IP=$(kubectl -n argocd get svc argocd-server -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
echo ""
echo "Open in browser:"
echo "  Demo UI:    http://${UI_IP}/"
echo "  App health: http://${UI_IP}/staging/health"
echo "  ArgoCD:     https://${ARGO_IP}/"
