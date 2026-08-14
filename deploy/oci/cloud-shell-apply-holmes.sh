#!/bin/bash
# Apply Holmes overlay on OKE from a tar.gz uploaded to Cloud Shell home.
# Usage (after upload holmes-deploy.tar.gz to ~/):
#   cd ~ && bash cloud-shell-apply-holmes.sh
# Or if tarball extracted already:
#   cd ~/devops-selfheal && bash deploy/oci/deploy-holmes-live.sh
set -eu

ARCHIVE="${1:-$HOME/holmes-deploy.tar.gz}"
WORKDIR="${HOME}/devops-selfheal"

echo "=== 1. Clean workdir ==="
chmod -R u+w "$WORKDIR" 2>/dev/null || true
rm -rf "$WORKDIR"
mkdir -p "$WORKDIR"

if [ -f "$ARCHIVE" ]; then
  echo "=== 2. Extract $ARCHIVE ==="
  tar -xzf "$ARCHIVE" -C "$WORKDIR"
else
  echo "WARN: $ARCHIVE not found — using existing $WORKDIR if present"
fi

cd "$WORKDIR"

if [ ! -f web/actions.py ] || [ ! -f deploy/oci/deploy-holmes-live.sh ]; then
  echo "ERROR: Missing web/actions.py or deploy script under $WORKDIR"
  echo "Upload holmes-deploy.tar.gz from Windows:"
  echo "  powershell -File deploy/oci/pack-holmes-overlay.ps1"
  exit 1
fi

find deploy/oci -name '*.sh' -exec sed -i 's/\r$//' {} + 2>/dev/null || true
exec bash deploy/oci/deploy-holmes-live.sh
