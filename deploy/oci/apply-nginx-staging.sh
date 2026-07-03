#!/bin/bash
# Register nginx-staging in Argo CD and apply manifests from this repo checkout.
# Run from repo root in Cloud Shell:
#   bash deploy/oci/apply-nginx-staging.sh
set -eu

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

find deploy/oci -name '*.sh' -exec sed -i 's/\r$//' {} + 2>/dev/null || true

need() {
  if [ ! -f "$1" ]; then
    echo "Missing $1"
    echo "Re-pack on Windows and re-upload holmes-deploy.tar.gz, or git pull the latest repo."
    exit 1
  fi
}

need deploy/k8s/argocd/nginx-staging-app.yaml
need deploy/k8s/staging-nginx/deployment.yaml

echo "=== 1. Apply nginx workload manifests (immediate) ==="
kubectl apply -f deploy/k8s/staging-nginx/

echo ""
echo "=== 2. Register Argo CD Application nginx-staging ==="
kubectl apply -f deploy/k8s/argocd/nginx-staging-app.yaml

echo ""
echo "=== 3. Force good image (recover from outage / ImageInspectError) ==="
GOOD_IMAGE="${NGINX_GOOD_IMAGE:-docker.io/library/nginx:1.27-alpine}"
kubectl -n enlight-staging set image "deployment/nginx-demo" "nginx=${GOOD_IMAGE}"
kubectl -n enlight-staging patch deployment nginx-demo --type=json -p='[
  {"op":"replace","path":"/spec/template/spec/containers/0/imagePullPolicy","value":"Always"}
]' 2>/dev/null || true
kubectl -n enlight-staging delete pods -l app=nginx-demo --force --grace-period=0 2>/dev/null || true
sleep 5

echo ""
echo "=== 4. Trigger Argo sync (Git path: demos/nginx-staging/overlays/oci) ==="
kubectl -n argocd annotate application nginx-staging argocd.argoproj.io/refresh=hard --overwrite 2>/dev/null || true
kubectl -n argocd patch application nginx-staging --type merge -p \
  '{"operation":{"initiatedBy":{"username":"selfheal-ui"},"sync":{"revision":"HEAD"}}}' 2>/dev/null || true
sleep 8
kubectl -n argocd get application nginx-staging \
  -o jsonpath='sync={.status.sync.status} health={.status.health.status}{"\n"}' 2>/dev/null || true

echo ""
echo "Current image on deployment:"
kubectl -n enlight-staging get deployment nginx-demo \
  -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}' 2>/dev/null || true

echo ""
echo "=== 5. Wait for pod ==="
for i in $(seq 1 30); do
  LINE=$(kubectl -n enlight-staging get pods -l app=nginx-demo --no-headers 2>/dev/null | head -1)
  if echo "$LINE" | grep -q '1/1.*Running'; then
    echo "OK: $LINE"
    break
  fi
  echo "  waiting... ${LINE:-no pod yet}"
  sleep 5
done

echo ""
echo "=== 6. Status ==="
kubectl -n argocd get application nginx-staging -o wide 2>/dev/null || true
kubectl -n enlight-staging get pods,svc,ingress -l app=nginx-demo 2>/dev/null || \
  kubectl -n enlight-staging get pods,svc -l app=nginx-demo

UI_IP=$(kubectl -n selfheal get svc selfheal-ui -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || true)
echo ""
echo "Nginx UI (via selfheal LB): http://${UI_IP:-selfheal.enlightlab.com}/nginx/"
echo "FastAPI UI:                 http://${UI_IP:-selfheal.enlightlab.com}/staging/"
echo ""
echo "NOTE: Argo CD syncs demos/nginx-staging/overlays/oci from enlight-lab-platform (same repo as fastapi-staging)."
echo "      Copy demos/nginx-staging/ to that GitHub repo main branch, then refresh nginx-staging in Argo CD."
