#!/usr/bin/env bash
# Download the pinned official FuncVEP archive from Zenodo, then prepare it.
# The user must review and acknowledge the upstream terms before this script is
# invoked. The archive is served by Zenodo, retained locally, and never bundled
# with or redistributed by GUIDE-IEI.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
source "${HERE}/lib.sh"

CONFIG="${ROOT}/config/annotation.config.yaml"
CONFIG_SET=0
ACKNOWLEDGED=0
while [[ "$#" -gt 0 ]]; do
    case "$1" in
        --acknowledge-license) ACKNOWLEDGED=1 ;;
        --*) die "unknown FuncVEP download option: $1" ;;
        *)
            [[ "${CONFIG_SET}" == "0" ]] \
              || die "multiple annotation config paths were provided"
            CONFIG="$1"
            CONFIG_SET=1
            ;;
    esac
    shift
done
[[ "${ACKNOWLEDGED}" == "1" ]] || die \
  "FuncVEP terms must be reviewed and acknowledged before downloading"
[[ -f "${CONFIG}" ]] || die "annotation config not found: ${CONFIG}"
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

PIN_VALUES="$(python3 "${ROOT}/pipeline/funcvep_dataset.py" release-pin --format tsv)"
IFS=$'\t' read -r ARCHIVE_NAME ARCHIVE_URL ARCHIVE_MD5 ARCHIVE_SIZE <<<"${PIN_VALUES}"
[[ -n "${ARCHIVE_NAME}" && -n "${ARCHIVE_URL}" && -n "${ARCHIVE_MD5}" \
   && "${ARCHIVE_SIZE}" =~ ^[0-9]+$ ]] \
  || die "could not read the pinned FuncVEP release metadata"
SCORE_OUTPUT="$(configured_path plugins.FuncVEP.file)"
ARCHIVE="$(dirname "${SCORE_OUTPUT}")/${ARCHIVE_NAME}"
mkdir -p "$(dirname "${ARCHIVE}")"

printf '=== FuncVEP official archive from Zenodo (resumable, eight connections) ===\n'
python3 "${HERE}/parallel_fetch.py" "${ARCHIVE_URL}" "${ARCHIVE}" \
    --connections 8 \
    --chunk-mib 128 \
    --md5 "${ARCHIVE_MD5}" \
    --progress-start 0 \
    --progress-scale 40

printf '=== FuncVEP local validation and preparation ===\n'
# Map the preparer's native 0–100 progress into the remaining 40–100 range so
# the UI remains monotonic across the download and preparation phases.
bash "${HERE}/prepare_funcvep.sh" "${ARCHIVE}" "${CONFIG}" \
    --acknowledge-license \
    --source-kind downloaded_from_official_zenodo_record \
  | while IFS= read -r line; do
        if [[ "${line}" =~ ^([0-9]+([.][0-9]+)?)%(.*)$ ]]; then
            native="${BASH_REMATCH[1]}"
            suffix="${BASH_REMATCH[3]}"
            scaled="$(awk -v value="${native}" 'BEGIN { printf "%.1f", 40 + (0.6 * value) }')"
            printf '%s%%%s\n' "${scaled}" "${suffix}"
        else
            printf '%s\n' "${line}"
        fi
    done

log "FuncVEP source archive retained locally: ${ARCHIVE}"
