#!/usr/bin/env bash
# Refresh public annotation sources whose releases change regularly. Pinned
# resources (VEP/cache, dbNSFP, CADD, SpliceAI and SCREEN) are intentionally not
# changed outside a validated software-bundle release.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
CONFIG="${1:-${ROOT}/config/annotation.config.yaml}"

echo "Updating refreshable public annotation datasets"
clinvar=failed
erepo="skipped (requires a successful ClinVar refresh)"
knowledge=failed
if bash "${HERE}/fetch_clinvar.sh" "${CONFIG}"; then
    clinvar=updated
    erepo=failed
    if bash "${HERE}/update_clingen_erepo.sh" "${CONFIG}"; then erepo=updated; fi
fi
echo "=== Updating GenCC and public gene knowledge ==="
if bash "${HERE}/update_gene_knowledge.sh" "${2:-}" "${3:-}"; then knowledge=updated; fi

echo "ClinVar: ${clinvar}. ClinGen ERepo: ${erepo}. Public gene knowledge (HGNC, ClinGen and GenCC): ${knowledge}."
if [[ "$clinvar" == updated && "$erepo" == updated && "$knowledge" == updated ]]; then exit 0; fi
# Distinguish a partial update from a run that published nothing.
if [[ "$clinvar" == updated || "$knowledge" == updated ]]; then exit 20; fi
exit 1
