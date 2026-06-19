#!/bin/bash
# Fix ErrImageNeverPull / ImageInspectError on OKE staging fastapi.
set -euo pipefail

CLUSTER_ID="ocid1.cluster.oc1.ap-mumbai-1.aaaaaaaam6icjl6eveifq64rf6lmqip2yqpuweoctwsiedmiicll75yrq75q"
REGION="ap-mumbai-1"
GOOD_IMAGE="bom.ocir.io/bmitpaosivqx/enlight-fastapi:demo-pass"

oci ce cluster create-kubeconfig \
  --cluster-id "$CLUSTER_ID" \
  --file "$HOME/.kube/config" \
  --region "$REGION" \
  --token-version 2.0.0 \
  --kube-endpoint PUBLIC_ENDPOINT

echo "=== 1. Stop ArgoCD from applying kind/local overlay (imagePullPolicy: Never) ==="
kubectl -n argocd patch application fastapi-staging --type merge \
  -p '{"spec":{"syncPolicy":{"automated":null}}}' 2>/dev/null || true

echo "=== 2. Remove broken pods and old replica sets ==="
kubectl -n enlight-staging delete pods -l app=fastapi --wait=false 2>/dev/null || true
kubectl -n enlight-staging delete rs -l app=fastapi 2>/dev/null || true

echo "=== 3. Apply OKE staging manifest (OCIR image, pull Always) ==="
kubectl apply -f deploy/k8s/staging-app/
kubectl -n enlight-staging patch deployment fastapi --type=json -p="[
  {\"op\":\"replace\",\"path\":\"/spec/replicas\",\"value\":1},
  {\"op\":\"replace\",\"path\":\"/spec/template/spec/containers/0/image\",\"value\":\"${GOOD_IMAGE}\"},
  {\"op\":\"replace\",\"path\":\"/spec/template/spec/containers/0/imagePullPolicy\",\"value\":\"Always\"}
]"

echo "=== 4. Wait for rollout ==="
kubectl -n enlight-staging rollout status deployment/fastapi --timeout=180s
kubectl -n enlight-staging get pods -o wide
kubectl -n enlight-staging describe pod -l app=fastapi | tail -20

echo ""
echo "If still ImageInspectError: rebuild AMD64 image on Windows and push:"
echo "  docker build --platform linux/amd64 -t ${GOOD_IMAGE} D:/enlight-lab-platform/workload/fastapi"
echo "  docker push ${GOOD_IMAGE}"
