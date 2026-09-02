#!/usr/bin/env bash
# Validate and locally index an official FuncVEP v2 ZIP.
# The source ZIP is intentionally never moved, deleted, or redistributed.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
source "${HERE}/lib.sh"

SOURCE_ZIP="${1:-}"
[[ -n "${SOURCE_ZIP}" ]] || die \
  "usage: scripts/prepare_funcvep.sh /path/to/FuncVEP_and_ClinVEP_scores_all_possible_missense_variants.zip [config.yaml] --acknowledge-license"
shift || true

CONFIG="${ROOT}/config/annotation.config.yaml"
ACKNOWLEDGED=0
SOURCE_KIND="user_supplied_official_archive"
while [[ "$#" -gt 0 ]]; do
    case "$1" in
        --acknowledge-license) ACKNOWLEDGED=1 ;;
        --source-kind)
            shift
            [[ "$#" -gt 0 ]] || die "--source-kind requires a value"
            SOURCE_KIND="$1"
            ;;
        -*) die "unknown FuncVEP preparation option: $1" ;;
        *)
            [[ "${CONFIG}" == "${ROOT}/config/annotation.config.yaml" ]] \
                || die "more than one annotation config was provided"
            CONFIG="$1"
            ;;
    esac
    shift
done
[[ "${ACKNOWLEDGED}" == "1" ]] || die \
  "FuncVEP terms must be reviewed and acknowledged before local preparation"
[[ -f "${SOURCE_ZIP}" ]] || die "FuncVEP ZIP not found: ${SOURCE_ZIP}"
[[ -f "${CONFIG}" ]] || die "annotation config not found: ${CONFIG}"

SOURCE_ZIP="$(cd "$(dirname "${SOURCE_ZIP}")" && pwd -P)/$(basename "${SOURCE_ZIP}")"
CONFIG="$(cd "$(dirname "${CONFIG}")" && pwd -P)/$(basename "${CONFIG}")"

configured_path() {
    local key="$1" value
    value="$(yaml_get "${CONFIG}" "${key}")"
    [[ -n "${value}" ]] || die "missing config value: ${key}"
    if [[ "${value}" = /* ]]; then
        printf '%s\n' "${value}"
    else
        printf '%s\n' "${ROOT}/${value}"
    fi
}

ASSEMBLY="$(yaml_get "${CONFIG}" reference.assembly)"
[[ "${ASSEMBLY}" == "GRCh38" ]] || die "FuncVEP v2 scores require a GRCh38 annotation config"
SCORE_OUTPUT="$(configured_path plugins.FuncVEP.file)"
MANIFEST_OUTPUT="$(configured_path plugins.FuncVEP.manifest)"
mkdir -p "$(dirname "${SCORE_OUTPUT}")" "$(dirname "${MANIFEST_OUTPUT}")"

# Keep all large temporaries beside the managed score file. This guarantees
# same-filesystem atomic renames and makes the 20 GB free-space guard relevant
# to the actual installation destination.
WORK_DIR="$(mktemp -d "$(dirname "${SCORE_OUTPUT}")/.funcvep-prep.XXXXXX")"
cleanup() { rm -rf -- "${WORK_DIR}"; }
trap cleanup EXIT

PLAIN_SCORES="${WORK_DIR}/funcvep_scores.grch38.tsv"
STATS="${WORK_DIR}/funcvep.stats.json"
STAGED_BGZF="${WORK_DIR}/funcvep_scores.grch38.tsv.gz"
STAGED_MANIFEST="${WORK_DIR}/funcvep.manifest.json"

python3 "${ROOT}/pipeline/funcvep_dataset.py" prepare \
    --archive "${SOURCE_ZIP}" \
    --output "${PLAIN_SCORES}" \
    --stats "${STATS}" \
    --work-directory "${WORK_DIR}"

printf '90.0%% BGZF-compressing prepared FuncVEP scores\n'
RUNTIME="$(yaml_get "${CONFIG}" container.runtime)"; RUNTIME="${RUNTIME:-docker}"
IMAGE="$(yaml_get "${CONFIG}" container.image)"; IMAGE="${IMAGE:-vep-annotate:latest}"
export RUNTIME IMAGE
hts bgzip -@ 2 -f "${PLAIN_SCORES}"
# Compression has succeeded; release the full plain table before indexing and
# manifest generation so peak managed-disk use stays below the 20 GB guard.
rm -f -- "${PLAIN_SCORES}"
printf '96.0%% building FuncVEP coordinate index\n'
hts tabix -f -s 1 -b 2 -e 2 "${STAGED_BGZF}"

python3 "${ROOT}/pipeline/funcvep_dataset.py" finalize \
    --archive "${SOURCE_ZIP}" \
    --scores "${STAGED_BGZF}" \
    --index "${STAGED_BGZF}.tbi" \
    --stats "${STATS}" \
    --output "${STAGED_MANIFEST}" \
    --installed-score-name "$(basename "${SCORE_OUTPUT}")" \
    --license-acknowledged \
    --source-kind "${SOURCE_KIND}"

# The manifest is renamed last and serves as the committed installation marker.
# If any rename fails, the publisher restores the complete prior bundle.
python3 "${ROOT}/pipeline/funcvep_dataset.py" publish \
    --scores "${STAGED_BGZF}" \
    --index "${STAGED_BGZF}.tbi" \
    --manifest "${STAGED_MANIFEST}" \
    --target-scores "${SCORE_OUTPUT}" \
    --target-manifest "${MANIFEST_OUTPUT}"

log "FuncVEP scores: ${SCORE_OUTPUT}"
log "FuncVEP manifest: ${MANIFEST_OUTPUT}"
log "original licensed ZIP preserved: ${SOURCE_ZIP}"
