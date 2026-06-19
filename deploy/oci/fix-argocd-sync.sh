#!/bin/bash
# Fix ArgoCD always OutOfSync: use OKE oci overlay + sync + ignore image drift for demo.
set -euo pipefail

echo "=== 1. Apply ArgoCD Application (oci overlay + ignoreDifferences) ==="
kubectl apply -f - <<'EOF'
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: fastapi-staging
  namespace: argocd
spec:
  project: default
  source:
    repoURL: https://github.com/kirtiprasad2003/enlight-lab-platform.git
    targetRevision: main
    path: demos/demo2-chat-to-deploy/overlays/oci
  destination:
    server: https://kubernetes.default.svc
    namespace: enlight-staging
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
  ignoreDifferences:
    - group: apps
      kind: Deployment
      name: fastapi
      jsonPointers:
        - /spec/template/spec/containers/0/image
    - kind: Service
      name: fastapi
      jsonPointers:
        - /metadata/labels
        - /spec/clusterIP
EOF

echo "=== 2. Hard refresh + sync ==="
kubectl -n argocd annotate application fastapi-staging argocd.argoproj.io/refresh=hard --overwrite
kubectl -n argocd patch application fastapi-staging --type merge -p '{
  "operation": {
    "initiatedBy": {"username": "cloudshell"},
    "sync": {"revision": "HEAD"}
  }
}'

echo "Waiting 30s for sync..."
sleep 30
kubectl -n argocd get application fastapi-staging \
  -o jsonpath='Sync={.status.sync.status} Health={.status.health.status}{"\n"}'

echo ""
echo "NOTE: overlays/oci must exist on GitHub main. If sync fails, run on laptop:"
echo "  cd D:\\enlight-lab-platform"
echo "  git add demos/demo2-chat-to-deploy/overlays/oci"
echo "  git commit -m 'Add OKE oci overlay for fastapi staging'"
echo "  git push"
echo ""
echo "Open: https://80.225.201.11/applications/argocd/fastapi-staging"
