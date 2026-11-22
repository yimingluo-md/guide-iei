#!/usr/bin/env bash
# Build the VEP+LOFTEE image. Reads VEP tag / LOFTEE branch from the pipeline
# config if present, else uses defaults. Works with docker or podman.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${HERE}/.." && pwd)"
CONFIG="${1:-${REPO_ROOT}/config/annotation.config.yaml}"

# The two build settings are single-token scalars directly under container.
container_setting() {
    awk -v key="$1:" -v quote="'" '
        { sub(/\r$/, "") }
        /^[[:space:]]*(#|$)/ { next }
        /^container:[[:space:]]*(#.*)?$/ { active=1; indent=0; next }
        /^[^[:space:]]/ { active=0 }
        active {
            match($0, /^ +/)
            if (!indent) indent=RLENGTH
            if (RLENGTH != indent || $1 != key) next
            value=$2
            if (value ~ /^#/) exit
            first=substr(value, 1, 1)
            if ((first == quote || first == "\"") && substr(value, length(value)) == first)
                value=substr(value, 2, length(value)-2)
            print value
            exit
        }
    ' "${CONFIG}"
}

# Non-empty environment overrides take precedence over config and defaults.
if [[ -f "${CONFIG}" ]]; then
    VEP_TAG="${VEP_TAG:-$(container_setting vep_image_tag)}"
    LOFTEE_BRANCH="${LOFTEE_BRANCH:-$(container_setting loftee_branch)}"
fi

VEP_TAG="${VEP_TAG:-release_113.4}"
LOFTEE_BRANCH="${LOFTEE_BRANCH:-grch38}"
LOFTEE_COMMIT="${LOFTEE_COMMIT:-a46b502a68c812c8ae0c5a5721c0603fe81cae8d}"
BCFTOOLS_VERSION="${BCFTOOLS_VERSION:-1.20}"
BCFTOOLS_LIFTOVER_COMMIT="${BCFTOOLS_LIFTOVER_COMMIT:-909d23019e19aeadf3bf6fe1407fd6afc094592a}"
IMAGE_NAME="${IMAGE_NAME:-vep-annotate}"
IMAGE_TAG="${IMAGE_TAG:-latest}"

RUNTIME="${RUNTIME:-docker}"
command -v "${RUNTIME}" >/dev/null 2>&1 || { echo "ERROR: '${RUNTIME}' not found on PATH." >&2; exit 1; }
IMAGE_FINGERPRINT="$(bash "${HERE}/image_fingerprint.sh")"

echo "Building ${IMAGE_NAME}:${IMAGE_TAG}"
echo "  base VEP image : ensemblorg/ensembl-vep:${VEP_TAG}"
echo "  LOFTEE branch  : ${LOFTEE_BRANCH}"
echo "  bcftools       : ${BCFTOOLS_VERSION}"
echo "  liftover plugin: ${BCFTOOLS_LIFTOVER_COMMIT}"
echo "  runtime        : ${RUNTIME}"
echo "  source identity: ${IMAGE_FINGERPRINT}"

exec "${RUNTIME}" build \
    -t "${IMAGE_NAME}:${IMAGE_TAG}" \
    --label "org.guide-iei.source-fingerprint=${IMAGE_FINGERPRINT}" \
    --build-arg "VEP_TAG=${VEP_TAG}" \
    --build-arg "LOFTEE_BRANCH=${LOFTEE_BRANCH}" \
    --build-arg "LOFTEE_COMMIT=${LOFTEE_COMMIT}" \
    --build-arg "BCFTOOLS_VERSION=${BCFTOOLS_VERSION}" \
    --build-arg "BCFTOOLS_LIFTOVER_COMMIT=${BCFTOOLS_LIFTOVER_COMMIT}" \
    -f "${HERE}/Dockerfile" \
    "${HERE}"
