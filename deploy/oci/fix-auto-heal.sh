#!/bin/bash
# Fix Auto-fix app button (RBAC + restore fastapi + restart UI)
set -euo pipefail

GOOD_IMAGE="bom.ocir.io/bmitpaosivqx/enlight-fastapi:demo-pass"

echo "=== 1. Grant demo RBAC (Auto-fix needs deployment patch) ==="
kubectl apply -f deploy/k8s/selfheal-ui.yaml 2>/dev/null || true
kubectl apply -f deploy/k8s/selfheal-admin-binding.yaml

echo "=== 2. Pause ArgoCD auto-sync ==="
kubectl -n argocd patch application fastapi-staging --type merge \
  -p '{"spec":{"syncPolicy":{"automated":null}}}' 2>/dev/null || true

echo "=== 3. Restore staging fastapi ==="
kubectl -n enlight-staging delete deployment fastapi --ignore-not-found 2>/dev/null || true
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
          readinessProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 10
---
apiVersion: v1
kind: Service
metadata:
  name: fastapi
  namespace: enlight-staging
spec:
  type: ClusterIP
  selector:
    app: fastapi
  ports:
    - port: 80
      targetPort: 8000
EOF

kubectl -n enlight-staging set image deployment/fastapi api="$GOOD_IMAGE"
kubectl -n enlight-staging rollout status deployment/fastapi --timeout=120s
kubectl -n enlight-staging get pods

echo "=== 4. Restart selfheal-ui (pull latest image if pushed) ==="
kubectl -n selfheal rollout restart deployment/selfheal-ui
kubectl -n selfheal rollout status deployment/selfheal-ui --timeout=300s

UI_IP=$(kubectl -n selfheal get svc selfheal-ui -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
echo ""
echo "Demo: http://${UI_IP}/"
echo "Test heal from pod:"
kubectl -n selfheal exec deploy/selfheal-ui -- kubectl -n enlight-staging get pods
