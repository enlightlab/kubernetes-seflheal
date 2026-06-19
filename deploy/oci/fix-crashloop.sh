#!/bin/bash
# CrashLoopBackOff on OKE: usually ARM image on AMD64 nodes, or ArgoCD still fighting.
set -euo pipefail

GOOD_IMAGE="bom.ocir.io/bmitpaosivqx/enlight-fastapi:demo-pass"

echo "=== Logs from crashing pod (look for 'exec format error') ==="
POD=$(kubectl -n enlight-staging get pods -l app=fastapi -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -n "$POD" ]; then
  kubectl -n enlight-staging logs "$POD" --tail=30 2>&1 || true
  kubectl -n enlight-staging logs "$POD" --previous --tail=30 2>&1 || true
fi

echo ""
echo "=== Pause ArgoCD + delete broken deployment ==="
kubectl -n argocd patch application fastapi-staging --type merge \
  -p '{"spec":{"syncPolicy":{"automated":null}}}' 2>/dev/null || true
kubectl -n enlight-staging delete deployment fastapi --ignore-not-found
kubectl -n enlight-staging delete rs -l app=fastapi --ignore-not-found
kubectl -n enlight-staging delete pods -l app=fastapi --ignore-not-found

echo ""
echo "=== Recreate single-replica deployment ==="
kubectl apply -f deploy/k8s/staging-app/ 2>/dev/null || kubectl apply -f - <<EOF
apiVersion: v1
kind: Namespace
metadata:
  name: enlight-staging
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: fastapi
  namespace: enlight-staging
  labels:
    app: fastapi
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app: fastapi
  template:
    metadata:
      labels:
        app: fastapi
    spec:
      containers:
        - name: api
          image: ${GOOD_IMAGE}
          imagePullPolicy: Always
          ports:
            - containerPort: 8000
          livenessProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 15
            periodSeconds: 10
          readinessProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 10
            periodSeconds: 5
EOF

kubectl -n enlight-staging rollout status deployment/fastapi --timeout=180s || true
kubectl -n enlight-staging get pods -o wide
kubectl -n enlight-staging describe pod -l app=fastapi | tail -25

echo ""
echo "If logs show 'exec format error', rebuild AMD64 on Windows:"
echo "  docker build --platform linux/amd64 -t ${GOOD_IMAGE} ."
echo "  docker push ${GOOD_IMAGE}"
echo "  kubectl -n enlight-staging delete pods -l app=fastapi"
