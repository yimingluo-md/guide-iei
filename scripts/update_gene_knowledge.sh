#!/usr/bin/env bash
# Rebuild the public HGNC/IUIS/ClinGen/GenCC gene-knowledge bundle. OMIM is excluded.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
STAGE="$(mktemp -d "${TMPDIR:-/tmp}/iei-gene-knowledge.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT
RELEASE_DATE="$(date -u +%F)"
# Omitted destinations are resolved by the builder through the workbench storage registry.
output="${1:-}"
manifest="${2:-}"
set --
if [[ -n "$output" ]]; then set -- "$@" --output "$output"; fi
if [[ -n "$manifest" ]]; then set -- "$@" --manifest "$manifest"; fi

curl -fL --retry 4 --retry-delay 3 -o "${STAGE}/hgnc_complete_set.txt" \
  "https://storage.googleapis.com/public-download-files/hgnc/tsv/tsv/hgnc_complete_set.txt"
curl -fL --retry 4 --retry-delay 3 -o "${STAGE}/clingen_gene_validity.csv" \
  "https://search.clinicalgenome.org/kb/gene-validity/download"
curl -fL --retry 4 --retry-delay 3 -o "${STAGE}/clingen_dosage.tsv" \
  "https://ftp.clinicalgenome.org/ClinGen_gene_curation_list_GRCh38.tsv"

PYTHONPATH="${ROOT}" "${IEI_PYTHON_BIN:-python3}" "${HERE}/build_gene_knowledge.py" \
  --hgnc "${STAGE}/hgnc_complete_set.txt" \
  --iuis "${ROOT}/webui/public/bundled-data/iuis_2024_classification.tsv" \
  --clingen-validity "${STAGE}/clingen_gene_validity.csv" \
  --clingen-dosage "${STAGE}/clingen_dosage.tsv" \
  --fetch-gencc \
  --release-date "$RELEASE_DATE" "$@"
