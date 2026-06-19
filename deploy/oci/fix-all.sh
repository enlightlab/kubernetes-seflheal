#!/bin/bash
# Fix fastapi + expose ArgoCD + update demo UI links. Run in OCI Cloud Shell.
set -euo pipefail

CLUSTER_ID="ocid1.cluster.oc1.ap-mumbai-1.aaaaaaaam6icjl6eveifq64rf6lmqip2yqpuweoctwsiedmiicll75yrq75q"
REGION="ap-mumbai-1"
GOOD_IMAGE="bom.ocir.io/bmitpaosivqx/enlight-fastapi:demo-pass"
ROOT="${SELFHEAL_ROOT:-$HOME/devops-selfheal}"

oci ce cluster create-kubeconfig \
  --cluster-id "$CLUSTER_ID" \
  --file "$HOME/.kube/config" \
  --region "$REGION" \
  --token-version 2.0.0 \
  --kube-endpoint PUBLIC_ENDPOINT

echo "=== 1. Pause ArgoCD (stops kind/local overlay breaking OKE) ==="
kubectl -n argocd patch application fastapi-staging --type merge \
  -p '{"spec":{"syncPolicy":{"automated":null}}}' 2>/dev/null || true

echo "=== 2. Reset staging fastapi deployment ==="
kubectl -n enlight-staging delete deployment fastapi --ignore-not-found
kubectl -n enlight-staging delete rs -l app=fastapi --ignore-not-found
kubectl -n enlight-staging delete pods -l app=fastapi --ignore-not-found

if [ -d "$ROOT/deploy/k8s/staging-app" ]; then
  kubectl apply -f "$ROOT/deploy/k8s/staging-app/"
else
  kubectl apply -f - <<EOF
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
          readinessProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 10
          livenessProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 15
---
apiVersion: v1
kind: Service
metadata:
  name: fastapi
  namespace: enlight-staging
spec:
  type: LoadBalancer
  selector:
    app: fastapi
  ports:
    - port: 80
      targetPort: 8000
EOF
fi

kubectl -n enlight-staging set image deployment/fastapi api="$GOOD_IMAGE"
kubectl -n enlight-staging rollout status deployment/fastapi --timeout=180s
kubectl -n enlight-staging get pods -o wide

echo "=== 3. Expose ArgoCD UI (LoadBalancer) ==="
kubectl -n argocd patch svc argocd-server -p '{"spec":{"type":"LoadBalancer"}}' 2>/dev/null || \
  kubectl apply -f "$ROOT/deploy/k8s/argocd/argocd-server-lb.yaml" 2>/dev/null || true

echo "Waiting for LoadBalancer IPs (up to 3 min)..."
for i in $(seq 1 18); do
  APP_IP=$(kubectl -n enlight-staging get svc fastapi -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || true)
  ARGO_IP=$(kubectl -n argocd get svc argocd-server -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || true)
  [ -n "$APP_IP" ] && [ -n "$ARGO_IP" ] && break
  sleep 10
done

APP_IP=$(kubectl -n enlight-staging get svc fastapi -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || echo "")
ARGO_IP=$(kubectl -n argocd get svc argocd-server -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || echo "")

echo ""
echo "=== 4. URLs ==="
echo "FastAPI health:  http://${APP_IP:-PENDING}/health"
echo "FastAPI app:     http://${APP_IP:-PENDING}/"
echo "ArgoCD UI:       https://${ARGO_IP:-PENDING}/"
echo "ArgoCD app:      https://${ARGO_IP:-PENDING}/applications/argocd/fastapi-staging"
echo "Self-heal demo:  http://$(kubectl -n selfheal get svc selfheal-ui -o jsonpath='{.status.loadBalancer.ingress[0].ip}')/"

if [ -n "$APP_IP" ] && [ -n "$ARGO_IP" ]; then
  echo "=== 5. Update selfheal UI links ==="
  kubectl -n selfheal patch configmap selfheal-ui-config --type merge -p "{
    \"data\": {
      \"PUBLIC_APP_HEALTH_URL\": \"http://${APP_IP}/health\",
      \"PUBLIC_APP_DASHBOARD_URL\": \"http://${APP_IP}/\",
      \"PUBLIC_ARGOCD_URL\": \"https://${ARGO_IP}\",
      \"PUBLIC_ARGOCD_APP_URL\": \"https://${ARGO_IP}/applications/argocd/fastapi-staging\"
    }
  }" 2>/dev/null || true
  kubectl -n selfheal rollout restart deployment/selfheal-ui 2>/dev/null || true
fi

echo ""
echo "ArgoCD login: admin / $(kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' 2>/dev/null | base64 -d || echo 'see argocd-initial-admin-secret')"
