#!/bin/bash
# Install Chaos Mesh on OKE for Enlight Lab demo (staging namespace experiments only).
# Run in Cloud Shell: bash deploy/oci/setup-chaos-mesh.sh
set -euo pipefail

echo "=== Chaos Mesh install for Enlight Lab ==="

if ! kubectl cluster-info &>/dev/null; then
  echo "ERROR: kubectl cannot reach cluster"
  exit 1
fi

if kubectl get crd podchaos.chaos-mesh.org &>/dev/null; then
  echo "Chaos Mesh CRDs already present — skipping helm install"
else
  echo "Adding Chaos Mesh helm repo..."
  helm repo add chaos-mesh https://charts.chaos-mesh.org 2>/dev/null || true
  helm repo update
  echo "Installing Chaos Mesh (controller only, ~2 min)..."
  helm upgrade --install chaos-mesh chaos-mesh/chaos-mesh \
    --namespace chaos-mesh --create-namespace \
    --set chaosDaemon.runtime=containerd \
    --set chaosDaemon.socketPath=/run/containerd/containerd.sock \
    --set dashboard.create=false \
    --set controllerManager.replicaCount=1 \
    --wait --timeout 5m
fi

echo "Waiting for chaos-controller-manager..."
kubectl -n chaos-mesh rollout status deployment/chaos-controller-manager --timeout=180s

echo "Granting selfheal-ui permission to manage chaos experiments in enlight-staging..."
kubectl apply -f - <<'EOF'
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: enlight-chaos-demo
  namespace: enlight-staging
rules:
  - apiGroups: ["chaos-mesh.org"]
    resources: ["*"]
    verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: selfheal-chaos-demo
  namespace: enlight-staging
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: enlight-chaos-demo
subjects:
  - kind: ServiceAccount
    name: selfheal-ui
    namespace: selfheal
EOF

echo ""
echo "Chaos Mesh ready. Verify:"
echo "  kubectl get pods -n chaos-mesh"
echo "  curl -s https://selfheal.enlightlab.com/api/chaos/status | jq ."
echo ""
echo "Try in chat: 'DNS failure and network delay on nginx'"
