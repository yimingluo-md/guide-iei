#!/usr/bin/env bash
# Enrich and curate donor-aware baseline immune contexts over SCREEN Registry V4.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
source "${HERE}/lib.sh"

DATA_ROOT="${1:-}"
WORKERS="${2:-8}"
[[ -n "${DATA_ROOT}" ]] \
    || die "usage: scripts/prepare_screen_immune_contexts.sh /path/to/SCREEN/Registry-V4 [workers]"
[[ "${WORKERS}" =~ ^[1-9][0-9]*$ ]] || die "workers must be a positive integer"

DATA_ROOT="$(cd "${DATA_ROOT}" && pwd -P)"
SELECTION="${DATA_ROOT}/source/screen.registry-v4.selection.json"
PREPARED="${DATA_ROOT}/prepared/screen.registry-v4.prepared.json"
EXPERIMENT_METADATA="${DATA_ROOT}/source/encode-experiment-curation.json"
POLICY="${ROOT}/config/screen/alphagenome_encode_audit_policy.json"
ALIGNMENT_FIXTURE="${ROOT}/config/screen/registry_v4_alignment_fixture.json"
CELL_ONTOLOGY="${DATA_ROOT}/source/cl-basic.v2026-06-08.obo"
CELL_ONTOLOGY_URL="https://github.com/obophenotype/cell-ontology/releases/download/v2026-06-08/cl-basic.obo"
CELL_ONTOLOGY_SHA256="73996c6349283e7a8cbd8183367a1cb810d0077e9c291ae0c72bd31e869f8c1c"

[[ -r "${SELECTION}" ]] || die "missing SCREEN selection manifest: ${SELECTION}"
[[ -r "${PREPARED}" ]] || die "missing prepared SCREEN manifest: ${PREPARED}"
[[ -r "${POLICY}" ]] || die "missing pinned ENCODE audit policy: ${POLICY}"
[[ -r "${ALIGNMENT_FIXTURE}" ]] || die "missing SCREEN alignment fixture: ${ALIGNMENT_FIXTURE}"

fetch "${CELL_ONTOLOGY_URL}" "${CELL_ONTOLOGY}"
if command -v sha256sum >/dev/null 2>&1; then
    ONTOLOGY_SHA256="$(sha256sum "${CELL_ONTOLOGY}" | awk '{print $1}')"
else
    ONTOLOGY_SHA256="$(shasum -a 256 "${CELL_ONTOLOGY}" | awk '{print $1}')"
fi
[[ "${ONTOLOGY_SHA256}" == "${CELL_ONTOLOGY_SHA256}" ]] \
    || die "pinned Cell Ontology checksum mismatch: ${CELL_ONTOLOGY}"

python3 "${ROOT}/pipeline/screen_ccre_dataset.py" validate-alignment \
    --fixture "${ALIGNMENT_FIXTURE}" \
    --selection "${SELECTION}" \
    --prepared-manifest "${PREPARED}"

python3 "${ROOT}/pipeline/screen_immune_curation.py" enrich \
    --selection "${SELECTION}" \
    --policy "${POLICY}" \
    --output "${EXPERIMENT_METADATA}" \
    --workers "${WORKERS}"

python3 "${ROOT}/pipeline/screen_immune_curation.py" prepare \
    --selection "${SELECTION}" \
    --prepared-manifest "${PREPARED}" \
    --experiment-metadata "${EXPERIMENT_METADATA}" \
    --policy "${POLICY}" \
    --cell-ontology "${CELL_ONTOLOGY}" \
    --output-dir "${DATA_ROOT}/prepared" \
    --force

log "SCREEN immune context layer prepared under ${DATA_ROOT}/prepared"
