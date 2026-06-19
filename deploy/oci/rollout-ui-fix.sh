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

echo "=== Restart UI to pull latest image ==="
kubectl -n selfheal rollout restart deployment/selfheal-ui
kubectl -n selfheal rollout status deployment/selfheal-ui --timeout=180s

echo "=== Verify in-cluster kubectl from the pod ==="
kubectl -n selfheal exec deploy/selfheal-ui -- kubectl get nodes
kubectl -n selfheal exec deploy/selfheal-ui -- kubectl auth can-i get nodes

echo "=== Demo URL ==="
kubectl -n selfheal get svc selfheal-ui
