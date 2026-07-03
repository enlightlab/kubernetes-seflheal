#!/bin/bash
# Run from extracted tarball root:  bash apply-holmes.sh
# Or:  bash deploy/oci/cloud-shell-apply-holmes.sh
ROOT="$(cd "$(dirname "$0")" && pwd)"
exec bash "$ROOT/deploy/oci/cloud-shell-apply-holmes.sh" "$@"
