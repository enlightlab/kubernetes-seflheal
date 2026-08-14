#!/bin/bash
# ONE COMMAND after uploading holmes-deploy.tar.gz to Cloud Shell home directory.
# Paste this entire block into Cloud Shell:
#
#   cd ~ && rm -rf devops-selfheal && mkdir devops-selfheal && \
#   tar -xzf ~/holmes-deploy.tar.gz -C devops-selfheal && \
#   cd ~/devops-selfheal && bash deploy/oci/hotfix-live-now.sh
#
set -eu
NS=selfheal
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

echo "=== Self-Heal hotfix deploy from $ROOT ==="
UI_TAG="$(grep -o 'agent-v[0-9]*' web/static/chat.html | head -1)"
echo "Packaged UI tag: ${UI_TAG:-unknown}"
[ -n "$UI_TAG" ] || { echo "ERROR: not in repo root"; exit 1; }

bash deploy/oci/deploy-holmes-live.sh

echo ""
echo "=== Post-deploy smoke test ==="
sleep 5
curl -fsS https://selfheal.enlightlab.com/api/ui-version | head -c 200; echo ""
curl -fsS https://selfheal.enlightlab.com/api/holmes/snapshot | head -c 120; echo ""
echo "If ui_build matches ${UI_TAG} and snapshot has ok:true — hard refresh browser (Ctrl+Shift+R)"
