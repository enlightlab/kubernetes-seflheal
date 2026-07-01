#!/bin/bash
# Install Robusta + HolmesGPT on OKE and wire selfheal-ui to use Robusta Cloud AI (no Gemini/OpenAI key).
#
# Prerequisites:
#   - Tokens from https://platform.robusta.dev (same files as local kind: robusta-secrets.yaml)
#   - helm + kubectl in Cloud Shell
#
# Usage:
#   export ROBUSTA_UI_TOKEN='...'    # from platform.robusta.dev
#   export SIGNING_KEY='...'         # from robusta-secrets.yaml
#   export ROBUSTA_ACCOUNT_ID='...'  # optional; in token JSON / generated_values.yaml
#   bash deploy/oci/setup-robusta-holmes.sh
#
# Or place foundation/robusta/robusta-secrets.yaml and run without exports.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
NS=robusta
RELEASE=robusta
CLUSTER_NAME="${ROBUSTA_CLUSTER_NAME:-enlight-oke-mumbai}"

SECRETS_FILE="${ROBUSTA_SECRETS_FILE:-$ROOT/foundation/robusta/robusta-secrets.yaml}"

echo "=== Robusta + HolmesGPT for selfheal demo (no LLM API keys in selfheal-ui) ==="

if [ -f "$SECRETS_FILE" ]; then
  echo "Applying secrets from $SECRETS_FILE"
  kubectl create namespace "$NS" --dry-run=client -o yaml | kubectl apply -f -
  # Normalize namespace line from Robusta UI download
  sed 's/namespace:.*robusta.*/namespace: robusta/' "$SECRETS_FILE" | kubectl apply -f -
elif [ -n "${ROBUSTA_UI_TOKEN:-}" ] && [ -n "${SIGNING_KEY:-}" ]; then
  kubectl create namespace "$NS" --dry-run=client -o yaml | kubectl apply -f -
  kubectl -n "$NS" create secret generic robusta-secrets \
    --from-literal=ROBUSTA_UI_TOKEN="$ROBUSTA_UI_TOKEN" \
    --from-literal=SIGNING_KEY="$SIGNING_KEY" \
    ${ROBUSTA_ACCOUNT_ID:+--from-literal=ROBUSTA_ACCOUNT_ID="$ROBUSTA_ACCOUNT_ID"} \
    --dry-run=client -o yaml | kubectl apply -f -
else
  echo "ERROR: Set ROBUSTA_UI_TOKEN + SIGNING_KEY, or place $SECRETS_FILE"
  echo "  Download from https://platform.robusta.dev → cluster Install / Verify"
  exit 1
fi

echo ""
echo "=== 1. Helm repo ==="
helm repo add robusta https://robusta-charts.storage.googleapis.com 2>/dev/null || true
helm repo update

echo ""
echo "=== 2. Install / upgrade Robusta (5–10 min) ==="
helm upgrade --install "$RELEASE" robusta/robusta \
  -f "$ROOT/deploy/k8s/robusta/oke-values.yaml" \
  -n "$NS" \
  --set clusterName="$CLUSTER_NAME" \
  --set isSmallCluster=true \
  --wait --timeout 15m

echo ""
echo "=== 3. Wait for Holmes pod ==="
kubectl -n "$NS" wait --for=condition=ready pod -l app.kubernetes.io/name=holmes --timeout=300s 2>/dev/null \
  || kubectl -n "$NS" get pods | grep -i holmes || true

HOLMES_SVC="${RELEASE}-holmes"
HOLMES_URL="http://${HOLMES_SVC}.${NS}.svc.cluster.local/api/chat"

echo ""
echo "=== 4. Wire selfheal-ui to Robusta Holmes HTTP API ==="
kubectl -n selfheal patch configmap selfheal-ui-config --type merge -p "{
  \"data\": {
    \"HOLMES_ENABLED\": \"true\",
    \"HOLMES_MODE\": \"robusta\",
    \"HOLMES_HTTP_URL\": \"${HOLMES_URL}\",
    \"HOLMES_HTTP_MODEL\": \"robusta\",
    \"HOLMES_HTTP_TIMEOUT\": \"300\"
  }
}"
kubectl -n selfheal rollout restart deployment/selfheal-ui
kubectl -n selfheal rollout status deployment/selfheal-ui --timeout=180s || true

echo ""
echo "=== 5. Test from selfheal-ui pod ==="
POD=$(kubectl -n selfheal get pods -l app=selfheal-ui -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -n "$POD" ]; then
  kubectl -n selfheal exec "$POD" -- python3 -c "
import json, urllib.request
url = '${HOLMES_URL}'
body = json.dumps({
    'ask': 'List unhealthy pods in enlight-staging in 2 sentences.',
    'model': 'robusta',
    'behavior_controls': {'todowrite_instructions': False, 'todowrite_reminder': False},
}).encode()
req = urllib.request.Request(url, data=body, headers={'Content-Type': 'application/json'}, method='POST')
with urllib.request.urlopen(req, timeout=120) as r:
    d = json.loads(r.read().decode())
    print((d.get('analysis') or str(d))[:500])
" 2>&1 | tail -15 || echo "WARN: Holmes test failed — check: kubectl -n $NS logs -l app.kubernetes.io/name=holmes --tail=40"
fi

echo ""
echo "=== Done ==="
echo "Holmes service: ${HOLMES_URL}"
echo "Robusta UI:     https://platform.robusta.dev"
echo "Demo:           https://selfheal.enlightlab.com/demo → Step 3 Explain"
echo ""
echo "Rebuild + deploy selfheal-ui image if actions.py changes are not live yet:"
echo "  docker build -f deploy/Dockerfile -t bom.ocir.io/bmitpaosivqx/selfheal-ui:demo-robusta-v1 ."
echo "  docker push bom.ocir.io/bmitpaosivqx/selfheal-ui:demo-robusta-v1"
echo "  kubectl -n selfheal set image deployment/selfheal-ui ui=bom.ocir.io/bmitpaosivqx/selfheal-ui:demo-robusta-v1"
