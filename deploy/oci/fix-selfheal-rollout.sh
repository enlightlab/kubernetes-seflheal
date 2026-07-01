#!/bin/bash
# Fix selfheal-ui stuck rollout: bad image tag and/or slow HTTP readiness probes.
set -euo pipefail

NS=selfheal
DEP=selfheal-ui
# Use an image tag that exists in OCIR (change if needed).
UI_IMAGE="${UI_IMAGE:-bom.ocir.io/bmitpaosivqx/selfheal-ui:demo-stable-v1}"

echo "=== Current pods ==="
kubectl -n "$NS" get pods -l app=selfheal-ui -o wide || true

echo ""
echo "=== Replace probes (JSON patch — removes old httpGet handlers) ==="
kubectl -n "$NS" patch deployment "$DEP" --type=json -p='[
  {"op":"replace","path":"/spec/template/spec/containers/0/readinessProbe","value":{"tcpSocket":{"port":30901},"initialDelaySeconds":5,"periodSeconds":10,"timeoutSeconds":3,"failureThreshold":6}},
  {"op":"replace","path":"/spec/template/spec/containers/0/livenessProbe","value":{"tcpSocket":{"port":30901},"initialDelaySeconds":15,"periodSeconds":15,"timeoutSeconds":3,"failureThreshold":5}},
  {"op":"replace","path":"/spec/template/spec/containers/0/startupProbe","value":{"tcpSocket":{"port":30901},"initialDelaySeconds":3,"periodSeconds":3,"failureThreshold":30}}
]'

echo ""
echo "=== Set image to ${UI_IMAGE} ==="
kubectl -n "$NS" set image "deployment/${DEP}" "ui=${UI_IMAGE}"

echo ""
echo "=== Force single replica (Recreate strategy) ==="
kubectl -n "$NS" scale deployment "$DEP" --replicas=0
sleep 5
kubectl -n "$NS" delete pods -l app=selfheal-ui --force --grace-period=0 2>/dev/null || true
kubectl -n "$NS" scale deployment "$DEP" --replicas=1

echo ""
echo "=== Wait for rollout ==="
kubectl -n "$NS" rollout status "deployment/${DEP}" --timeout=300s
kubectl -n "$NS" get pods -l app=selfheal-ui -o wide

UI_IP=$(kubectl -n "$NS" get svc selfheal-ui -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
echo ""
echo "Demo UI: http://${UI_IP}/demo"
