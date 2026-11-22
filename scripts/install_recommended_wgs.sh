#!/usr/bin/env bash
# WGS additions only. Essential references and clinical snapshot updates are
# separate actions. The service supplies its validated list of missing resources.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${1:?annotation config required}"
SCREEN_ROOT="${2:?SCREEN destination required}"
ONLY="${3-spliceai,screen_context,alphagenome_avi,encode_re2g}"

# Reject invalid input before starting any transfers. An empty list is a no-op.
IFS=',' read -r -a resources <<< "$ONLY"
for resource in ${resources[@]+"${resources[@]}"}; do
  case "$resource" in
    spliceai|screen_context|alphagenome_avi|encode_re2g) ;;
    *) echo "ERROR: unknown WGS dataset: $resource" >&2; exit 2 ;;
  esac
done
for resource in ${resources[@]+"${resources[@]}"}; do
  case "$resource" in
    spliceai)
      echo "=== Full SpliceAI MANE 1.5 ==="
      bash "$HERE/download_references.sh" "$CONFIG" --only spliceai --skip-final-status ;;
    encode_re2g)
      "${IEI_PYTHON_BIN:-python3}" "$HERE/install_encode_re2g.py" "$(dirname "$SCREEN_ROOT")" ;;
    screen_context)
      echo "=== ENCODE SCREEN tissue and immune contexts ==="
      bash "$HERE/download_screen_context_bundle.sh" "$SCREEN_ROOT" ;;
    alphagenome_avi)
      echo "=== AlphaGenome AVI ==="
      bash "$HERE/download_avi.sh" "$CONFIG" ;;
  esac
done
echo "Recommended WGS datasets are ready. Essential references and clinical snapshots are unchanged."
