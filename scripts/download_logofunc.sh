#!/usr/bin/env bash
# Resumably download and validate the pinned LoGoFunc Zenodo bundle.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${ROOT}/scripts/lib.sh"

CONFIG="${1:-${ROOT}/config/annotation.config.yaml}"
OUTPUT="$(yaml_get "$CONFIG" plugins.LoGoFunc.file)"
MANIFEST="$(yaml_get "$CONFIG" plugins.LoGoFunc.manifest)"
[[ "$OUTPUT" = /* ]] || OUTPUT="${ROOT}/${OUTPUT}"
[[ "$MANIFEST" = /* ]] || MANIFEST="${ROOT}/${MANIFEST}"

python3 "${ROOT}/pipeline/logofunc_dataset.py" download \
  --output "$OUTPUT" --manifest "$MANIFEST"
