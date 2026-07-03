#!/bin/bash
# Refresh nginx-staging in Argo CD after Git path is on main.
set -eu
kubectl -n argocd annotate application nginx-staging argocd.argoproj.io/refresh=hard --overwrite
kubectl -n argocd patch application nginx-staging --type merge -p \
  '{"operation":{"initiatedBy":{"username":"selfheal-ui"},"sync":{"revision":"HEAD"}}}' 2>/dev/null || true
sleep 8
kubectl -n argocd get application nginx-staging -o jsonpath='sync={.status.sync.status} health={.status.health.status}{"\n"}' 2>/dev/null || true
echo "Open Argo CD → nginx-staging → Sync. Path: demos/nginx-staging/overlays/oci"
