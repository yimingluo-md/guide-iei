#!/usr/bin/env bash
# Download and prepare categorical SCREEN Registry V4 tissue/immune data.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
source "${HERE}/lib.sh"

DATA_ROOT="${1:-}"
WORKERS="${2:-6}"
[[ -n "${DATA_ROOT}" ]] \
    || die "usage: scripts/prepare_screen_ccre_data.sh /path/to/SCREEN/Registry-V4 [workers]"
mkdir -p "${DATA_ROOT}/source" "${DATA_ROOT}/prepared"
DATA_ROOT="$(cd "${DATA_ROOT}" && pwd -P)"

CCRE_BED="${DATA_ROOT}/source/GRCh38-cCREs.bed"
EXPERIMENT_LISTS="${DATA_ROOT}/source/hg38-Experiment-Lists.tar.gz"
METADATA="${DATA_ROOT}/source/encode-biosample-metadata.json"
MANIFEST="${DATA_ROOT}/source/screen.registry-v4.selection.json"
CELL_ONTOLOGY="${DATA_ROOT}/source/cl-basic.v2026-06-08.obo"
CELL_ONTOLOGY_URL="https://github.com/obophenotype/cell-ontology/releases/download/v2026-06-08/cl-basic.obo"
CELL_ONTOLOGY_SHA256="73996c6349283e7a8cbd8183367a1cb810d0077e9c291ae0c72bd31e869f8c1c"

fetch "https://downloads.wenglab.org/Registry-V4/GRCh38-cCREs.bed" "${CCRE_BED}"
fetch \
    "https://users.moore-lab.org/ENCODE-cCREs/Pipeline-Input-Files/hg38-Experiment-Lists.tar.gz" \
    "${EXPERIMENT_LISTS}"
fetch "${CELL_ONTOLOGY_URL}" "${CELL_ONTOLOGY}"
if command -v sha256sum >/dev/null 2>&1; then
    ONTOLOGY_SHA256="$(sha256sum "${CELL_ONTOLOGY}" | awk '{print $1}')"
else
    ONTOLOGY_SHA256="$(shasum -a 256 "${CELL_ONTOLOGY}" | awk '{print $1}')"
fi
[[ "${ONTOLOGY_SHA256}" == "${CELL_ONTOLOGY_SHA256}" ]] \
    || die "pinned Cell Ontology checksum mismatch: ${CELL_ONTOLOGY}"

python3 "${ROOT}/pipeline/screen_ccre_dataset.py" build-manifest \
    --experiment-lists "${EXPERIMENT_LISTS}" \
    --ccre-bed "${CCRE_BED}" \
    --cell-ontology "${CELL_ONTOLOGY}" \
    --metadata-cache "${METADATA}" \
    --output "${MANIFEST}"

python3 "${ROOT}/pipeline/screen_ccre_dataset.py" download \
    --manifest "${MANIFEST}" \
    --source-dir "${DATA_ROOT}/source" \
    --workers "${WORKERS}"

python3 "${ROOT}/pipeline/screen_ccre_dataset.py" prepare \
    --manifest "${MANIFEST}" \
    --ccre-bed "${CCRE_BED}" \
    --source-dir "${DATA_ROOT}/source" \
    --output-dir "${DATA_ROOT}/prepared"

bash "${HERE}/prepare_screen_immune_contexts.sh" "${DATA_ROOT}" "${WORKERS}"

log "SCREEN categorical data and immune contexts prepared under ${DATA_ROOT}/prepared"
