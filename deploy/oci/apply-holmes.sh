#!/bin/bash
# Deploy Holmes UI overlay (run from extracted repo root).
#   cd ~/devops-selfheal && bash deploy/oci/apply-holmes.sh
set -eu

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

find deploy/oci -name '*.sh' -exec sed -i 's/\r$//' {} + 2>/dev/null || true

bash deploy/oci/deploy-holmes-live.sh
if [ -f deploy/oci/apply-nginx-staging.sh ]; then
  bash deploy/oci/apply-nginx-staging.sh
fi
