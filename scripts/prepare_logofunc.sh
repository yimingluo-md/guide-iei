#!/usr/bin/env bash
# Validate a downloaded LoGoFunc Zenodo bundle and move it into the configured
# managed annotation storage.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${ROOT}/scripts/lib.sh"

SOURCE_PATH="${1:?usage: scripts/prepare_logofunc.sh <source file-or-folder> [config.yaml]}"
CONFIG="${2:-${ROOT}/config/annotation.config.yaml}"
OUTPUT="$(yaml_get "$CONFIG" plugins.LoGoFunc.file)"
MANIFEST="$(yaml_get "$CONFIG" plugins.LoGoFunc.manifest)"
[[ "$OUTPUT" = /* ]] || OUTPUT="${ROOT}/${OUTPUT}"
[[ "$MANIFEST" = /* ]] || MANIFEST="${ROOT}/${MANIFEST}"

python3 "${ROOT}/pipeline/logofunc_dataset.py" install \
  --source "$SOURCE_PATH" --output "$OUTPUT" --manifest "$MANIFEST"
