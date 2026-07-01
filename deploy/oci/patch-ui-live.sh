#!/bin/bash
# DEPRECATED — this REMOVES Holmes. Use deploy/oci/deploy-holmes-live.sh instead.
# Patch running selfheal-ui pod: remove HolmesGPT labels + Mumbai text (no Docker rebuild).
set -euPOD=$(kubectl -n selfheal get pods -l app=selfheal-ui -o jsonpath='{.items[0].metadata.name}')
echo "Patching pod: $POD"

kubectl exec -n selfheal "$POD" -- sh -c '
  for f in /app/web/static/demo.html /app/web/static/demo.js /app/web/static/index.html; do
    [ -f "$f" ] || continue
    sed -i "s/HolmesGPT/k8sgpt/g" "$f"
    sed -i "s/HolmesGPT + k8sgpt/k8sgpt/g" "$f"
    sed -i "s/AI diagnosis · k8sgpt + k8sgpt/AI diagnosis · k8sgpt/g" "$f"
    sed -i "s/ (Mumbai)//g" "$f"
    sed -i "s/Mumbai region/Managed OKE cluster/g" "$f"
    sed -i "s/worker nodes in Mumbai region/worker nodes in your cloud region/g" "$f"
  done
  # Remove holmes panel block if present (legacy HTML)
  sed -i "/holmes-panel/,+6d" /app/web/static/demo.html 2>/dev/null || true
'

kubectl -n selfheal patch configmap selfheal-ui-config --type merge \
  -p '{"data":{"HOLMES_ENABLED":"false","AUTO_DEPLOY_ON_LOAD":"false"}}' 2>/dev/null || true

echo "Done. Hard-refresh https://selfheal.enlightlab.com/demo (Ctrl+Shift+R)"
echo "Pipeline node should show k8sgpt, not HolmesGPT."
