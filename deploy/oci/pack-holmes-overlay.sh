#!/bin/bash
# Pack web/ + deploy/ for OKE overlay deploy (Linux / Cloud Shell).
# Usage from repo root:
#   bash deploy/oci/pack-holmes-overlay.sh
# Then apply (same machine or after upload):
#   bash deploy/oci/cloud-shell-apply-holmes.sh
set -eu

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="${HOME}/holmes-deploy.tar.gz"

if [ ! -f "$ROOT/web/actions.py" ]; then
  echo "ERROR: Run from devops-selfheal repo (missing $ROOT/web/actions.py)"
  exit 1
fi

rm -f "$OUT"
tar -czf "$OUT" -C "$ROOT" web deploy
echo "Created: $OUT"
echo "Apply with:"
echo "  bash deploy/oci/cloud-shell-apply-holmes.sh"
