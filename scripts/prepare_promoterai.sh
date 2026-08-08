#!/usr/bin/env bash
# Prepare the two licensed files supplied by Illumina for the PromoterAI plugin.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
source "${HERE}/lib.sh"

SOURCE_DIR="${1:-}"
CONFIG="${2:-${ROOT}/config/annotation.config.yaml}"
CONSUME_SOURCE="${3:-}"
[[ -n "${SOURCE_DIR}" ]] || die "usage: scripts/prepare_promoterai.sh /path/to/PromoterAI [config.yaml]"
[[ -z "${CONSUME_SOURCE}" || "${CONSUME_SOURCE}" == "--remove-source-after-success" ]] \
    || die "unknown preparation option: ${CONSUME_SOURCE}"
SOURCE_DIR="$(cd "${SOURCE_DIR}" && pwd -P)"
CONFIG="$(cd "$(dirname "${CONFIG}")" && pwd -P)/$(basename "${CONFIG}")"

# hts() falls back to the container when host bgzip/tabix/samtools are absent;
# honour the configured runtime/image instead of the docker/vep-annotate:latest
# defaults (a podman-only host previously failed here despite correct config).
RUNTIME="$(yaml_get "$CONFIG" container.runtime)"; RUNTIME="${RUNTIME:-docker}"
IMAGE="$(yaml_get "$CONFIG" container.image)";     IMAGE="${IMAGE:-vep-annotate:latest}"
export RUNTIME IMAGE

TSS_SOURCE="${SOURCE_DIR}/tss.tsv"
SCORE_SOURCE="${SOURCE_DIR}/promoterAI_tss500.tsv.gz"
[[ -s "${TSS_SOURCE}" ]] || die "missing Illumina transcript table: ${TSS_SOURCE}"
[[ -s "${SCORE_SOURCE}" ]] || die "missing Illumina score table: ${SCORE_SOURCE}"

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

SCORE_OUTPUT="$(configured_path plugins.PromoterAI.file)"
MAP_OUTPUT="$(configured_path plugins.PromoterAI.transcript_map)"
MANIFEST_OUTPUT="$(configured_path plugins.PromoterAI.manifest)"
OUTPUT_DIR="$(dirname "${SCORE_OUTPUT}")"
mkdir -p "${OUTPUT_DIR}" "$(dirname "${MAP_OUTPUT}")" "$(dirname "${MANIFEST_OUTPUT}")"
WORK_DIR="$(mktemp -d "${OUTPUT_DIR}/.promoterai-prep.XXXXXX")"
cleanup() { rm -rf "${WORK_DIR}"; }
trap cleanup EXIT

PLAIN_SCORES="${WORK_DIR}/promoterai_scores.tsv"
TEMP_MAP="${WORK_DIR}/promoterai_transcripts.tsv"
TEMP_STATS="${WORK_DIR}/promoterai_stats.json"
TEMP_BGZF="${WORK_DIR}/promoterai_scores.tsv.gz"
TEMP_MANIFEST="${WORK_DIR}/promoterai.manifest.json"

python3 "${ROOT}/pipeline/prepare_promoterai.py" \
    --tss "${TSS_SOURCE}" \
    --scores "${SCORE_SOURCE}" \
    --output "${PLAIN_SCORES}" \
    --transcript-map "${TEMP_MAP}" \
    --stats "${TEMP_STATS}" \
    --temporary-directory "${WORK_DIR}"

log "BGZF-compressing compact PromoterAI table"
hts bgzip -@ 2 -c "${PLAIN_SCORES}" > "${TEMP_BGZF}"
hts tabix -f -s 1 -b 2 -e 2 "${TEMP_BGZF}"

python3 "${ROOT}/pipeline/finalize_promoterai_manifest.py" \
    --stats "${TEMP_STATS}" \
    --scores "${TEMP_BGZF}" \
    --index "${TEMP_BGZF}.tbi" \
    --transcript-map "${TEMP_MAP}" \
    --output "${TEMP_MANIFEST}"

# Publish only after validation, compression, indexing, and manifest checksums
# have all succeeded. Each rename stays on the destination filesystem.
mv "${TEMP_BGZF}" "${SCORE_OUTPUT}"
mv "${TEMP_BGZF}.tbi" "${SCORE_OUTPUT}.tbi"
mv "${TEMP_MAP}" "${MAP_OUTPUT}"
mv "${TEMP_MANIFEST}" "${MANIFEST_OUTPUT}"

if [[ "${CONSUME_SOURCE}" == "--remove-source-after-success" ]]; then
    [[ -s "${SCORE_OUTPUT}" && -s "${SCORE_OUTPUT}.tbi" && -s "${MAP_OUTPUT}" && -s "${MANIFEST_OUTPUT}" ]] \
        || die "refusing to remove PromoterAI source before every managed output is present"
    rm -f -- "${TSS_SOURCE}" "${SCORE_SOURCE}"
    log "removed the two downloaded PromoterAI source files after successful managed installation"
fi

printf '100.0%% PromoterAI preparation complete\n'
log "scores: ${SCORE_OUTPUT}"
log "map: ${MAP_OUTPUT}"
log "manifest: ${MANIFEST_OUTPUT}"
