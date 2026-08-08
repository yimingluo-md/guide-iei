#!/usr/bin/env bash
# Refresh public annotation sources whose releases change regularly. Pinned
# resources (VEP/cache, dbNSFP, CADD, SpliceAI and SCREEN) are intentionally not
# changed outside a validated software-bundle release.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
CONFIG="${1:-${ROOT}/config/annotation.config.yaml}"

echo "Updating refreshable public annotation datasets"
bash "${HERE}/fetch_clinvar.sh" "${CONFIG}"
bash "${HERE}/update_clingen_erepo.sh" "${CONFIG}"
echo "ClinVar and ClinGen variant-curation updates are ready."

