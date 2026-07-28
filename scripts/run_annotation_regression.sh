#!/usr/bin/env bash
# Run the five-public-variant annotation regression panel through the real,
# locally installed VEP/reference stack.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${HERE}/lib.sh"
ROOT="$(cd "${HERE}/.." && pwd)"

CONFIG="${1:-${ROOT}/config/annotation.config.yaml}"
OUT_DIR="${2:-${ROOT}/test/out/regression}"
INPUT="${ROOT}/test/regression/annotation_regression.GRCh38.vcf"
EXPECTED="${ROOT}/test/regression/expected.yaml"
OUTPUT="${OUT_DIR}/annotation_regression.vep.vcf.gz"
FINAL="${OUT_DIR}/annotation_regression.vep.aamatch.vcf.gz"
REPORT="${OUT_DIR}/annotation_regression.report.json"

mkdir -p "$OUT_DIR"
log "running public annotation regression panel"
# --all-variants is required for the TERT promoter control. --no-clinvar avoids
# a network fetch; the current locally cached ClinVar release remains annotated.
bash "${HERE}/run_annotation.sh" \
    --input "$INPUT" --output "$OUTPUT" --config "$CONFIG" \
    --input-assembly GRCh38 --all-variants --no-clinvar

[[ -s "$FINAL" ]] || FINAL="$OUTPUT"
python3 "${ROOT}/pipeline/validate_regression_annotations.py" \
    --config "$CONFIG" --expected "$EXPECTED" --vcf "$FINAL" --json "$REPORT"
log "regression report -> $REPORT"
