#!/usr/bin/env bash
# Upload a .scip index to a self-hosted Sourcegraph instance.
#
# Requires the `src` CLI:
#   brew install sourcegraph/src-cli/src-cli
#
# Env:
#   SRC_ENDPOINT  default http://localhost:7080
#   SRC_ACCESS_TOKEN  required (Site admin → Access tokens)
#
# Usage:
#   scripts/upload-scip.sh adsw                     # uses scip-indexer/adsw.scip
#   scripts/upload-scip.sh adsw path/to/index.scip  # explicit file

set -euo pipefail

PROJECT="${1:-}"
if [[ -z "$PROJECT" ]]; then
  echo "usage: $0 <project> [<scip-file>]" >&2
  exit 2
fi

SCIP_FILE="${2:-scip-indexer/${PROJECT}.scip}"
if [[ ! -f "$SCIP_FILE" ]]; then
  echo "scip file missing: $SCIP_FILE" >&2
  exit 2
fi

: "${SRC_ENDPOINT:=http://localhost:7080}"
: "${SRC_ACCESS_TOKEN:?SRC_ACCESS_TOKEN must be set}"

export SRC_ENDPOINT SRC_ACCESS_TOKEN
exec src code-intel upload -file "$SCIP_FILE" -no-progress
