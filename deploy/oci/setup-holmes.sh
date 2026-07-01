#!/bin/bash
# Enable HolmesGPT for Step 3 Explain in selfheal-ui. Run in OCI Cloud Shell.
set -eu

NS=selfheal
KEY="${GEMINI_API_KEY:-${ANTHROPIC_API_KEY:-${OPENAI_API_KEY:-}}}"

if [ -z "$KEY" ]; then
  echo "Set one of GEMINI_API_KEY, ANTHROPIC_API_KEY, or OPENAI_API_KEY first:"
  echo "  read -s GEMINI_API_KEY; export GEMINI_API_KEY; echo"
  echo "  bash deploy/oci/setup-holmes.sh"
  exit 1
fi

if [ -n "${GEMINI_API_KEY:-}" ]; then
  case "$GEMINI_API_KEY" in
    *" "*|*"bash"*|*"kubectl"*|*"export"*)
      echo "ERROR: GEMINI_API_KEY looks corrupted (contains spaces or shell command text)."
      echo "Run ONLY:  read -s GEMINI_API_KEY; export GEMINI_API_KEY; echo"
      echo "Then paste the key alone — no command, no quotes, no trailing text."
      exit 1
      ;;
  esac
  case "$GEMINI_API_KEY" in
    AIza*|AQ.*)
      ;;
    *)
      echo "WARN: Expected Google key to start with AIza or AQ. (new auth keys from AI Studio)."
      ;;
  esac
  if [ "${#GEMINI_API_KEY}" -gt 120 ]; then
    echo "ERROR: GEMINI_API_KEY is ${#GEMINI_API_KEY} chars — likely pasted with export/command text."
    echo "Key alone is usually 40–80 chars. Use a file:  nano /tmp/gemini.key  (paste key only), then:"
    echo "  export GEMINI_API_KEY=\$(tr -d '\\n\\r' < /tmp/gemini.key); rm -f /tmp/gemini.key"
    exit 1
  fi
fi

echo "=== 1. Update k8sgpt-ai secret ==="
if [ -n "${GEMINI_API_KEY:-}" ]; then
  kubectl -n "$NS" create secret generic k8sgpt-ai \
    --from-literal=gemini-api-key="$GEMINI_API_KEY" \
    --dry-run=client -o yaml | kubectl apply -f -
  HOLMES_MODEL="gemini/gemini-3.5-flash"
elif [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  kubectl -n "$NS" create secret generic k8sgpt-ai \
    --from-literal=anthropic-api-key="$ANTHROPIC_API_KEY" \
    --dry-run=client -o yaml | kubectl apply -f -
  HOLMES_MODEL="anthropic/claude-3-5-haiku-20241022"
else
  kubectl -n "$NS" create secret generic k8sgpt-ai \
    --from-literal=openai-api-key="$OPENAI_API_KEY" \
    --dry-run=client -o yaml | kubectl apply -f -
  HOLMES_MODEL="openai/gpt-4o-mini"
fi

echo "=== 2. ConfigMap — Holmes model + enabled ==="
EXTRA_ENV=""
if [[ "$HOLMES_MODEL" == gemini/* ]]; then
  EXTRA_ENV=", \"TOOL_SCHEMA_NO_PARAM_OBJECT_IF_NO_PARAMS\": \"true\""
fi
kubectl -n "$NS" patch configmap selfheal-ui-config --type merge -p "{
  \"data\": {
    \"HOLMES_ENABLED\": \"true\",
    \"HOLMES_MODE\": \"cli\",
    \"HOLMES_MODEL\": \"${HOLMES_MODEL}\"${EXTRA_ENV},
    \"HOLMES_TIMEOUT\": \"300\",
    \"HOLMES_MAX_STEPS\": \"10\"
  }
}"

echo "=== 3. Restart UI pod (pick up secret + config) ==="
kubectl -n "$NS" rollout restart deployment/selfheal-ui
kubectl -n "$NS" rollout status deployment/selfheal-ui --timeout=180s

POD=$(kubectl -n "$NS" get pods -l app=selfheal-ui -o jsonpath='{.items[0].metadata.name}')
echo "Pod: $POD"

echo ""
echo "=== 4. Test Holmes inside pod ==="
# Older kubectl lacks "exec -e"; use env(1) inside the pod (TOOL_SCHEMA_* is also set on the deployment).
HOLMES_ASK="holmes ask \"What is wrong with fastapi pods in namespace enlight-staging? Reply in 3 sentences.\" --model $HOLMES_MODEL"
if [[ "$HOLMES_MODEL" == gemini/* ]]; then
  HOLMES_ASK="env TOOL_SCHEMA_NO_PARAM_OBJECT_IF_NO_PARAMS=true $HOLMES_ASK"
fi
if kubectl -n "$NS" exec "$POD" -- which holmes >/dev/null 2>&1; then
  kubectl -n "$NS" exec "$POD" -- sh -c "$HOLMES_ASK" | tail -25
else
  echo "WARN: holmes CLI not in this image — pip install or deploy demo-holmes-v1 image"
  kubectl -n "$NS" exec "$POD" -- pip install holmesgpt
  kubectl -n "$NS" exec "$POD" -- sh -c "$HOLMES_ASK" | tail -25
fi

echo ""
echo "Done. Run Step 3 Explain on https://selfheal.enlightlab.com/demo"
