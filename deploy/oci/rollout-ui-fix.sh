#!/bin/bash
# Apply in OCI Cloud Shell after pushing a new selfheal-ui image.
set -euo pipefail

CLUSTER_ID="ocid1.cluster.oc1.ap-mumbai-1.aaaaaaaam6icjl6eveifq64rf6lmqip2yqpuweoctwsiedmiicll75yrq75q"
REGION="ap-mumbai-1"
ROOT="${SELFHEAL_ROOT:-$HOME/devops-selfheal}"

mkdir -p ~/.kube
oci ce cluster create-kubeconfig \
  --cluster-id "$CLUSTER_ID" \
  --file "$HOME/.kube/config" \
  --region "$REGION" \
  --token-version 2.0.0 \
  --kube-endpoint PUBLIC_ENDPOINT

kubectl apply -f "$ROOT/deploy/k8s/selfheal-ui.yaml"
kubectl apply -f "$ROOT/deploy/k8s/staging-app/"

echo "=== Deploy UI image (use explicit tag — not just rollout restart) ==="
UI_IMAGE="${UI_IMAGE:-bom.ocir.io/bmitpaosivqx/selfheal-ui:ui-20260619}"
kubectl -n selfheal set image deployment/selfheal-ui ui="$UI_IMAGE"
kubectl -n selfheal rollout status deployment/selfheal-ui --timeout=180s

echo "=== Verify HTML served ==="
UI_IP=$(kubectl -n selfheal get svc selfheal-ui -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
curl -s "http://${UI_IP}/" | grep -q "How fast can your team" && echo "OK: new UI live at http://${UI_IP}/" || echo "WARN: still old UI — hard-refresh browser or check image tag"

echo "=== Verify in-cluster kubectl from the pod ==="
kubectl -n selfheal exec deploy/selfheal-ui -- kubectl get nodes
kubectl -n selfheal exec deploy/selfheal-ui -- kubectl auth can-i get nodes

echo "=== Demo URL ==="
kubectl -n selfheal get svc selfheal-ui
