#!/usr/bin/env bash
# Download official gene-level resources and rebuild the compact UI bundle.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
STAGE="$(mktemp -d "${TMPDIR:-/tmp}/iei-workbench-references.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT

GNOMAD_URL="https://storage.googleapis.com/gcp-public-data--gnomad/release/4.1.1/constraint/gnomad.v4.1.1.constraint_metrics.tsv.bgz"
IUIS_URL="https://wp-iuis.s3.eu-west-1.amazonaws.com/app/uploads/2024/10/30094653/IUIS-IEI-list-for-web-site-July-2024V2.xlsx"

echo "Downloading gnomAD v4.1.1 gene constraint metrics..."
curl -fL --retry 4 --retry-delay 3 \
    -o "${STAGE}/gnomad.v4.1.1.constraint_metrics.tsv.bgz" "$GNOMAD_URL"

echo "Downloading IUIS October 2024 IEI classification..."
curl -fL --retry 4 --retry-delay 3 \
    -o "${STAGE}/IUIS-IEI-list-for-web-site-July-2024V2.xlsx" "$IUIS_URL"

python3 "${HERE}/build_workbench_reference_data.py" \
    --gnomad "${STAGE}/gnomad.v4.1.1.constraint_metrics.tsv.bgz" \
    --iuis "${STAGE}/IUIS-IEI-list-for-web-site-July-2024V2.xlsx" \
    --output "${ROOT}/webui/public/bundled-data"

# Build the richer public gene-knowledge layer after the reviewed IUIS table
# has been regenerated. OMIM is intentionally not part of this update.
bash "${HERE}/update_gene_knowledge.sh"
