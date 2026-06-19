#!/bin/bash
# Configure k8sgpt AI inside the selfheal-ui pod. Run in OCI Cloud Shell.
# Usage: read -s ANTHROPIC_API_KEY; echo; ./setup-k8sgpt-auth.sh
set -euo pipefail

NS=selfheal
KEY="${ANTHROPIC_API_KEY:-${OPENAI_API_KEY:-}}"

if [ -z "$KEY" ]; then
  echo "Set ANTHROPIC_API_KEY first, e.g.:"
  echo "  read -s ANTHROPIC_API_KEY; export ANTHROPIC_API_KEY; echo"
  exit 1
fi

kubectl -n "$NS" create secret generic k8sgpt-ai \
  --from-literal=anthropic-api-key="$KEY" \
  --dry-run=client -o yaml | kubectl apply -f -

POD=$(kubectl -n "$NS" get pods -l app=selfheal-ui -o jsonpath='{.items[0].metadata.name}')
if [ -z "$POD" ]; then
  echo "No selfheal-ui pod found"
  exit 1
fi

# Anthropic API via OpenAI-compatible localai backend (k8sgpt 0.4.x)
kubectl -n "$NS" exec "$POD" -- k8sgpt auth remove --backend localai 2>/dev/null || true
kubectl -n "$NS" exec "$POD" -- k8sgpt auth add \
  --backend localai \
  --baseurl https://api.anthropic.com/v1 \
  --model claude-3-5-haiku-20241022 \
  --password "$KEY"

kubectl -n "$NS" exec "$POD" -- k8sgpt auth default -p localai

echo "Testing analyze (no AI)..."
kubectl -n "$NS" exec "$POD" -- k8sgpt analyze --namespace enlight-staging --no-cache | head -20

echo ""
echo "Done. Click Explain with AI on http://141.148.192.40/"
echo "If explain still fails, use demo mode: kubectl -n $NS exec $POD -- k8sgpt auth add -b noopai"
