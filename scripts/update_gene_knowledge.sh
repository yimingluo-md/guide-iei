#!/usr/bin/env bash
# Rebuild the public HGNC/IUIS/ClinGen gene-knowledge bundle. OMIM is excluded.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
STAGE="$(mktemp -d "${TMPDIR:-/tmp}/iei-gene-knowledge.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT
RELEASE_DATE="$(date -u +%F)"
OUTPUT="${1:-${ROOT}/webui/public/bundled-data/gene_knowledge_public.sqlite3}"
MANIFEST="${2:-${ROOT}/webui/public/bundled-data/gene_knowledge_manifest.json}"
mkdir -p "$(dirname "$OUTPUT")" "$(dirname "$MANIFEST")"

curl -fL --retry 4 --retry-delay 3 -o "${STAGE}/hgnc_complete_set.txt" \
  "https://storage.googleapis.com/public-download-files/hgnc/tsv/tsv/hgnc_complete_set.txt"
curl -fL --retry 4 --retry-delay 3 -o "${STAGE}/clingen_gene_validity.csv" \
  "https://search.clinicalgenome.org/kb/gene-validity/download"
curl -fL --retry 4 --retry-delay 3 -o "${STAGE}/clingen_dosage.tsv" \
  "https://ftp.clinicalgenome.org/ClinGen_gene_curation_list_GRCh38.tsv"

PYTHONPATH="${ROOT}" python3 "${HERE}/build_gene_knowledge.py" \
  --hgnc "${STAGE}/hgnc_complete_set.txt" \
  --iuis "${ROOT}/webui/public/bundled-data/iuis_2024_classification.tsv" \
  --clingen-validity "${STAGE}/clingen_gene_validity.csv" \
  --clingen-dosage "${STAGE}/clingen_dosage.tsv" \
  --output "$OUTPUT" \
  --manifest "$MANIFEST" \
  --release-date "$RELEASE_DATE"
