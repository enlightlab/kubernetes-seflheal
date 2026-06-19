#!/bin/bash
# Run this in OCI Cloud Shell after uploading/cloning devops-selfheal
set -euo pipefail

CLUSTER_ID="ocid1.cluster.oc1.ap-mumbai-1.aaaaaaaam6icjl6eveifq64rf6lmqip2yqpuweoctwsiedmiicll75yrq75q"
REGION="ap-mumbai-1"
OCIR="bom.ocir.io/bmitpaosivqx"
OCIR_USER="bmitpaosivqx/kirti@enlightlab.com"

echo "=== 1. Connect kubectl to OKE ==="
mkdir -p ~/.kube
oci ce cluster create-kubeconfig \
  --cluster-id "$CLUSTER_ID" \
  --file "$HOME/.kube/config" \
  --region "$REGION" \
  --token-version 2.0.0 \
  --kube-endpoint PUBLIC_ENDPOINT

kubectl get nodes

echo ""
echo "=== 2. Docker login to OCIR ==="
echo "Run: docker login $OCIR -u $OCIR_USER"
echo "(Password = Auth Token from OCI Console → Profile → Auth Tokens)"
read -r -p "Press Enter after docker login succeeds..."

SELFHEAL_ROOT="${SELFHEAL_ROOT:-$HOME/devops-selfheal}"
ENLIGHT_ROOT="${ENLIGHT_ROOT:-$HOME/enlight-lab-platform}"

if [ ! -d "$SELFHEAL_ROOT/deploy" ]; then
  echo "ERROR: devops-selfheal not found at $SELFHEAL_ROOT"
  echo "Upload zip or: git clone <your-repo-url> $SELFHEAL_ROOT"
  exit 1
fi

if [ ! -d "$ENLIGHT_ROOT/workload/fastapi" ]; then
  echo "Cloning enlight-lab-platform..."
  git clone https://github.com/kirtiprasad2003/enlight-lab-platform.git "$ENLIGHT_ROOT"
fi

echo ""
echo "=== 3. Build & push images ==="
docker build --platform linux/amd64 -f "$SELFHEAL_ROOT/deploy/Dockerfile" -t "$OCIR/selfheal-ui:latest" "$SELFHEAL_ROOT"
docker push "$OCIR/selfheal-ui:latest"

docker build -f "$ENLIGHT_ROOT/workload/fastapi/Dockerfile" -t "$OCIR/enlight-fastapi:demo-pass" "$ENLIGHT_ROOT/workload/fastapi"
docker push "$OCIR/enlight-fastapi:demo-pass"

echo ""
echo "=== 4. Deploy staging app ==="
kubectl apply -f "$SELFHEAL_ROOT/deploy/k8s/staging-app/"

echo ""
echo "=== 5. Install ArgoCD ==="
kubectl get ns argocd 2>/dev/null || kubectl create namespace argocd
if ! kubectl -n argocd get deploy argocd-server &>/dev/null; then
  kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
  kubectl -n argocd wait --for=condition=available deployment/argocd-server --timeout=300s
fi
kubectl apply -f "$SELFHEAL_ROOT/deploy/k8s/argocd/"

echo ""
echo "=== 6. Deploy self-heal UI ==="
kubectl apply -f "$SELFHEAL_ROOT/deploy/k8s/selfheal-ui.yaml"

echo ""
echo "=== 7. Get public URLs ==="
echo "Waiting for LoadBalancer IPs (1-3 min)..."
sleep 30
echo ""
echo "--- Self-heal demo UI (open in browser) ---"
kubectl -n selfheal get svc selfheal-ui
echo ""
echo "--- Staging app ---"
kubectl -n enlight-staging get svc fastapi
echo ""
echo "Update PUBLIC_* URLs in ConfigMap after you have the IPs:"
echo "  kubectl -n selfheal edit configmap selfheal-ui-config"
